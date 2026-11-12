"""One database file per tenant. The default tenant "admin" uses DATABASE_URL and holds the tenant list."""
import logging
import os
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .database import DATABASE_URL, SessionLocal, make_engine, upgrade_schema
from .models import Tenant

log = logging.getLogger(__name__)

DEFAULT = "admin"
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{1,29}$")


def _data_dir() -> Path:
    if os.getenv("TENANT_DATA_DIR"):
        return Path(os.environ["TENANT_DATA_DIR"]).resolve()
    if DATABASE_URL.startswith("sqlite:///"):
        return Path(DATABASE_URL[len("sqlite:///"):]).resolve().parent / "tenants"
    return Path("tenants").resolve()


DATA_DIR = _data_dir()
_makers: dict[str, sessionmaker] = {DEFAULT: SessionLocal}
_registry: dict[str, Tenant] = {}
_lock = threading.Lock()


def db_path(slug: str) -> Path:
    return DATA_DIR / f"{slug}.db"


def _maker(slug: str, create: bool = False) -> sessionmaker:
    maker = _makers.get(slug)
    if maker is None:
        with _lock:
            maker = _makers.get(slug)
            if maker is None:
                # only create_tenant may make a new file; stops deleted tenants coming back
                if not SLUG.match(slug) or not (create or db_path(slug).exists()):
                    raise LookupError(f"Unknown tenant: {slug}")
                DATA_DIR.mkdir(parents=True, exist_ok=True)
                engine = make_engine(f"sqlite:///{db_path(slug).as_posix()}")
                upgrade_schema(engine)
                maker = _makers[slug] = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return maker


def session_for(slug: str, create: bool = False) -> Session:
    db = _maker(slug, create)()
    db.info["tenant"] = slug
    return db


def engine_for(slug: str):
    return _maker(slug).kw["bind"]


def tenant_of(db: Session) -> str:
    return db.info.get("tenant", DEFAULT)


# ---------- registry (cached copy of the tenants table) ----------

def load() -> None:
    with session_for(DEFAULT) as db:
        if db.scalar(select(Tenant).where(Tenant.slug == DEFAULT)) is None:
            db.add(Tenant(slug=DEFAULT, name="Admin"))
            db.commit()
        rows = db.scalars(select(Tenant)).all()
        db.expunge_all()
    _registry.clear()
    _registry.update({t.slug: t for t in rows})


def get(slug: str) -> Tenant | None:
    return _registry.get(slug)


def name(slug: str) -> str:
    t = _registry.get(slug)
    return t.name if t else slug


def exists(slug: str) -> bool:
    return slug in _registry


def is_active(slug: str) -> bool:
    t = _registry.get(slug)
    return t is not None and t.active


def slugs(active_only: bool = True) -> list[str]:
    """Default tenant first."""
    found = [s for s, t in _registry.items() if s != DEFAULT and (t.active or not active_only)]
    return [DEFAULT, *sorted(found)]


def archive(slug: str) -> Path | None:
    """Close the tenant database and move its file to tenants/deleted (kept as a backup)."""
    with _lock:
        maker = _makers.pop(slug, None)
        if maker is not None:
            maker.kw["bind"].dispose()
    src = db_path(slug)
    if not src.exists():
        return None
    target = DATA_DIR / "deleted" / f"{slug}-{datetime.now():%Y%m%d-%H%M%S}.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), target)
    log.info("Tenant %s archived to %s", slug, target)
    return target
