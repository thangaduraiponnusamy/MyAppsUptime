import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

log = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./myappsuptime.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
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
