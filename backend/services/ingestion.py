"""
Ingestion service: pull questions from SO Enterprise → SQLite.

IMPORTANT — field name constants (prefixed _SO_) are guesses based on typical
SO Enterprise v3 shapes. Verify each one against <SO_BASE_URL>/api/v3 Swagger
before running against a real instance. Wrong names silently default to empty/0.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import Engine
from sqlalchemy import func as sql_func
from sqlmodel import Session, col, select

from app.dates import resolve_range, utcfromtimestamp, utcnow
from app.models import Answer, Question, Run
from services.so_client import SOClient

log = structlog.get_logger("soinsight.ingestion")

# ─── TODO: verify all of these against <SO_BASE_URL>/api/v3 Swagger ──────────
_SO_QUESTION_ID = "id"
_SO_CREATION_DATE = "creationDate"
_SO_AUTHOR_KEY = "owner"
_SO_AUTHOR_ID = "id"
_SO_HAS_ACCEPTED = "isAnswered"
# Answer field names (guessed — verify in Swagger):
_SO_ANSWER_ID = "id"
_SO_ANSWER_ACCEPTED = "isAccepted"
# ─────────────────────────────────────────────────────────────────────────────

DAILY_BUDGET = 9_500  # 95 % of the 10 000/day quota — remaining is headroom


class BudgetExhaustedError(RuntimeError):
    """Raised when the daily API call budget is exhausted."""


class BudgetTracker:
    """
    Simple daily call counter. Resets at midnight UTC.
    Charge once per iter_questions call (one tag fetch = one logical unit).
    """

    def __init__(self, daily_limit: int = DAILY_BUDGET) -> None:
        self._limit = daily_limit
        self._count = 0
        self._date = utcnow().date()

    def _maybe_reset(self) -> None:
        today = utcnow().date()
        if today != self._date:
            self._count = 0
            self._date = today

    def charge(self, n: int = 1) -> None:
        """Raise BudgetExhaustedError if charging n would exceed the daily limit."""
        self._maybe_reset()
        if self._count + n > self._limit:
            raise BudgetExhaustedError(
                f"Daily budget of {self._limit} calls reached ({self._count} used)"
            )
        self._count += n

    @property
    def remaining(self) -> int:
        self._maybe_reset()
        return self._limit - self._count

    @property
    def used(self) -> int:
        self._maybe_reset()
        return self._count


@dataclass
class IngestResult:
    inserted: int = 0
    skipped: int = 0
    errors: int = 0
    answers_fetched: int = 0
    tags: list[str] = field(default_factory=list)


def _map_question(raw: dict[str, Any], team_slug: str | None = None) -> dict[str, Any]:
    """
    Map a raw SO Enterprise v3 question dict → Question model kwargs.

    Raises KeyError if so_id is missing (caller should catch and log).
    All other fields fall back to safe defaults when absent.
    """
    owner: dict[str, Any] = raw.get(_SO_AUTHOR_KEY) or {}

    tags_raw = raw.get("tags") or []
    if tags_raw and isinstance(tags_raw[0], dict):
        tag_names = [t["name"] for t in tags_raw if isinstance(t, dict) and "name" in t]
    else:
        tag_names = [str(t) for t in tags_raw]

    raw_date = raw.get(_SO_CREATION_DATE)
    if isinstance(raw_date, str) and raw_date:
        try:
            parsed = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            created_at = parsed.replace(tzinfo=None)
        except ValueError:
            created_at = utcnow()
    elif isinstance(raw_date, (int, float)) and raw_date:
        created_at = utcfromtimestamp(raw_date)
    else:
        created_at = utcnow()

    return {
        "so_id": int(raw[_SO_QUESTION_ID]),
        "title": str(raw.get("title") or ""),
        "body": str(raw.get("body") or raw.get("bodyMarkdown") or ""),
        "tags": json.dumps(tag_names),
        "score": int(raw.get("score") or 0),
        "view_count": int(raw.get("viewCount") or 0),
        "created_at": created_at,
        "author_id": int(owner.get(_SO_AUTHOR_ID) or owner.get("accountId") or 0),
        "author_role": owner.get("role"),
        "answer_count": int(raw.get("answerCount") or 0),
        "has_accepted": bool(raw.get(_SO_HAS_ACCEPTED)),
        "team_slug": team_slug,
    }


def _map_answer(raw: dict[str, Any], question_so_id: int) -> dict[str, Any]:
    """
    Map a raw SO Enterprise v3 answer dict → Answer model kwargs.

    Raises KeyError if the answer so_id is missing (caller should catch and log).
    All other fields fall back to safe defaults when absent.
    """
    owner: dict[str, Any] = raw.get(_SO_AUTHOR_KEY) or {}

    raw_date = raw.get(_SO_CREATION_DATE)
    if isinstance(raw_date, str) and raw_date:
        try:
            created_at = datetime.fromisoformat(
                raw_date.replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except ValueError:
            created_at = utcnow()
    elif isinstance(raw_date, (int, float)) and raw_date:
        created_at = utcfromtimestamp(raw_date)
    else:
        created_at = utcnow()

    return {
        "so_id": int(raw[_SO_ANSWER_ID]),
        "question_so_id": int(question_so_id),
        "body": str(raw.get("body") or raw.get("bodyMarkdown") or ""),
        "score": int(raw.get("score") or 0),
        "is_accepted": bool(raw.get(_SO_ANSWER_ACCEPTED) or raw.get("accepted")),
        "author_id": int(owner.get(_SO_AUTHOR_ID) or owner.get("accountId") or 0),
        "author_role": owner.get("role"),
        "created_at": created_at,
    }


class IngestService:
    """
    Fetches questions from SO Enterprise and upserts them into SQLite.
    Idempotent: re-running with the same products/window skips existing rows.
    """

    def __init__(
        self,
        client: SOClient,
        budget: BudgetTracker | None = None,
        fetch_answers: bool = True,
    ) -> None:
        self._client = client
        self._budget = budget or BudgetTracker()
        # Answers are only fetched when enabled AND the client actually exposes
        # iter_answers — this keeps lightweight/mocked clients working unchanged.
        self._fetch_answers = fetch_answers and hasattr(client, "iter_answers")

    async def _ingest_answers(
        self,
        question_so_id: int,
        team: str | None,
        session: Session,
    ) -> int:
        """
        Fetch and upsert all answers for one question. Returns the number of new
        answer rows inserted. Failures are non-fatal (logged, return what we got).
        Charges one budget unit for the answer fetch of this question.
        """
        try:
            self._budget.charge()
        except BudgetExhaustedError:
            log.warning("budget_exhausted_answers", question_so_id=question_so_id)
            return 0

        inserted = 0
        try:
            async for raw_a in self._client.iter_answers(question_so_id, team=team):
                try:
                    a_data = _map_answer(raw_a, question_so_id=question_so_id)
                    a_so_id = a_data["so_id"]
                    exists = session.exec(
                        select(Answer).where(Answer.so_id == a_so_id)
                    ).first()
                    if exists is None:
                        session.add(Answer(**a_data))
                        inserted += 1
                except (KeyError, ValueError, TypeError) as exc:
                    log.warning("map_answer_failed", error=str(exc))
        except Exception as exc:  # network/HTTP — don't abort the whole ingest
            log.warning("answer_fetch_failed", question_so_id=question_so_id, error=str(exc))

        return inserted

    async def run(
        self,
        products: list[str],
        window_days: int,
        team: str | None,
        queue: asyncio.Queue[dict[str, Any] | None],
        engine: Engine,
        from_date: str | None = None,
        to_date: str | None = None,
        incremental: bool = True,
    ) -> IngestResult:
        """
        Fetch questions for each tag in *products* created within *window_days*.
        When incremental=True (default), each tag's since is advanced to the
        most recent question already in DB for that tag, so only new questions
        are fetched from SO. Always puts a None sentinel into *queue* when done.
        """
        since, until = resolve_range(window_days, from_date, to_date)
        result = IngestResult(tags=list(products))

        with Session(engine) as session:
            run_rec = Run(
                products=json.dumps(products),
                window_days=window_days,
                status="running",
            )
            session.add(run_rec)
            session.commit()
            session.refresh(run_rec)
            run_id: int | None = run_rec.id

            for tag in products:
                await queue.put({"type": "tag_start", "tag": tag})
                log.info("ingest_tag_start", tag=tag)
                # Snapshot so we can emit a per-tag delta even when the tag
                # yields fewer than 25 questions and the in-loop progress
                # event never fires.
                inserted_at_tag_start = result.inserted
                skipped_at_tag_start = result.skipped

                try:
                    self._budget.charge()
                except BudgetExhaustedError:
                    log.warning("budget_exhausted", tag=tag)
                    await queue.put(
                        {"type": "warning", "message": "Daily budget exhausted; stopping."}
                    )
                    break

                # Incremental: advance since to last known question for this tag
                effective_since = since
                if incremental:
                    last_ts = session.exec(
                        select(sql_func.max(Question.created_at)).where(
                            col(Question.tags).like(f'%"{tag}"%')
                        )
                    ).first()
                    if last_ts and last_ts > since:
                        effective_since = last_ts
                        log.info("ingest_incremental", tag=tag, since=str(effective_since))
                        await queue.put({
                            "type": "info",
                            "message": (
                                f"{tag}: incremental — fetching only questions newer than "
                                f"{effective_since:%Y-%m-%d %H:%M} UTC "
                                "(already stored locally up to here)."
                            ),
                        })
                    else:
                        log.info("ingest_full", tag=tag, since=str(effective_since))

                async for raw_q in self._client.iter_questions(
                    tag=tag, since=effective_since, until=until, team=team
                ):
                    try:
                        q_data = _map_question(raw_q, team_slug=team)
                        so_id: int = q_data["so_id"]

                        existing = session.exec(
                            select(Question).where(Question.so_id == so_id)
                        ).first()

                        if existing is None:
                            session.add(Question(**q_data))
                            result.inserted += 1
                            # Pull this question's answers (best-effort, gated by
                            # config + budget). Linked by question_so_id, so the
                            # parent's local PK is not needed here.
                            if self._fetch_answers and q_data.get("answer_count"):
                                fetched = await self._ingest_answers(
                                    question_so_id=so_id,
                                    team=team,
                                    session=session,
                                )
                                result.answers_fetched += fetched
                        else:
                            result.skipped += 1

                        total = result.inserted + result.skipped
                        if total % 25 == 0:
                            session.commit()
                            await queue.put({
                                "type": "progress",
                                "tag": tag,
                                "inserted": result.inserted,
                                "skipped": result.skipped,
                            })
                    except (KeyError, ValueError, TypeError) as exc:
                        result.errors += 1
                        log.warning("map_question_failed", error=str(exc))

                # Per-tag wrap-up commit + progress event — guarantees the UI
                # always sees a final count for the tag, even at low volume.
                session.commit()
                await queue.put({
                    "type": "progress",
                    "tag": tag,
                    "inserted": result.inserted,
                    "skipped": result.skipped,
                    "tag_inserted": result.inserted - inserted_at_tag_start,
                    "tag_skipped": result.skipped - skipped_at_tag_start,
                    "tag_done": True,
                })

            session.commit()

            if run_id is not None:
                run_rec_upd = session.get(Run, run_id)
                if run_rec_upd is not None:
                    run_rec_upd.finished_at = utcnow()
                    run_rec_upd.status = "done" if result.errors == 0 else "partial"
                    run_rec_upd.counts = json.dumps({
                        "inserted": result.inserted,
                        "skipped": result.skipped,
                        "errors": result.errors,
                        "answers_fetched": result.answers_fetched,
                    })
                    session.add(run_rec_upd)
                    session.commit()

        log.info(
            "ingest_done",
            inserted=result.inserted,
            skipped=result.skipped,
            errors=result.errors,
            answers_fetched=result.answers_fetched,
        )
        await queue.put({
            "type": "done",
            "inserted": result.inserted,
            "skipped": result.skipped,
            "errors": result.errors,
            "answers_fetched": result.answers_fetched,
        })
        await queue.put(None)
        return result
