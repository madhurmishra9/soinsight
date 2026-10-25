"""
Classification engine — any Ollama model, tuned to keep 3-4B models precise.

What makes a small model both fast and accurate here:

1. **Constrained decoding.** The reply is constrained by a JSON schema whose
   only legal labels are the taxonomy's 29 "Main > Sub" strings, with exactly
   one key per question ("q1", "q2", ...). An invalid category, a mismatched
   main/sub pair or a dropped question cannot be generated, so the retry path
   that used to cost one extra model call per bad item is all but gone.
   Ollama builds without schema support get plain JSON mode automatically
   (see services/ollama.py) and the tolerant parser below takes over.

2. **A cached, static prefix.** Instructions, labelled definitions and examples
   live in one fixed system prompt that never varies between calls, including
   retries. Ollama reuses the KV cache for an identical prefix, so after the
   first call only the questions themselves are processed — on CPU, prompt
   processing is the dominant cost and this removes most of it.

3. **Less to read, less to write.** Bodies are HTML-stripped before truncation
   (300 chars of raw HTML was often half markup), examples are one line each
   instead of indented JSON, and the reply carries a label and a confidence —
   no free-text reason, which was never stored but cost the most output tokens.

4. **Definitions, not just names.** Each label carries a one-line definition.
   Small models pick between "Feature Gap" and "Demand Signal" by meaning, not
   by guessing from the name.

Failure semantics: if Ollama is unreachable or the model is not installed,
nothing is recorded for the affected questions — they stay unclassified and the
next run picks them up. Previously every such question was saved as Misuse /
Noise and, because classification is idempotent, stayed noise forever.

_RETRY_WAIT_MIN / _RETRY_WAIT_MAX are module-level so tests can monkeypatch them to 0.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import structlog
from sqlalchemy import Engine
from sqlmodel import Session, col, select
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.models import Classification
from app.settings import settings
from app.taxonomy import TAXONOMY, is_valid
from services import ollama

log = structlog.get_logger("soinsight.classifier")

_BATCH_SIZE = 10  # fallback only; settings.classify_batch_size wins at call time
_RETRY_ATTEMPTS = 3
_RETRY_WAIT_MIN = 1   # monkeypatch to 0 in tests
_RETRY_WAIT_MAX = 20

_NOISE_MAIN = "Misuse / Noise"
_NOISE_SUB_INVALID = "Incomplete or low-quality questions"
_NOISE_SUB_DUPLICATE = "Duplicate questions"

_FALLBACK: tuple[str, str, float, str] = (
    _NOISE_MAIN,
    _NOISE_SUB_INVALID,
    0.0,
    "Fallback: classification failed after retry.",
)

_LABEL_SEP = " > "

# Output-token budget: one entry is roughly
#   "q12":{"label":"Security / Compliance > Access control or permissions
#   confusion","confidence":0.9},
# ~30 tokens; 40 per question leaves headroom without letting a stuck model run on.
_TOKENS_PER_ITEM = 40
_TOKENS_OVERHEAD = 32
# Conservative chars-per-token for prompt-size estimates (English + code ≈ 3.5-4).
_CHARS_PER_TOKEN = 3.0
# A question this short has nothing to classify; skip the model entirely.
_MIN_WORDS = 3

ProgressCallback = Callable[[int, int], Awaitable[None]]


# ─── Retry predicate ─────────────────────────────────────────────────────────

def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        # A connect timeout means the server isn't there; retrying with
        # back-off just delays the "Ollama unreachable" report.
        return not isinstance(exc, httpx.ConnectTimeout)
    if isinstance(exc, httpx.HTTPStatusError):
        if ollama.is_capability_error(exc):
            return False  # handled by downgrading the request, not by waiting
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


# ─── Taxonomy: labels and definitions ────────────────────────────────────────

_DEFINITIONS: dict[tuple[str, str], str] = {
    ("Product", "Feature Gap"):
        "asks for a capability the product does not have",
    ("Product", "User / Developer Experience Gap"):
        "the capability exists but is awkward, slow or confusing to use",
    ("Product", "Integration Gap"):
        "the product does not connect to another tool or service they need",
    ("Product", "Demand Signal"):
        "asks whether something is offered, supported or planned; interest in using it",
    ("Documentation", "Missing Documentation"):
        "needed docs or examples do not exist or cannot be found",
    ("Documentation", "Unclear or poorly explained"):
        "docs exist but are confusing, vague or incomplete",
    ("Documentation", "Conflicting Information"):
        "two official sources disagree",
    ("Documentation", "Information spread across multiple sources"):
        "the answer exists but is scattered across pages, teams or tools",
    ("Operational", "Configuration Complexity"):
        "hard to configure correctly: settings, flags, variables, IaC inputs",
    ("Operational", "Setup or deployment issues"):
        "provisioning, installing, pipeline or deployment fails",
    ("Operational", "Environment constraints"):
        "blocked by limits of an environment: quotas, org policy, env differences",
    ("Operational", "Lack of troubleshooting support"):
        "stuck debugging with no logs, runbook or support path",
    ("Awareness", "Feature not known"):
        "did not know an existing feature exists",
    ("Awareness", "Incorrect assumptions about capability"):
        "wrongly believes the product can or cannot do something",
    ("Awareness", "Poor communication of changes or releases"):
        "surprised by a change, deprecation or release that was not announced well",
    ("Technical", "Reliability issues or instability"):
        "crashes, intermittent failures, outages in something that should work",
    ("Technical", "Performance or scaling issues"):
        "slow, high latency, low throughput, limits under load",
    ("Technical", "Poor error handling or failures"):
        "unclear or misleading errors, silent failures",
    ("Security / Compliance", "Access control or permissions confusion"):
        "IAM roles, permissions, who can access what",
    ("Security / Compliance", "Network or connectivity issues"):
        "cannot connect: firewall, VPC, private IP, DNS, TLS, proxy",
    ("Security / Compliance", "Data protection or encryption questions"):
        "encryption, keys, data handling and retention",
    ("Security / Compliance", "Compliance or regulatory gaps"):
        "audit, policy or regulatory requirements",
    ("Adoption / Migration", "Migration challenges between platforms/products"):
        "moving from one platform, product or major version to another",
    ("Adoption / Migration", "Breaking changes or upgrades"):
        "an upgrade or new version broke something",
    ("Adoption / Migration", "Difficulty getting started"):
        "first-time use, onboarding, where to begin",
    ("Adoption / Migration", "Compatibility issues"):
        "versions, tools or components that do not work together",
    ("Misuse / Noise", "Incorrect usage"):
        "using the product for something it is not meant for, or the wrong forum",
    ("Misuse / Noise", "Duplicate questions"):
        "explicitly a duplicate of another question",
    ("Misuse / Noise", "Incomplete or low-quality questions"):
        "no real question: placeholder text, or too vague to act on",
}

_LABELS: list[str] = [f"{m}{_LABEL_SEP}{s}" for m, subs in TAXONOMY.items() for s in subs]
_LABEL_TO_PAIR: dict[str, tuple[str, str]] = {
    f"{m}{_LABEL_SEP}{s}": (m, s) for m, subs in TAXONOMY.items() for s in subs
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


# Lenient lookups for unconstrained (plain JSON mode) replies. Every key maps to
# exactly one valid pair, so this recovers formatting slips, never guesses.
_NORM_LABEL: dict[str, tuple[str, str]] = {_norm(k): v for k, v in _LABEL_TO_PAIR.items()}
_NORM_MAIN: dict[str, str] = {_norm(m): m for m in TAXONOMY}
_NORM_SUB_PER_MAIN: dict[str, dict[str, str]] = {
    m: {_norm(s): s for s in subs} for m, subs in TAXONOMY.items()
}
_sub_owner: dict[str, list[tuple[str, str]]] = {}
for _m, _subs in TAXONOMY.items():
    for _s in _subs:
        _sub_owner.setdefault(_norm(_s), []).append((_m, _s))
_NORM_SUB_UNIQUE: dict[str, tuple[str, str]] = {
    k: v[0] for k, v in _sub_owner.items() if len(v) == 1
}


def _resolve_pair(main: str, sub: str) -> tuple[str, str] | None:
    """Map a (possibly sloppy) main/sub pair onto the taxonomy, or None."""
    if is_valid(main, sub):
        return main, sub
    n_main, n_sub = _norm(main), _norm(sub)
    canon_main = _NORM_MAIN.get(n_main)
    if canon_main is not None:
        canon_sub = _NORM_SUB_PER_MAIN[canon_main].get(n_sub)
        if canon_sub is not None:
            return canon_main, canon_sub
    # Main missing or wrong but the sub-category is unambiguous on its own.
    if n_sub in _NORM_SUB_UNIQUE and (not n_main or canon_main is None):
        return _NORM_SUB_UNIQUE[n_sub]
    return None


def _resolve_label(label: str) -> tuple[str, str] | None:
    if label in _LABEL_TO_PAIR:
        return _LABEL_TO_PAIR[label]
    hit = _NORM_LABEL.get(_norm(label))
    if hit is not None:
        return hit
    if ">" in label:
        main, _, sub = label.partition(">")
        return _resolve_pair(main.strip(), sub.strip())
    return _NORM_SUB_UNIQUE.get(_norm(label))


# ─── Few-shot examples (2 per main category, 16 total) ───────────────────────

_FEW_SHOT: list[dict[str, Any]] = [
    {"title": "Can we add bulk export to the data pipeline?",
     "body": "We need to export thousands of records but the tool only exports one at a time.",
     "main": "Product", "sub": "Feature Gap"},
    {"title": "5 nested API calls just to get a deployment status",
     "body": "Retrieving a deployment status requires 5 separate endpoint calls.",
     "main": "Product", "sub": "User / Developer Experience Gap"},
    {"title": "Where is the OAuth setup guide?",
     "body": "I cannot find documentation on configuring OAuth for the internal platform.",
     "main": "Documentation", "sub": "Missing Documentation"},
    {"title": "Setup guide says v2 but API reference shows v3",
     "body": "Setup guide says v2; the API reference shows v3 syntax. Which is right?",
     "main": "Documentation", "sub": "Conflicting Information"},
    {"title": "Permission denied on every production deploy",
     "body": "I get 'permission denied' on every deploy to prod. Dev environment works fine.",
     "main": "Operational", "sub": "Setup or deployment issues"},
    {"title": "How to configure TLS certificates in the cluster?",
     "body": "TLS cert setup for our internal cluster is undocumented and very complex.",
     "main": "Operational", "sub": "Configuration Complexity"},
    {"title": "Didn't know the batch import feature existed",
     "body": "A colleague showed me batch import yesterday — why isn't this more visible?",
     "main": "Awareness", "sub": "Feature not known"},
    {"title": "Assumed the legacy v1 API was deprecated",
     "body": "We stopped using v1 thinking it was deprecated, but it's still fully supported.",
     "main": "Awareness", "sub": "Incorrect assumptions about capability"},
    {"title": "Worker service OOM-crashes every few hours",
     "body": "The service crashes with OOM every 3-4 hours. We must restart it manually.",
     "main": "Technical", "sub": "Reliability issues or instability"},
    {"title": "Simple queries take 30 seconds",
     "body": "A basic SELECT on 10k rows takes 30 s. This blocks our workflows.",
     "main": "Technical", "sub": "Performance or scaling issues"},
    {"title": "Other teams can see our private repo",
     "body": "We set the repo to private but another team's members can still browse it.",
     "main": "Security / Compliance", "sub": "Access control or permissions confusion"},
    {"title": "Does the platform encrypt data at rest?",
     "body": "For GDPR we need to confirm if stored data is encrypted at rest.",
     "main": "Security / Compliance", "sub": "Data protection or encryption questions"},
    {"title": "Migrating from v1 API to v2 without downtime",
     "body": "We're on v1 and need to migrate to v2. Looking for a migration guide.",
     "main": "Adoption / Migration", "sub": "Migration challenges between platforms/products"},
    {"title": "v4 upgrade broke our CI pipeline",
     "body": "After upgrading to v4.0 the lint step in CI fails with a new error.",
     "main": "Adoption / Migration", "sub": "Breaking changes or upgrades"},
    {"title": "test", "body": "test",
     "main": "Misuse / Noise", "sub": "Incomplete or low-quality questions"},
    {"title": "Same as question #456",
     "body": "This is a duplicate of the OAuth question. Please refer to that one.",
     "main": "Misuse / Noise", "sub": "Duplicate questions"},
]


def _build_system_prompt() -> str:
    label_lines = "\n".join(
        f"- {m}{_LABEL_SEP}{s}: {_DEFINITIONS[(m, s)]}"
        for m, subs in TAXONOMY.items()
        for s in subs
    )
    example_lines = "\n".join(
        f'- "{ex["title"]}" {ex["body"]} => {ex["main"]}{_LABEL_SEP}{ex["sub"]}'
        for ex in _FEW_SHOT
    )
    return (
        "You classify questions from an internal developer Q&A site "
        "(Stack Overflow Enterprise) to find what should be fixed so the question "
        "is not asked again.\n\n"
        "Give each question exactly ONE label from this list. Copy the label text "
        "exactly, including the \" > \".\n\n"
        f"LABELS:\n{label_lines}\n\n"
        "HOW TO DECIDE:\n"
        "1. Label the root cause that would prevent the question, not the technology "
        "it mentions.\n"
        "2. \"How do I ...\" that good docs would answer: Documentation.\n"
        "3. Failure while provisioning or deploying: Operational. Failure of something "
        "already running: Technical.\n"
        "4. Misuse / Noise only when there is no real question. A short but real "
        "question is never noise.\n"
        "5. Confidence: 0.9 clear fit, 0.7 likely, 0.5 unsure.\n\n"
        f"EXAMPLES:\n{example_lines}\n\n"
        "OUTPUT: one JSON object with a key per question, \"q1\", \"q2\", ... in order. "
        "Each value is {\"label\": \"<label>\", \"confidence\": <number>}."
    )


_SYSTEM_PROMPT = _build_system_prompt()


def _response_schema(n: int) -> dict[str, Any]:
    """JSON schema constraining the reply to exactly n valid labels, in order."""
    item = {
        "type": "object",
        "properties": {
            "label": {"type": "string", "enum": _LABELS},
            "confidence": {"type": "number"},
        },
        "required": ["label", "confidence"],
        "additionalProperties": False,
    }
    keys = [f"q{i + 1}" for i in range(n)]
    return {
        "type": "object",
        "properties": {k: item for k in keys},
        "required": keys,
        "additionalProperties": False,
    }


# ─── Text cleaning ───────────────────────────────────────────────────────────

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _clean_text(text: str, limit: int | None = None) -> str:
    """HTML → compact plain text. Code is kept (error messages live there)."""
    if not text:
        return ""
    out = _TAG_RE.sub(" ", text) if "<" in text else text
    out = html.unescape(out)
    out = _WS_RE.sub(" ", out).strip()
    if limit is not None and len(out) > limit:
        cut = out[:limit]
        space = cut.rfind(" ")
        out = (cut[:space] if space > limit * 0.6 else cut).rstrip() + " …"
    return out


def _body_limit() -> int:
    return max(80, int(settings.classify_body_chars or 400))


def _is_too_short(title: str, body: str) -> bool:
    words = f"{_clean_text(title)} {_clean_text(body)}".split()
    return len(words) < _MIN_WORDS


# ─── Prompt builders ─────────────────────────────────────────────────────────

def _build_batch_prompt(questions: list[dict[str, str]], strict: bool = False) -> str:
    """The per-call (uncached) part of the prompt: just the questions."""
    n = len(questions)
    limit = _body_limit()
    blocks = []
    for i, q in enumerate(questions):
        title = _clean_text(q.get("title", ""), 300)
        body = _clean_text(q.get("body", ""), limit)
        blocks.append(f"q{i + 1}\nTitle: {title}\nBody: {body or '(empty)'}")
    keys = ", ".join(f'"q{i + 1}"' for i in range(n))
    strict_note = (
        "CRITICAL: copy each label exactly as written in LABELS; pick the closest "
        "label rather than inventing one.\n"
        if strict
        else ""
    )
    return (
        f"Classify these {n} question(s).\n\n"
        + "\n\n".join(blocks)
        + f"\n\n{strict_note}Reply with a JSON object with keys {keys}."
    )


def _build_single_prompt(title: str, body: str, strict: bool = False) -> str:
    return _build_batch_prompt([{"title": title, "body": body}], strict=strict)


def _estimate_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN) + 1


_SYSTEM_TOKENS = _estimate_tokens(_SYSTEM_PROMPT)


# ─── Response parsing ────────────────────────────────────────────────────────

def _parse_single(raw: Any) -> tuple[str, str, float, str] | None:
    """Validate one classification object. Returns None if it maps to no label.

    Accepts the constrained shape ({"label": "Main > Sub"}), the legacy shape
    ({"main", "sub"}), and a bare sub-category when that is unambiguous.
    """
    if not isinstance(raw, dict):
        return None
    pair: tuple[str, str] | None = None
    label = raw.get("label") or raw.get("category")
    if isinstance(label, str) and label.strip():
        pair = _resolve_label(label.strip())
    if pair is None and ("main" in raw or "sub" in raw):
        pair = _resolve_pair(str(raw.get("main", "")), str(raw.get("sub", "")))
    if pair is None:
        return None
    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    if 1.0 < confidence <= 100.0:
        confidence /= 100.0   # "90" meant 90%
    confidence = max(0.0, min(1.0, confidence))
    reason = str(raw.get("reason", ""))
    return pair[0], pair[1], confidence, reason


def _align_batch(items: list[Any], expected: int) -> list[dict[str, Any] | None]:
    """Map a list-shaped batch response back onto its questions.

    Only reached for replies that are not keyed by question ("q1", ...) — i.e.
    from Ollama builds without schema support, or models ignoring the format.
    Results are consumed positionally by the caller, so a model that drops or
    reorders one entry would otherwise shift every later result onto the wrong
    question — silently mislabelling them rather than failing. Three cases:

    1. Items carry a valid, unique 1-based "index": map by it. Slots with no
       item are left None.
    2. No usable indices, but exactly the expected number of items: position.
    3. Anything else (miscount without indices): all None.

    A None slot is retried on its own; it is never filled with a guess.
    """
    dicts = [it if isinstance(it, dict) else None for it in items]

    indexed: dict[int, dict[str, Any]] = {}
    for it in dicts:
        if it is None:
            continue
        raw_idx = it.get("index", it.get("i", it.get("id")))
        if raw_idx is None:
            continue
        try:
            idx = int(str(raw_idx).lstrip("qQ"))
        except (TypeError, ValueError):
            continue
        if 1 <= idx <= expected and idx not in indexed:
            indexed[idx] = it

    if indexed:
        if len(indexed) != expected:
            log.warning(
                "batch_partial_indices",
                expected=expected,
                returned=len(indexed),
                detail="unmatched questions fall back to per-question classification",
            )
        return [indexed.get(i + 1) for i in range(expected)]

    if len(dicts) == expected:
        return list(dicts)

    log.warning(
        "batch_misaligned_no_index",
        expected=expected,
        returned=len(dicts),
        detail="falling back to per-question classification for this batch",
    )
    return [None] * expected


_KEY_RE = re.compile(r"^\s*q?\s*(\d+)\s*$", re.IGNORECASE)


def _extract_items(raw: Any, expected: int) -> list[dict[str, Any] | None]:
    """Turn any reply shape into exactly *expected* slots (None = unresolved)."""
    if isinstance(raw, list):
        return _align_batch(raw, expected)
    if not isinstance(raw, dict):
        return [None] * expected

    # Keyed by question: {"q1": {...}, "q2": {...}} — the constrained shape.
    keyed: dict[int, dict[str, Any]] = {}
    for k, v in raw.items():
        m = _KEY_RE.match(str(k))
        if m and isinstance(v, dict):
            idx = int(m.group(1))
            if 1 <= idx <= expected and idx not in keyed:
                keyed[idx] = v
    if keyed:
        return [keyed.get(i + 1) for i in range(expected)]

    # Wrapped array: {"results": [...]}.
    for v in raw.values():
        if isinstance(v, list):
            return _align_batch(v, expected)

    # A bare object answers a single-question batch.
    if expected == 1:
        return [raw]
    return [None] * expected


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


def _is_stale_fallback(c: Classification) -> bool:
    """A row written by the old failure path (or a model that gave up).

    Such rows used to be permanent: classification is idempotent, so a question
    filed as noise because Ollama was down was never looked at again. They are
    cheap to recognise — noise with zero confidence — and are reclassified.
    Duplicates and genuine model verdicts carry a non-zero confidence.
    """
    return (
        c.main_category == _NOISE_MAIN
        and c.sub_category == _NOISE_SUB_INVALID
        and float(c.confidence or 0.0) == 0.0
    )


def select_unclassified(questions: list[Any], engine: Engine) -> list[Any]:
    """Questions that still need a classification (none yet, or a stale fallback)."""
    ids = [q.id for q in questions if q.id is not None]
    if not ids:
        return []
    done: set[int] = set()
    with Session(engine) as session:
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            rows = session.exec(
                select(Classification).where(col(Classification.question_id).in_(chunk))
            ).all()
            done.update(r.question_id for r in rows if not _is_stale_fallback(r))
    return [q for q in questions if q.id is not None and q.id not in done]


# ─── Classifier service ───────────────────────────────────────────────────────

class ClassifierService:
    """
    Classifies questions using the configured Ollama model.

    Batches ≤ classify_batch_size questions per call, with grammar-constrained
    output. A question the batch reply could not resolve is retried on its own
    with a stricter prompt; still unresolved → Misuse / Noise, confidence 0.0
    (reclassified on the next run). Embedding-detected duplicates and questions
    with no content bypass the model.

    After a run, ``unavailable_error`` is set if Ollama could not be used, and
    ``skipped`` counts the questions left unclassified because of it.
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
        self._http: httpx.AsyncClient | None = None
        self.unavailable_error: str | None = None
        self.skipped = 0

    # ── Ollama HTTP call ─────────────────────────────────────────────────────

    async def _post(self, http: httpx.AsyncClient, payload: dict[str, Any]) -> Any:
        result: Any = None
        async for attempt in AsyncRetrying(
            retry=retry_if_exception(_is_retryable),
            stop=stop_after_attempt(_RETRY_ATTEMPTS),
            wait=wait_exponential(multiplier=1, min=_RETRY_WAIT_MIN, max=_RETRY_WAIT_MAX),
            reraise=True,
        ):
            with attempt:
                r = await http.post(f"{self._ollama_url}/api/generate", json=payload)
                r.raise_for_status()
                outer = r.json()
                result = json.loads(outer["response"])
        return result

    async def _call_ollama_raw(
        self,
        prompt: str,
        schema: dict[str, Any] | None = None,
        num_predict: int | None = None,
    ) -> Any:
        """POST to /api/generate; returns the parsed JSON value of the reply.

        Raises ``ollama.OllamaUnavailable`` when nothing reached a model.
        """
        payload = ollama.build_generate_payload(
            self._ollama_url,
            model=settings.ollama_model,
            prompt=prompt,
            system=_SYSTEM_PROMPT,
            schema=schema,
            num_predict=num_predict,
        )
        owned = self._http is None
        http = self._http or ollama.make_client(self._ollama_url, transport=self._transport)
        try:
            try:
                return await self._post(http, payload)
            except httpx.HTTPStatusError as exc:
                if ollama.is_model_missing(exc):
                    raise ollama.OllamaUnavailable(
                        f"Model '{settings.ollama_model}' is not installed in Ollama at "
                        f"{self._ollama_url}. Run `ollama pull {settings.ollama_model}` "
                        "or pick an installed model in Settings."
                    ) from exc
                if ollama.is_capability_error(exc) and ollama.has_optional_fields(payload):
                    log.warning(
                        "ollama_legacy_mode",
                        detail="server rejected schema/think fields; using plain JSON mode",
                        status=exc.response.status_code,
                    )
                    return await self._post(http, ollama.downgrade_payload(
                        self._ollama_url, payload
                    ))
                raise
            except (httpx.ConnectError, httpx.ConnectTimeout, OSError) as exc:
                if isinstance(exc, httpx.HTTPError) and not ollama.is_unreachable(exc):
                    raise
                raise ollama.OllamaUnavailable(
                    f"Cannot reach Ollama at {self._ollama_url}: {exc}"
                ) from exc
        finally:
            if owned:
                await http.aclose()

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
        """Classify one question individually. Returns None on a bad reply."""
        try:
            raw = await self._call_ollama_raw(
                _build_single_prompt(title, body, strict=strict),
                schema=_response_schema(1),
                num_predict=_TOKENS_PER_ITEM + _TOKENS_OVERHEAD,
            )
        except ollama.OllamaUnavailable:
            raise
        except Exception as exc:
            log.warning("classify_single_failed", error=str(exc))
            return None
        return _parse_single(_extract_items(raw, 1)[0])

    # ── Batch LLM call ───────────────────────────────────────────────────────

    def _fits_context(self, prompt: str, n: int) -> bool:
        budget = int(settings.ollama_num_ctx or 8192)
        need = _SYSTEM_TOKENS + _estimate_tokens(prompt) + n * _TOKENS_PER_ITEM + 64
        return need <= budget

    async def _batch_llm_call(
        self, questions: list[Any]
    ) -> list[dict[str, Any] | None]:
        """
        Send one batch prompt for all questions; return one result per question.
        Any question whose result is None will trigger a per-item retry upstream.
        """
        n = len(questions)
        q_dicts = [{"title": q.title, "body": q.body or ""} for q in questions]
        prompt = _build_batch_prompt(q_dicts)
        if n > 1 and not self._fits_context(prompt, n):
            # An overflowing prompt is truncated from the front (the taxonomy).
            # Halve instead — two smaller calls beat one blind one.
            half = n // 2
            log.info("batch_split_for_context", batch_size=n)
            return (await self._batch_llm_call(questions[:half])) + (
                await self._batch_llm_call(questions[half:])
            )
        try:
            raw = await self._call_ollama_raw(
                prompt,
                schema=_response_schema(n),
                num_predict=n * _TOKENS_PER_ITEM + _TOKENS_OVERHEAD,
            )
        except ollama.OllamaUnavailable:
            raise
        except Exception as exc:
            log.warning("batch_llm_failed", error=str(exc), batch_size=n)
            return [None] * n
        return _extract_items(raw, n)

    # ── Batch classification (internal) ──────────────────────────────────────

    async def _classify_batch(self, questions: list[Any]) -> list[ClassificationResult]:
        """
        Classify one batch.

        1. Questions with no content, and embedding-detected duplicates, are
           marked noise without a model call.
        2. One constrained batch call for the rest.
        3. Per-item: an unresolved result is retried alone with a strict prompt.
        4. Still unresolved → fallback noise with confidence 0.0.
        """
        final: list[ClassificationResult | None] = [None] * len(questions)
        to_llm: list[tuple[int, Any]] = []
        for i, q in enumerate(questions):
            if _is_too_short(q.title or "", q.body or ""):
                final[i] = ClassificationResult(
                    question_id=q.id, main_category=_NOISE_MAIN,
                    sub_category=_NOISE_SUB_INVALID, confidence=0.95, is_noise=True,
                    reason="No meaningful content to classify.",
                )
            elif await self._is_duplicate(q):
                log.info("duplicate_classified_as_noise", so_id=q.so_id)
                final[i] = ClassificationResult(
                    question_id=q.id, main_category=_NOISE_MAIN,
                    sub_category=_NOISE_SUB_DUPLICATE, confidence=1.0, is_noise=True,
                    reason="Near-duplicate detected by vector similarity.",
                )
            else:
                to_llm.append((i, q))

        if to_llm:
            raw_items = await self._batch_llm_call([q for _, q in to_llm])
            for (orig_idx, q), raw in zip(to_llm, raw_items, strict=False):
                parsed = _parse_single(raw) if isinstance(raw, dict) else None
                if parsed is None:
                    parsed = await self._classify_single_question(
                        q.title, q.body or "", strict=True
                    )
                if parsed is None:
                    log.warning(
                        "classify_fallback_noise",
                        so_id=q.so_id,
                        title=(q.title or "")[:60],   # abbreviated — never log full body
                    )
                    parsed = _FALLBACK
                main, sub, confidence, reason = parsed
                final[orig_idx] = ClassificationResult(
                    question_id=q.id,
                    main_category=main,
                    sub_category=sub,
                    confidence=confidence,
                    is_noise=(main == _NOISE_MAIN),
                    reason=reason,
                )

        return [r for r in final if r is not None]

    def _persist(self, engine: Engine, results: list[ClassificationResult]) -> None:
        if not results:
            return
        ids = [r.question_id for r in results]
        with Session(engine) as session:
            # Replace stale fallback rows being reclassified (see _is_stale_fallback).
            for old in session.exec(
                select(Classification).where(col(Classification.question_id).in_(ids))
            ).all():
                session.delete(old)
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

    # ── Public API ───────────────────────────────────────────────────────────

    async def classify_questions(
        self,
        questions: list[Any],
        engine: Engine,
        progress: ProgressCallback | None = None,
    ) -> list[ClassificationResult]:
        """
        Classify all questions, persisting each batch as it completes.

        Idempotent: questions that already have a classification are skipped
        (stale fallback rows excepted). Returns a ClassificationResult for every
        question classified this run. *progress*, if given, is awaited with
        (done, total) after each batch.

        If Ollama becomes unavailable, the run stops; work already done is kept,
        the rest stays unclassified, and ``unavailable_error`` explains why.
        """
        self.unavailable_error = None
        self.skipped = 0
        to_classify = select_unclassified(questions, engine)
        total = len(to_classify)

        # Read at call time so the setting can be changed without a restart, and
        # so a nonsensical value can never produce a zero or negative step.
        batch_size = max(1, int(settings.classify_batch_size or _BATCH_SIZE))
        batches = [to_classify[s : s + batch_size] for s in range(0, total, batch_size)]
        concurrency = max(1, int(settings.classify_concurrency or 1))
        sem = asyncio.Semaphore(concurrency)
        stop = asyncio.Event()
        results: list[ClassificationResult] = []
        done = 0
        lock = asyncio.Lock()

        async def run(batch: list[Any]) -> None:
            nonlocal done
            async with sem:
                if stop.is_set():
                    return
                try:
                    batch_results = await self._classify_batch(batch)
                except ollama.OllamaUnavailable as exc:
                    if not stop.is_set():
                        stop.set()
                        self.unavailable_error = str(exc)
                        log.error("classify_ollama_unavailable", error=str(exc))
                    return
                self._persist(engine, batch_results)
                async with lock:
                    results.extend(batch_results)
                    done += len(batch)
                    current = done
            if progress is not None:
                try:
                    await progress(current, total)
                except Exception as exc:  # progress reporting must never fail a run
                    log.warning("classify_progress_failed", error=str(exc))

        async with ollama.make_client(self._ollama_url, transport=self._transport) as http:
            self._http = http
            try:
                if concurrency == 1:
                    for batch in batches:
                        await run(batch)
                        if stop.is_set():
                            break
                else:
                    await asyncio.gather(*(run(b) for b in batches))
            finally:
                self._http = None

        self.skipped = total - done
        log.info(
            "classify_done",
            total=len(results),
            noise=sum(1 for r in results if r.is_noise),
            skipped=self.skipped,
        )
        return results
