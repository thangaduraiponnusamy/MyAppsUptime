"""Daily copy of every database file (admin + tenants), kept for a number of days."""
import logging
import os
import shutil
from datetime import date, timedelta
from pathlib import Path

from . import tenancy
from .crypto import _key_file
from .database import DATABASE_URL, DB_PASSWORD

log = logging.getLogger(__name__)


def backup_dir() -> Path:
    if os.getenv("BACKUP_DIR"):
        return Path(os.environ["BACKUP_DIR"]).resolve()
    if DATABASE_URL.startswith("sqlite:///"):
        return Path(DATABASE_URL[len("sqlite:///"):]).resolve().parent / "backups"
    return Path("backups").resolve()


def supported() -> bool:
    return DATABASE_URL.startswith("sqlite")


def _copy(engine, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = engine.raw_connection()
    try:
        con = raw.driver_connection
        if DB_PASSWORD:
            con.execute("ATTACH DATABASE ? AS backup KEY ?", (str(target), DB_PASSWORD))
            con.execute("SELECT sqlcipher_export('backup')")
            con.execute("DETACH DATABASE backup")
        else:
            con.execute("VACUUM INTO ?", (str(target),))  # consistent copy while the app keeps running
    finally:
        raw.close()


def run(today: date | None = None) -> Path:
    """Write backups/<YYYY-MM-DD>/ with main.db, tenants/<slug>.db and secret.key."""
    today = today or date.today()
    root = backup_dir()
    folder, partial = root / today.isoformat(), root / f".{today.isoformat()}.partial"
    shutil.rmtree(partial, ignore_errors=True)
    for slug in tenancy.slugs(active_only=False):
        name = "main.db" if slug == tenancy.DEFAULT else f"tenants/{slug}.db"
        try:
            _copy(tenancy.engine_for(slug), partial / name)
        except LookupError:
            log.error("Backup skipped tenant %s: database file missing", slug)
    key = _key_file()
    if key.is_file() and not os.getenv("SECRET_KEY"):
        shutil.copy2(key, partial / "secret.key")  # needed to read saved webhook URLs and SMTP password
    shutil.rmtree(folder, ignore_errors=True)
    partial.rename(folder)
    log.info("Backup written to %s", folder)
    return folder


def prune(keep_days: int, today: date | None = None) -> list[str]:
    """Delete dated backup folders older than keep_days (today counts as day 1)."""
    oldest = (today or date.today()) - timedelta(days=keep_days - 1)
    removed = []
    for item in list_all():
        if date.fromisoformat(item["name"]) < oldest:
            shutil.rmtree(backup_dir() / item["name"], ignore_errors=True)
            removed.append(item["name"])
    return removed


def due(today: date | None = None) -> bool:
    return not (backup_dir() / (today or date.today()).isoformat()).is_dir()


def list_all() -> list[dict]:
    root = backup_dir()
    if not root.is_dir():
        return []
    out = []
    for folder in sorted(root.iterdir(), reverse=True):
        try:
            date.fromisoformat(folder.name)
        except ValueError:
            continue
        size = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
        out.append({"name": folder.name, "size_kb": round(size / 1024)})
    return out
