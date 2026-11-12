from sqlalchemy.orm import Session

from .crypto import decrypt, encrypt
from .models import AppSetting

APP_VERSION = "16.0.0"

DEFAULTS = {
    "general": {"app_url": "", "timezone": "", "refresh_sec": 5, "retention_value": 1, "retention_unit": "years"},
    "smtp": {"host": "", "port": 587, "security": "starttls", "username": "", "password": "", "from_addr": ""},
}

UNIT_DAYS = {"days": 1, "months": 30, "years": 365}

# Setting fields stored encrypted
SECRETS = {"smtp": ("password",)}


def retention_days(general: dict) -> int:
    return int(general["retention_value"]) * UNIT_DAYS.get(general["retention_unit"], 365)


def get_setting(db: Session, key: str) -> dict:
    row = db.get(AppSetting, key)
    value = {**DEFAULTS[key], **(row.value if row else {})}
    for field in SECRETS.get(key, ()):
        value[field] = decrypt(value[field])
    return value


def put_setting(db: Session, key: str, value: dict) -> dict:
    row = db.get(AppSetting, key)
    merged = {**get_setting(db, key), **value}
    stored = {**merged, **{f: encrypt(merged[f]) for f in SECRETS.get(key, ())}}
    if row is None:
        db.add(AppSetting(key=key, value=stored))
    else:
        row.value = stored
    db.commit()
    return merged
