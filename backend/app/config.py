from sqlalchemy.orm import Session

from .models import AppSetting

APP_VERSION = "16.0.0"

DEFAULTS = {
    "general": {"app_url": "", "refresh_sec": 5, "retention_value": 1, "retention_unit": "years"},
    "smtp": {"host": "", "port": 587, "security": "starttls", "username": "", "password": "", "from_addr": ""},
}

UNIT_DAYS = {"days": 1, "months": 30, "years": 365}


def retention_days(general: dict) -> int:
    return int(general["retention_value"]) * UNIT_DAYS.get(general["retention_unit"], 365)


def get_setting(db: Session, key: str) -> dict:
    row = db.get(AppSetting, key)
    return {**DEFAULTS[key], **(row.value if row else {})}


def put_setting(db: Session, key: str, value: dict) -> dict:
    row = db.get(AppSetting, key)
    merged = {**get_setting(db, key), **value}
    if row is None:
        db.add(AppSetting(key=key, value=merged))
    else:
        row.value = merged
    db.commit()
    return merged
