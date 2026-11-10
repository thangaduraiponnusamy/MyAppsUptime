from sqlalchemy.orm import Session

from .models import AppSetting

APP_VERSION = "14.0.0"

DEFAULTS = {
    "general": {"app_url": "", "refresh_sec": 5},
    "smtp": {"host": "", "port": 587, "security": "starttls", "username": "", "password": "", "from_addr": ""},
}


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
