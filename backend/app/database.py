import json
import logging
import os

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


def upgrade_schema() -> None:
    """Create new tables and add columns that newer versions introduced, so upgrades need no manual steps."""
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            existing = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column.type.compile(engine.dialect)}'
                conn.execute(text(ddl + _default_sql(column)))
                log.info("Added column %s.%s", table.name, column.name)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
