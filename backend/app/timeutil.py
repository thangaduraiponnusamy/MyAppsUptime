from datetime import datetime, timezone

# Shared by the server and the probe (no database imports here)
APP_VERSION = "18.0.0"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    # SQLite drops tzinfo on read
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
