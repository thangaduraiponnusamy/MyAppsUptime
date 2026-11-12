import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, make_url, text
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./myappsuptime.db")
SQLITE_HEADER = b"SQLite format 3\x00"


def _db_password() -> str:
    if os.getenv("DB_PASSWORD_FILE"):
        return Path(os.environ["DB_PASSWORD_FILE"]).read_text().rstrip("\r\n")
    return os.getenv("DB_PASSWORD", "")


DB_PASSWORD = _db_password()


def key_pragma(name: str, password: str) -> str:
    # PRAGMA does not accept bound parameters, so quote the value
    return f"PRAGMA {name} = '" + password.replace("'", "''") + "'"


def sqlcipher():
    try:
        from sqlcipher3 import dbapi2
    except ImportError:
        raise RuntimeError("DB_PASSWORD is set but SQLCipher is not installed (pip install sqlcipher3-binary)") from None
    return dbapi2


def _is_plain(path: Path) -> bool:
    with path.open("rb") as f:
        return f.read(16) == SQLITE_HEADER


def _encrypt_file(path: Path) -> None:
    """Convert a plain SQLite file to SQLCipher in place. The original stays until the copy is complete."""
    tmp = path.with_name(path.name + ".encrypting")
    tmp.unlink(missing_ok=True)
    con = sqlcipher().connect(str(path))
    try:
        con.execute("ATTACH DATABASE ? AS encrypted KEY ?", (str(tmp), DB_PASSWORD))
        con.execute("SELECT sqlcipher_export('encrypted')")
        con.execute("DETACH DATABASE encrypted")
    finally:
        con.close()
    os.replace(tmp, path)
    log.warning("Encrypted database %s with DB_PASSWORD. Delete old plain backups (*.bak) next to it.", path)


def make_engine(url: str):
    if not url.startswith("sqlite"):
        return create_engine(url)
    db = make_url(url).database
    path = Path(db) if db and db != ":memory:" else None
    exists = path is not None and path.is_file() and path.stat().st_size > 0
    if not DB_PASSWORD:
        if exists and not _is_plain(path):
            raise RuntimeError(f"{path} is encrypted. Set DB_PASSWORD or DB_PASSWORD_FILE.")
        return create_engine(url, connect_args={"check_same_thread": False})

    module = sqlcipher()
    if exists and _is_plain(path):
        _encrypt_file(path)
    eng = create_engine(url, module=module, connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect", insert=True)
    def _set_key(dbapi_conn, _record):
        dbapi_conn.execute(key_pragma("key", DB_PASSWORD))

    try:
        with eng.connect() as conn:
            conn.exec_driver_sql("SELECT count(*) FROM sqlite_master")
    except DatabaseError:
        eng.dispose()
        raise RuntimeError(f"Cannot open {path}: wrong DB_PASSWORD") from None
    return eng


engine = make_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def _default_sql(column) -> str:
    default = column.default
    if default is None:
        return ""
    value = default.arg if default.is_scalar else None
    if value is None and default.is_callable:
        try:
            value = default.arg(None)
        except TypeError:
            return ""
    if isinstance(value, bool):
        return f" DEFAULT {int(value)}"
    if isinstance(value, (int, float)):
        return f" DEFAULT {value}"
    if isinstance(value, (list, dict)):
        value = json.dumps(value)
    if isinstance(value, str):
        return " DEFAULT '" + value.replace("'", "''") + "'"
    return ""


# Versioned steps for changes that adding a column cannot cover (renames, data fixes, indexes).
# Append new steps with the next number. Never edit, remove or reorder a step that was released.
MIGRATIONS: list[tuple[int, str, list[str]]] = [
    (1, "index incidents.resolved_at for log retention",
     ["CREATE INDEX IF NOT EXISTS ix_incidents_resolved_at ON incidents (resolved_at)"]),
]


def _backup(eng, version: int) -> None:
    path = eng.url.database
    if eng.dialect.name == "sqlite" and path and path != ":memory:" and Path(path).is_file():
        target = Path(path).with_name(f"{Path(path).name}.v{version}.bak")
        shutil.copy2(path, target)
        log.info("Backed up %s to %s before migrating", path, target)


def _run_migrations(eng, had_tables: bool) -> None:
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations "
                          "(version INTEGER PRIMARY KEY, name VARCHAR(200), applied_at VARCHAR(40))"))
        done = set(conn.scalars(text("SELECT version FROM schema_migrations")))
    pending = [m for m in MIGRATIONS if m[0] not in done]
    if not pending:
        return
    if had_tables:
        _backup(eng, max(done, default=0))
    for version, name, statements in pending:
        with eng.begin() as conn:
            for sql in statements:
                conn.execute(text(sql))
            conn.execute(text("INSERT INTO schema_migrations (version, name, applied_at) VALUES (:v, :n, :t)"),
                         {"v": version, "n": name, "t": datetime.now(timezone.utc).isoformat()})
        log.info("Applied migration %s: %s", version, name)


def _encrypt_existing(eng) -> None:
    """Encrypt secrets that were saved in plain text by older versions. Safe to run every start."""
    from .crypto import PREFIX, encrypt
    with eng.begin() as conn:
        rows = conn.execute(text("SELECT id, url FROM notification_channels "
                                 "WHERE url <> '' AND url NOT LIKE 'enc:%'")).all()
        for row_id, url in rows:
            conn.execute(text("UPDATE notification_channels SET url = :u WHERE id = :i"),
                         {"u": encrypt(url), "i": row_id})
        smtp = conn.scalar(text("SELECT value FROM app_settings WHERE key = 'smtp'"))
        value = json.loads(smtp) if isinstance(smtp, str) else smtp
        password = (value or {}).get("password")
        plain_password = bool(password) and not password.startswith(PREFIX)
        if plain_password:
            value["password"] = encrypt(password)
            conn.execute(text("UPDATE app_settings SET value = :v WHERE key = 'smtp'"), {"v": json.dumps(value)})
        if rows or plain_password:
            log.info("Encrypted stored secrets")


def schema_version(eng=None) -> int:
    with (eng or engine).connect() as conn:
        return conn.scalar(text("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")) or 0


def upgrade_schema(eng=None) -> None:
    """Bring a database up to date: new tables, new columns, then pending versioned migrations."""
    eng = eng or engine
    from . import models  # noqa: F401  registers all tables on Base.metadata
    had_tables = bool(inspect(eng).get_table_names())
    Base.metadata.create_all(eng)
    inspector = inspect(eng)
    with eng.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column.type.compile(eng.dialect)}'
                conn.execute(text(ddl + _default_sql(column)))
                log.info("Added column %s.%s", table.name, column.name)
    _run_migrations(eng, had_tables)
    _encrypt_existing(eng)
