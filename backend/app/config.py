from sqlalchemy.orm import Session

from .crypto import decrypt, encrypt
from .models import AppSetting
from .timeutil import APP_VERSION  # noqa: F401  (re-exported)

DEFAULTS = {
    "general": {"app_url": "", "timezone": "", "refresh_sec": 5, "retention_value": 1, "retention_unit": "years",
                "down_after_checks": 3, "expiry_warn_days": [30, 14, 7], "domain_expiry": True},
    "smtp": {"host": "", "port": 587, "security": "starttls", "username": "", "password": "", "from_addr": ""},
    # whole server; stored in the admin tenant only
    "server": {"backup_enabled": True, "backup_keep_days": 7, "dns_cache": True},
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
