from datetime import datetime

from sqlmodel import Field, SQLModel

from app.dates import utcnow


class Question(SQLModel, table=True):
    __tablename__ = "questions"
    id: int | None = Field(default=None, primary_key=True)
    so_id: int = Field(unique=True, index=True)
    title: str
    body: str
    tags: str  # JSON-serialised list[str]
    score: int = 0
    view_count: int = 0
    created_at: datetime
    author_id: int
    author_role: str | None = None
    answer_count: int = 0
    has_accepted: bool = False
    team_slug: str | None = None


class Answer(SQLModel, table=True):
    """An answer to a Question, linked by the parent question's SO id.

    Stored as its own table (created automatically by SQLModel.metadata.create_all
    on next startup — existing databases gain the table with no manual migration).
    """
    __tablename__ = "answers"
    id: int | None = Field(default=None, primary_key=True)
    so_id: int = Field(unique=True, index=True)        # SO answer id
    question_so_id: int = Field(index=True)            # parent question's so_id
    body: str = ""
    score: int = 0
    is_accepted: bool = False
    author_id: int = 0
    author_role: str | None = None
    created_at: datetime


class Classification(SQLModel, table=True):
    __tablename__ = "classifications"
    id: int | None = Field(default=None, primary_key=True)
    question_id: int = Field(foreign_key="questions.id", index=True)
    main_category: str
    sub_category: str
    confidence: float
    is_noise: bool = False
    model: str
    classified_at: datetime = Field(default_factory=utcnow)


class Pattern(SQLModel, table=True):
    __tablename__ = "patterns"
    id: int | None = Field(default=None, primary_key=True)
    product_tag: str = Field(index=True)
    window_days: int
    main_category: str
    sub_category: str
    question_count: int
    distinct_users: int
    summary: str | None = None
    suggested_action: str | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None


class Run(SQLModel, table=True):
    __tablename__ = "runs"
    id: int | None = Field(default=None, primary_key=True)
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    products: str  # JSON-serialised list[str]
    window_days: int
    status: str  # "running" | "done" | "failed"
    counts: str | None = None  # JSON-serialised dict


class Remediation(SQLModel, table=True):
    """A grounded, LLM-generated fix-guide for one cluster of similar questions.

    One row per (product_tag, window_days, main_category, sub_category). The text
    fields are derived strictly from the cluster's stored questions and answers;
    `evidence_*_so_ids` record exactly which sources were used so every claim is
    auditable, and `grounded` is False when validation could not anchor the
    suggestion to real captured sources.
    """
    __tablename__ = "remediations"
    id: int | None = Field(default=None, primary_key=True)
    product_tag: str = Field(index=True)
    window_days: int
    main_category: str
    sub_category: str
    question_count: int = 0
    distinct_users: int = 0
    root_cause: str = ""
    solution: str = ""
    prevention: str = ""
    confidence: float = 0.0
    grounded: bool = False
    evidence_question_so_ids: str = "[]"   # JSON list[int]
    evidence_answer_so_ids: str = "[]"     # JSON list[int]
    content_hash: str = ""
    model: str = ""
    generated_at: datetime = Field(default_factory=utcnow)


class PatternDismissal(SQLModel, table=True):
    """An analyst has acknowledged a recurring (product, main, sub) pattern.

    A dismissed pattern is hidden from `top_issues`, `patterns`, and
    `recommended_actions` in the summary until `dismissed_until` passes.
    A null `dismissed_until` is an indefinite dismissal (until explicitly
    restored). Snoozing is per-(product, main, sub) so it survives window
    changes and re-aggregation.
    """
    __tablename__ = "pattern_dismissals"
    id: int | None = Field(default=None, primary_key=True)
    product_tag: str = Field(index=True)
    main_category: str
    sub_category: str
    dismissed_until: datetime | None = None
    reason: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class ScheduleConfig(SQLModel, table=True):
    """Singleton row — there is at most one row in this table."""
    __tablename__ = "schedule_config"
    id: int | None = Field(default=None, primary_key=True)
    enabled: bool = False
    interval_hours: int = 24      # cadence; 1–8760
    products: str = "[]"          # JSON list[str]
    window_days: int = 30         # look-back window for each refresh
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
