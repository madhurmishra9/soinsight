from datetime import datetime, timedelta


def resolve_range(
    window_days: int,
    from_date: str | None = None,
    to_date: str | None = None,
) -> tuple[datetime, datetime]:
    now = datetime.utcnow()
    since = datetime.fromisoformat(from_date) if from_date else now - timedelta(days=window_days)
    until = (
        datetime.fromisoformat(to_date) + timedelta(days=1) - timedelta(microseconds=1)
        if to_date
        else now
    )
    if since > until:
        raise ValueError("from_date must be on or before to_date")
    return since, until
