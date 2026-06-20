"""
Remediation service — grounded, LLM-generated fix guides for clusters of
similar questions.

For each (main, sub) category cluster that meets the pattern threshold, this
feeds the cluster's *actual* questions and their *actual* stored answers to the
local LLM and asks for a detailed, recurrence-preventing fix.

Grounding is enforced structurally, not by trust:
  * The model is told to use ONLY the supplied questions/answers, cite the source
    IDs it relied on, and never invent steps when the answers lack a solution.
  * Every cited evidence ID is intersected with the cluster's real source IDs;
    anything the model invents is discarded.
  * A remediation is marked grounded only when at least one cited question ID
    survives that check. Otherwise no model prose is stored — a neutral
    "insufficient grounded evidence" record is written instead.
The UI shows the surviving evidence next to each suggestion so every claim is
auditable back to a real captured question or answer.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog
from sqlalchemy import Engine
from sqlmodel import Session, select
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from app.dates import resolve_range
from app.models import Answer, Classification, Question, Remediation
from app.settings import settings
from services.aggregator import _question_has_tag

log = structlog.get_logger("soinsight.remediation")

_NOISE_MAIN = "Misuse / Noise"

_MIN_QUESTIONS = 3
_MIN_USERS = 2
_MAX_QS_PER_CLUSTER = 15
_MAX_ANSWERS_PER_Q = 4
_BODY_CHARS = 700
_ANSWER_CHARS = 700

_RETRY_ATTEMPTS = 4
_RETRY_WAIT_MIN = 1   # monkeypatch to 0 in tests
_RETRY_WAIT_MAX = 30

_UNGROUNDED_NOTE = (
    "No grounded fix could be produced: the model's output could not be tied back "
    "to the captured questions and answers. Review the source questions directly."
)


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TimeoutException):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (429, 500, 502, 503, 504)
    return False


@dataclass
class Cluster:
    main: str
    sub: str
    questions: list[Question]
    answers_by_q: dict[int, list[Answer]]  # keyed by question.so_id
    distinct_users: int = 0
    question_so_ids: set[int] = field(default_factory=set)
    answer_so_ids: set[int] = field(default_factory=set)


def _truncate(text: str, limit: int) -> str:
    collapsed = " ".join((text or "").split())
    return collapsed if len(collapsed) <= limit else collapsed[:limit].rstrip() + "…"


_PROMPT_INSTRUCTIONS = "\n".join([
    "You are a support-engineering analyst. Below is a cluster of similar Stack",
    "Overflow Enterprise questions about the same underlying problem, together",
    "with their captured answers.",
    "",
    "Category: {category}",
    "",
    "STRICT GROUNDING RULES — follow exactly:",
    "- Use ONLY the information contained in the SOURCES below. Do NOT use any",
    "  outside or prior knowledge.",
    "- Base every statement on one or more specific sources and record which Q/A",
    "  IDs you used.",
    "- If the captured answers do not contain a real solution, say so plainly in",
    '  "solution" and do NOT invent steps. Derive prevention from the recurring',
    "  problem the questions describe.",
    "- Do not reference any ID that is not present in the SOURCES.",
    "",
    "SOURCES:",
    "{sources}",
    "",
    "Produce a single JSON object (and nothing else) with these keys:",
    '  "root_cause":  one short paragraph naming the shared underlying cause,',
    "                 grounded in the sources.",
    '  "solution":    detailed, ordered, concrete steps to resolve the problem,',
    "                 drawn strictly from the captured answers/questions. If no",
    "                 answer contains a solution, state that explicitly.",
    '  "prevention":  specific actions (documentation to add/fix, guardrails,',
    "                 defaults, onboarding) that would stop this exact cluster of",
    "                 questions from recurring.",
    '  "evidence_question_ids": array of integer question IDs (numbers after "Q")',
    "                 you actually used.",
    '  "evidence_answer_ids":   array of integer answer IDs (numbers after "A")',
    "                 you actually used.",
    '  "confidence":  number 0..1 for how well the sources support your answer.',
    "",
    "Return only the JSON object.",
])


def _build_prompt(cluster: Cluster) -> str:
    """Build the strictly-grounded remediation prompt for one cluster."""
    lines: list[str] = []
    for q in cluster.questions:
        lines.append(f"QUESTION Q{q.so_id}: {q.title}")
        body = _truncate(q.body, _BODY_CHARS)
        if body:
            lines.append(f"  body: {body}")
        answers = cluster.answers_by_q.get(q.so_id, [])[:_MAX_ANSWERS_PER_Q]
        if not answers:
            lines.append("  (no answers captured for this question)")
        for a in answers:
            tag = "accepted" if a.is_accepted else f"score {a.score}"
            lines.append(f"  ANSWER A{a.so_id} ({tag}): {_truncate(a.body, _ANSWER_CHARS)}")
        lines.append("")

    return _PROMPT_INSTRUCTIONS.format(
        category=f"{cluster.main} / {cluster.sub}",
        sources="\n".join(lines).rstrip(),
    )


class RemediationService:
    """Generates and persists grounded remediation guides per question cluster."""

    def __init__(
        self,
        ollama_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        min_questions: int = _MIN_QUESTIONS,
        min_users: int = _MIN_USERS,
    ) -> None:
        self._ollama_url = (ollama_url or settings.ollama_url).rstrip("/")
        self._transport = transport
        self._min_questions = min_questions
        self._min_users = min_users

    # ── Ollama call (mirrors the classifier's shape; transport is injectable) ──

    async def _call_ollama(self, prompt: str) -> dict[str, Any]:
        result: dict[str, Any] = {}
        async with httpx.AsyncClient(timeout=180.0, transport=self._transport) as http:
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
                            "options": {"temperature": 0},
                        },
                    )
                    r.raise_for_status()
                    outer = r.json()
                    parsed = json.loads(outer["response"])
                    result = parsed if isinstance(parsed, dict) else {}
        return result

    # ── Cluster gathering ──────────────────────────────────────────────────────

    def _gather_clusters(
        self,
        product: str,
        window_days: int,
        session: Session,
        from_date: str | None,
        to_date: str | None,
    ) -> list[Cluster]:
        since, until = resolve_range(window_days, from_date, to_date)
        all_qs = session.exec(
            select(Question).where(Question.created_at >= since, Question.created_at <= until)
        ).all()
        questions = [q for q in all_qs if _question_has_tag(q, product)]
        q_by_id: dict[int, Question] = {q.id: q for q in questions if q.id is not None}
        if not q_by_id:
            return []

        cls = session.exec(
            select(Classification).where(
                Classification.question_id.in_(list(q_by_id.keys())),  # type: ignore[attr-defined]
                Classification.is_noise == False,  # noqa: E712
            )
        ).all()

        grouped: dict[tuple[str, str], list[Question]] = {}
        for c in cls:
            q = q_by_id.get(c.question_id)
            if q is None or c.main_category == _NOISE_MAIN:
                continue
            grouped.setdefault((c.main_category, c.sub_category), []).append(q)

        clusters: list[Cluster] = []
        for (main, sub), qs in grouped.items():
            users = {q.author_id for q in qs}
            if len(qs) < self._min_questions or len(users) < self._min_users:
                continue
            top = sorted(
                qs, key=lambda x: (x.score, x.view_count), reverse=True
            )[:_MAX_QS_PER_CLUSTER]
            so_ids = [q.so_id for q in top]
            answers = session.exec(
                select(Answer).where(Answer.question_so_id.in_(so_ids))  # type: ignore[attr-defined]
            ).all()
            answers_by_q: dict[int, list[Answer]] = {}
            answer_so_ids: set[int] = set()
            for a in answers:
                answers_by_q.setdefault(a.question_so_id, []).append(a)
                answer_so_ids.add(a.so_id)
            for qid in answers_by_q:
                answers_by_q[qid].sort(key=lambda x: (not x.is_accepted, -x.score))
            clusters.append(Cluster(
                main=main, sub=sub, questions=top, answers_by_q=answers_by_q,
                distinct_users=len(users),
                question_so_ids=set(so_ids), answer_so_ids=answer_so_ids,
            ))
        # Largest clusters first.
        clusters.sort(key=lambda c: len(c.questions), reverse=True)
        return clusters

    @staticmethod
    def _content_hash(cluster: Cluster, model: str) -> str:
        payload = json.dumps({
            "q": sorted(cluster.question_so_ids),
            "a": sorted(cluster.answer_so_ids),
            "m": model,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()

    # ── Grounding validation ───────────────────────────────────────────────────

    def _validate(self, cluster: Cluster, raw: dict[str, Any], model: str) -> dict[str, Any]:
        """Intersect the model's cited evidence with the cluster's real sources."""
        def _int_list(v: Any) -> list[int]:
            out: list[int] = []
            if isinstance(v, list):
                for x in v:
                    try:
                        out.append(int(x))
                    except (ValueError, TypeError):
                        continue
            return out

        ev_q = sorted(set(_int_list(raw.get("evidence_question_ids"))) & cluster.question_so_ids)
        ev_a = sorted(set(_int_list(raw.get("evidence_answer_ids"))) & cluster.answer_so_ids)
        grounded = len(ev_q) >= 1

        try:
            confidence = float(raw.get("confidence", 0.0))
        except (ValueError, TypeError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        if grounded:
            root_cause = str(raw.get("root_cause") or "").strip()
            solution = str(raw.get("solution") or "").strip()
            prevention = str(raw.get("prevention") or "").strip()
        else:
            root_cause = ""
            solution = ""
            prevention = _UNGROUNDED_NOTE
            confidence = 0.0

        return {
            "main_category": cluster.main,
            "sub_category": cluster.sub,
            "question_count": len(cluster.questions),
            "distinct_users": cluster.distinct_users,
            "root_cause": root_cause,
            "solution": solution,
            "prevention": prevention,
            "confidence": confidence,
            "grounded": grounded,
            "evidence_question_so_ids": json.dumps(ev_q),
            "evidence_answer_so_ids": json.dumps(ev_a),
            "content_hash": self._content_hash(cluster, model),
            "model": model,
        }

    # ── Persistence ────────────────────────────────────────────────────────────

    @staticmethod
    def _upsert(product: str, window_days: int, data: dict[str, Any], session: Session) -> None:
        existing = session.exec(
            select(Remediation).where(
                Remediation.product_tag == product,
                Remediation.window_days == window_days,
                Remediation.main_category == data["main_category"],
                Remediation.sub_category == data["sub_category"],
            )
        ).first()
        if existing is None:
            session.add(Remediation(product_tag=product, window_days=window_days, **data))
        else:
            for k, v in data.items():
                setattr(existing, k, v)
            session.add(existing)
        session.commit()

    # ── Orchestration ──────────────────────────────────────────────────────────

    async def run(
        self,
        products: list[str],
        window_days: int,
        engine: Engine,
        queue: asyncio.Queue[dict[str, Any] | None],
        from_date: str | None = None,
        to_date: str | None = None,
        regenerate: bool = False,
    ) -> int:
        """Generate grounded remediations for every qualifying cluster.

        regenerate=False reuses any stored remediation whose source set is
        unchanged (same content hash), so re-running is cheap.
        Always puts a None sentinel on the queue when done.
        """
        model = settings.ollama_model
        generated = 0
        try:
            for product in products:
                with Session(engine) as session:
                    clusters = self._gather_clusters(
                        product, window_days, session, from_date, to_date
                    )
                if not clusters:
                    await queue.put({
                        "type": "info",
                        "message": f"{product}: no clusters met the threshold "
                                   f"(≥{self._min_questions} questions from "
                        f"≥{self._min_users} users).",
                    })
                    continue

                await queue.put({
                    "type": "info",
                    "message": f"{product}: {len(clusters)} cluster(s) to remediate.",
                })

                for cluster in clusters:
                    label = f"{cluster.main} / {cluster.sub}"
                    with Session(engine) as session:
                        if not regenerate:
                            prior = session.exec(
                                select(Remediation).where(
                                    Remediation.product_tag == product,
                                    Remediation.window_days == window_days,
                                    Remediation.main_category == cluster.main,
                                    Remediation.sub_category == cluster.sub,
                                )
                            ).first()
                            if prior and prior.content_hash == self._content_hash(cluster, model):
                                await queue.put({
                                    "type": "cluster_done",
                                    "tag": product, "cluster": label,
                                    "grounded": prior.grounded, "cached": True,
                                })
                                continue

                    await queue.put({"type": "cluster_start", "tag": product, "cluster": label})
                    try:
                        raw = await self._call_ollama(_build_prompt(cluster))
                    except Exception as exc:
                        log.warning(
                            "remediation_llm_failed",
                            product=product, cluster=label, error=str(exc),
                        )
                        await queue.put({
                            "type": "warning",
                            "message": f"{label}: model call failed — {exc}",
                        })
                        continue

                    data = self._validate(cluster, raw, model)
                    with Session(engine) as session:
                        self._upsert(product, window_days, data, session)
                    generated += 1
                    await queue.put({
                        "type": "cluster_done",
                        "tag": product, "cluster": label,
                        "grounded": data["grounded"], "cached": False,
                    })

            log.info("remediation_done", generated=generated)
            await queue.put({"type": "done", "generated": generated})
        except Exception as exc:  # defensive — never leave the stream hanging
            log.error("remediation_run_error", error=str(exc))
            await queue.put({"type": "error", "message": str(exc)})
        finally:
            await queue.put(None)
        return generated
