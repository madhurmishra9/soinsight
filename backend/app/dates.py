from datetime import datetime, timedelta, timezone


def utcnow() -> datetime:
    """Return a naive UTC datetime.

    `datetime.utcnow()` is deprecated in Python 3.12+ and removed in 3.14.
    The SQLite columns are stored as naive datetimes, so we strip tzinfo
    here to keep schema compatibility.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcfromtimestamp(ts: float) -> datetime:
    """Naive UTC datetime from a POSIX timestamp — deprecation-safe replacement."""
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


def resolve_range(
    window_days: int,
    from_date: str | None = None,
    to_date: str | None = None,
) -> tuple[datetime, datetime]:
    now = utcnow()
    since = datetime.fromisoformat(from_date) if from_date else now - timedelta(days=window_days)
    until = (
        datetime.fromisoformat(to_date) + timedelta(days=1) - timedelta(microseconds=1)
        if to_date
        else now
    )
    if since > until:
        raise ValueError("from_date must be on or before to_date")
    return since, until
