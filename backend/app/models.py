from datetime import datetime

from sqlmodel import Field, SQLModel


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


class Classification(SQLModel, table=True):
    __tablename__ = "classifications"
    id: int | None = Field(default=None, primary_key=True)
    question_id: int = Field(foreign_key="questions.id", index=True)
    main_category: str
    sub_category: str
    confidence: float
    is_noise: bool = False
    model: str
    classified_at: datetime = Field(default_factory=datetime.utcnow)


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
    started_at: datetime = Field(default_factory=datetime.utcnow)
    finished_at: datetime | None = None
    products: str  # JSON-serialised list[str]
    window_days: int
    status: str  # "running" | "done" | "failed"
    counts: str | None = None  # JSON-serialised dict


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
