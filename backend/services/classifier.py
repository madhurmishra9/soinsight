"""
Classification engine — Ollama llama3.1:8b.

_RETRY_WAIT_MIN / _RETRY_WAIT_MAX are module-level so tests can monkeypatch them to 0.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import structlog
from sqlalchemy import Engine
from sqlmodel import Session, select
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.models import Classification
from app.settings import settings
from app.taxonomy import TAXONOMY, is_valid

log = structlog.get_logger("soinsight.classifier")

_LLM_MODEL = settings.ollama_model
_BATCH_SIZE = 20
_RETRY_ATTEMPTS = 4
_RETRY_WAIT_MIN = 1   # monkeypatch to 0 in tests
_RETRY_WAIT_MAX = 30

_NOISE_MAIN = "Misuse / Noise"
_NOISE_SUB_INVALID = "Incomplete or low-quality questions"
_NOISE_SUB_DUPLICATE = "Duplicate questions"

_FALLBACK: tuple[str, str, float, str] = (
    _NOISE_MAIN,
    _NOISE_SUB_INVALID,
    0.0,
    "Fallback: classification failed after retry.",
)


# ─── Retry predicate ─────────────────────────────────────────────────────────

def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


# ─── Taxonomy text (built once at import) ────────────────────────────────────

def _build_taxonomy_text() -> str:
    lines: list[str] = []
    for main, subs in TAXONOMY.items():
        lines.append(f'  "{main}":')
        for sub in subs:
            lines.append(f'    - "{sub}"')
    return "\n".join(lines)


_TAXONOMY_TEXT = _build_taxonomy_text()


# ─── Few-shot examples (2 per main category, 16 total) ───────────────────────

_FEW_SHOT: list[dict[str, Any]] = [
    {
        "title": "Can we add bulk export to the data pipeline?",
        "body": "We need to export thousands of records but the tool only exports one at a time.",
        "main": "Product", "sub": "Feature Gap", "confidence": 0.95,
        "reason": "User explicitly requests a missing capability.",
    },
    {
        "title": "5 nested API calls just to get a deployment status",
        "body": "Retrieving a deployment status requires 5 separate endpoint calls.",
        "main": "Product", "sub": "User / Developer Experience Gap", "confidence": 0.90,
        "reason": "Developer-experience friction, not a missing feature.",
    },
    {
        "title": "Where is the OAuth setup guide?",
        "body": "I cannot find documentation on configuring OAuth for the internal platform.",
        "main": "Documentation", "sub": "Missing Documentation", "confidence": 0.95,
        "reason": "User cannot find docs that should exist.",
    },
    {
        "title": "Setup guide says v2 but API reference shows v3",
        "body": "Setup guide says v2; the API reference shows v3 syntax. Which is right?",
        "main": "Documentation", "sub": "Conflicting Information", "confidence": 0.92,
        "reason": "Two official sources contradict each other.",
    },
    {
        "title": "Permission denied on every production deploy",
        "body": "I get 'permission denied' on every deploy to prod. Dev environment works fine.",
        "main": "Operational", "sub": "Setup or deployment issues", "confidence": 0.93,
        "reason": "Deployment failure in a specific environment.",
    },
    {
        "title": "How to configure TLS certificates in the cluster?",
        "body": "TLS cert setup for our internal cluster is undocumented and very complex.",
        "main": "Operational", "sub": "Configuration Complexity", "confidence": 0.88,
        "reason": "Configuration is the primary pain point.",
    },
    {
        "title": "Didn't know the batch import feature existed",
        "body": "A colleague showed me batch import yesterday — why isn't this more visible?",
        "main": "Awareness", "sub": "Feature not known", "confidence": 0.94,
        "reason": "Existing feature not discovered by users.",
    },
    {
        "title": "Assumed the legacy v1 API was deprecated",
        "body": "We stopped using v1 thinking it was deprecated, but it's still fully supported.",
        "main": "Awareness", "sub": "Incorrect assumptions about capability", "confidence": 0.90,
        "reason": "User had a wrong assumption about product state.",
    },
    {
        "title": "Worker service OOM-crashes every few hours",
        "body": "The service crashes with OOM every 3-4 hours. We must restart it manually.",
        "main": "Technical", "sub": "Reliability issues or instability", "confidence": 0.97,
        "reason": "Repeated crashes indicate instability.",
    },
    {
        "title": "Simple queries take 30 seconds",
        "body": "A basic SELECT on 10k rows takes 30 s. This blocks our workflows.",
        "main": "Technical", "sub": "Performance or scaling issues", "confidence": 0.95,
        "reason": "Performance is the core complaint.",
    },
    {
        "title": "Other teams can see our private repo",
        "body": "We set the repo to private but another team's members can still browse it.",
        "main": "Security / Compliance", "sub": "Access control or permissions confusion",
        "confidence": 0.94, "reason": "Access control not working as expected.",
    },
    {
        "title": "Does the platform encrypt data at rest?",
        "body": "For GDPR we need to confirm if stored data is encrypted at rest.",
        "main": "Security / Compliance", "sub": "Data protection or encryption questions",
        "confidence": 0.92, "reason": "Compliance-driven encryption question.",
    },
    {
        "title": "Migrating from v1 API to v2 without downtime",
        "body": "We're on v1 and need to migrate to v2. Looking for a migration guide.",
        "main": "Adoption / Migration", "sub": "Migration challenges between platforms/products",
        "confidence": 0.95, "reason": "Explicit migration request between API versions.",
    },
    {
        "title": "v4 upgrade broke our CI pipeline",
        "body": "After upgrading to v4.0 the lint step in CI fails with a new error.",
        "main": "Adoption / Migration", "sub": "Breaking changes or upgrades",
        "confidence": 0.93, "reason": "Upgrade caused a breaking change.",
    },
    {
        "title": "test",
        "body": "test",
        "main": "Misuse / Noise", "sub": "Incomplete or low-quality questions",
        "confidence": 0.99, "reason": "No meaningful content.",
    },
    {
        "title": "Same as question #456",
        "body": "This is a duplicate of the OAuth question. Please refer to that one.",
        "main": "Misuse / Noise", "sub": "Duplicate questions",
        "confidence": 0.98, "reason": "Explicit duplicate reference.",
    },
]


def _few_shot_block() -> str:
    q_lines = "\n".join(
        f'[{i + 1}] Title: {ex["title"]}\n     Body: {ex["body"]}'
        for i, ex in enumerate(_FEW_SHOT)
    )
    out_list = [
        {
            "index": i + 1,
            "main": ex["main"],
            "sub": ex["sub"],
            "confidence": ex["confidence"],
            "reason": ex["reason"],
        }
        for i, ex in enumerate(_FEW_SHOT)
    ]
    return f"EXAMPLE QUESTIONS:\n{q_lines}\n\nEXPECTED OUTPUT:\n{json.dumps(out_list, indent=2)}"


_FEW_SHOT_BLOCK = _few_shot_block()


# ─── Prompt builders ─────────────────────────────────────────────────────────

def _build_batch_prompt(questions: list[dict[str, str]], strict: bool = False) -> str:
    n = len(questions)
    strict_note = (
        '\nCRITICAL: Use ONLY the exact strings from the TAXONOMY. '
        'When uncertain, classify as "Misuse / Noise" / "Incomplete or low-quality questions".\n'
        if strict
        else ""
    )
    q_block = "\n".join(
        f'[{i + 1}] Title: {q["title"]}\n     Body: {q["body"][:300]}'
        for i, q in enumerate(questions)
    )
    return (
        "You are a technical classifier for an internal developer Q&A platform.\n"
        f"{strict_note}"
        "Classify each question using ONLY the TAXONOMY below. Output valid JSON.\n\n"
        f"TAXONOMY:\n{_TAXONOMY_TEXT}\n\n"
        f"{_FEW_SHOT_BLOCK}\n\n"
        f"Now classify the following {n} question(s).\n"
        f"Output a JSON array of exactly {n} objects, one per question, in order.\n"
        'Each object must have: "main", "sub", "confidence" (0.0-1.0), "reason".\n\n'
        f"QUESTIONS:\n{q_block}\n\nJSON array:"
    )


def _build_single_prompt(title: str, body: str, strict: bool = False) -> str:
    strict_note = (
        '\nCRITICAL: Use ONLY the exact strings from TAXONOMY. '
        'When uncertain, use "Misuse / Noise" / "Incomplete or low-quality questions".\n'
        if strict
        else ""
    )
    return (
        "You are a technical classifier for an internal developer Q&A platform.\n"
        f"{strict_note}"
        "Classify the question using ONLY the TAXONOMY below.\n\n"
        f"TAXONOMY:\n{_TAXONOMY_TEXT}\n\n"
        "Output a single JSON object: "
        '{"main": "...", "sub": "...", "confidence": 0.0-1.0, "reason": "..."}\n\n'
        f"Title: {title}\nBody: {body[:300]}\n\nJSON object:"
    )


# ─── Result dataclass ─────────────────────────────────────────────────────────

def _parse_single(raw: Any) -> tuple[str, str, float, str] | None:
    """Validate one classification object. Returns None if enum values are invalid."""
    if not isinstance(raw, dict):
        return None
    main = str(raw.get("main", ""))
    sub = str(raw.get("sub", ""))
    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    reason = str(raw.get("reason", ""))
    if not is_valid(main, sub):
        return None
    return main, sub, confidence, reason


class ClassificationResult:
    __slots__ = ("question_id", "main_category", "sub_category", "confidence", "is_noise", "reason")

    def __init__(
        self,
        question_id: int,
        main_category: str,
        sub_category: str,
        confidence: float,
        is_noise: bool,
        reason: str = "",
    ) -> None:
        self.question_id = question_id
        self.main_category = main_category
        self.sub_category = sub_category
        self.confidence = confidence
        self.is_noise = is_noise
        self.reason = reason


# ─── Classifier service ───────────────────────────────────────────────────────

class ClassifierService:
    """
    Classifies questions using Ollama llama3.1:8b.

    Batches ≤ _BATCH_SIZE questions per LLM call.
    Invalid LLM output → retry once with a strict single-question prompt.
    Still invalid → forced Misuse/Noise, confidence 0.0, logged.
    Embedding-detected duplicates bypass the LLM and are marked as noise.
    """

    def __init__(
        self,
        ollama_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        chroma_store: Any | None = None,
        embedding_svc: Any | None = None,
    ) -> None:
        self._ollama_url = (ollama_url or settings.ollama_url).rstrip("/")
        self._transport = transport   # None → real network; MockTransport in tests
        self._chroma = chroma_store
        self._embed = embedding_svc

    # ── Ollama HTTP call ─────────────────────────────────────────────────────

    async def _call_ollama_raw(self, prompt: str) -> Any:
        """
        POST to /api/generate with format=json; returns parsed Python value
        (list for batch prompts, dict for single prompts).
        """
        result: Any = None
        async with httpx.AsyncClient(timeout=120.0, transport=self._transport) as http:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception(_is_retryable),
                stop=stop_after_attempt(_RETRY_ATTEMPTS),
                wait=wait_exponential(multiplier=1, min=_RETRY_WAIT_MIN, max=_RETRY_WAIT_MAX),
                reraise=True,
            ):
                with attempt:
                    r = await http.post(
                        f"{self._ollama_url}/api/generate",
                        json={
                            "model": settings.ollama_model,
                            "prompt": prompt,
                            "format": "json",
                            "stream": False,
                        },
                    )
                    r.raise_for_status()
                    outer = r.json()
                    result = json.loads(outer["response"])
        return result

    # ── Duplicate detection ──────────────────────────────────────────────────

    async def _is_duplicate(self, question: Any) -> bool:
        """Return True if ChromaDB finds a near-duplicate for this question."""
        if self._chroma is None or self._embed is None:
            return False
        try:
            vec: list[float] = await self._embed.embed_question(
                question.title, question.body or ""
            )
            dupes = self._chroma.find_duplicates(so_id=question.so_id, embedding=vec)
            return len(dupes) > 0
        except Exception as exc:
            log.warning("duplicate_check_failed", so_id=question.so_id, error=str(exc))
            return False

    # ── Single-question fallback ─────────────────────────────────────────────

    async def _classify_single_question(
        self, title: str, body: str, strict: bool = False
    ) -> tuple[str, str, float, str] | None:
        """Classify one question individually. Returns None on failure."""
        try:
            prompt = _build_single_prompt(title, body, strict=strict)
            raw = await self._call_ollama_raw(prompt)
            return _parse_single(raw)
        except Exception as exc:
            log.warning("classify_single_failed", error=str(exc))
            return None

    # ── Batch LLM call ───────────────────────────────────────────────────────

    async def _batch_llm_call(
        self, questions: list[Any]
    ) -> list[dict[str, Any] | None]:
        """
        Send one batch prompt for all questions; return one result per question.
        Any question whose result is None will trigger a per-item retry upstream.
        """
        q_dicts = [{"title": q.title, "body": q.body or ""} for q in questions]
        prompt = _build_batch_prompt(q_dicts)
        try:
            raw = await self._call_ollama_raw(prompt)
        except Exception as exc:
            log.warning("batch_llm_failed", error=str(exc), batch_size=len(questions))
            return [None] * len(questions)

        if isinstance(raw, list):
            # Pad or trim to match question count (LLM may over/under-produce)
            padded: list[dict[str, Any] | None] = list(raw) + [None] * len(questions)
            return padded[: len(questions)]

        if isinstance(raw, dict):
            # Model may have wrapped the array in an object — unwrap the first list value
            for v in raw.values():
                if isinstance(v, list):
                    padded = list(v) + [None] * len(questions)
                    return padded[: len(questions)]
            # Single-question batch: treat the object as the one result
            if len(questions) == 1:
                return [raw]

        return [None] * len(questions)

    # ── Batch classification (internal) ──────────────────────────────────────

    async def _classify_batch(self, questions: list[Any]) -> list[ClassificationResult]:
        """
        Classify one batch (≤ _BATCH_SIZE).

        1. Duplicate check: embedding-detected dupes are marked noise, bypassing LLM.
        2. Batch LLM call for the rest.
        3. Per-item: if LLM output is invalid, retry individually with a strict prompt.
        4. Still invalid → forced fallback to noise with confidence 0.0.
        """
        # Phase 1: duplicate detection
        is_dup = [await self._is_duplicate(q) for q in questions]

        # Phase 2: LLM for non-duplicates
        to_llm = [(orig_idx, q) for orig_idx, q in enumerate(questions) if not is_dup[orig_idx]]
        llm_results: list[ClassificationResult | None] = [None] * len(questions)

        if to_llm:
            llm_qs = [q for _, q in to_llm]
            raw_items = await self._batch_llm_call(llm_qs)

            for (orig_idx, q), raw in zip(to_llm, raw_items, strict=False):
                parsed = _parse_single(raw) if isinstance(raw, dict) else None

                if parsed is None:
                    # Retry individually with a stricter prompt
                    parsed = await self._classify_single_question(
                        q.title, q.body or "", strict=True
                    )

                if parsed is None:
                    log.warning(
                        "classify_fallback_noise",
                        so_id=q.so_id,
                        title=q.title[:60],   # abbreviated — never log full body
                    )
                    parsed = _FALLBACK

                main, sub, confidence, reason = parsed
                llm_results[orig_idx] = ClassificationResult(
                    question_id=q.id,
                    main_category=main,
                    sub_category=sub,
                    confidence=confidence,
                    is_noise=(main == _NOISE_MAIN),
                    reason=reason,
                )

        # Phase 3: merge in original order
        final: list[ClassificationResult] = []
        for i, q in enumerate(questions):
            if is_dup[i]:
                log.info("duplicate_classified_as_noise", so_id=q.so_id)
                final.append(
                    ClassificationResult(
                        question_id=q.id,
                        main_category=_NOISE_MAIN,
                        sub_category=_NOISE_SUB_DUPLICATE,
                        confidence=1.0,
                        is_noise=True,
                        reason="Near-duplicate detected by vector similarity.",
                    )
                )
            else:
                res = llm_results[i]
                if res is not None:
                    final.append(res)

        return final

    # ── Public API ───────────────────────────────────────────────────────────

    async def classify_questions(
        self,
        questions: list[Any],
        engine: Engine,
    ) -> list[ClassificationResult]:
        """
        Classify all questions, persisting to DB.

        Idempotent: questions that already have a classification row are skipped.
        Returns ClassificationResult for every question processed this run.
        """
        # Filter out already-classified questions
        to_classify: list[Any] = []
        with Session(engine) as session:
            for q in questions:
                if q.id is None:
                    continue
                existing = session.exec(
                    select(Classification).where(Classification.question_id == q.id)
                ).first()
                if existing is None:
                    to_classify.append(q)

        # Process in batches
        results: list[ClassificationResult] = []
        for batch_start in range(0, len(to_classify), _BATCH_SIZE):
            batch = to_classify[batch_start : batch_start + _BATCH_SIZE]
            results.extend(await self._classify_batch(batch))

        # Persist
        with Session(engine) as session:
            for res in results:
                session.add(
                    Classification(
                        question_id=res.question_id,
                        main_category=res.main_category,
                        sub_category=res.sub_category,
                        confidence=res.confidence,
                        is_noise=res.is_noise,
                        model=settings.ollama_model,
                    )
                )
            session.commit()

        log.info(
            "classify_done",
            total=len(results),
            noise=sum(1 for r in results if r.is_noise),
        )
        return results
