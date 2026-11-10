"""Tenant management for the super admin. Each tenant has its own database file and one admin."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import tenancy
from .admin_api import _unique_username
from .auth import end_all_sessions, hash_password, require_super
from .database import DATABASE_URL
from .models import Monitor, Project, Tenant, User
from .schemas import TenantCreate, TenantOut, TenantUpdate

router = APIRouter(prefix="/api/tenants", dependencies=[Depends(require_super)])


class ResetAdminIn(BaseModel):
    password: str = Field(min_length=8, max_length=200)


def _db_file(slug: str) -> Path | None:
    if slug != tenancy.DEFAULT:
        return tenancy.db_path(slug)
    return Path(DATABASE_URL[len("sqlite:///"):]).resolve() if DATABASE_URL.startswith("sqlite:///") else None


def _count(db: Session, model, *where) -> int:
    return db.scalar(select(func.count()).select_from(model).where(*where)) or 0


def _out(t: Tenant) -> TenantOut:
    with tenancy.session_for(t.slug) as db:
        admin_role = "superadmin" if t.slug == tenancy.DEFAULT else "admin"
        admins = db.scalars(select(User.username).where(User.role == admin_role).order_by(User.username)).all()
        users, teams, monitors = _count(db, User), _count(db, Project), _count(db, Monitor)
        down = _count(db, Monitor, Monitor.status == "down")
    path = _db_file(t.slug)
    return TenantOut(slug=t.slug, name=t.name, active=t.active, is_default=t.slug == tenancy.DEFAULT,
                     created_at=t.created_at, admins=list(admins), users=users, teams=teams, monitors=monitors,
                     down=down, db_file=path.name if path else "database server",
                     db_size_kb=round(path.stat().st_size / 1024) if path and path.exists() else 0)


def _get(db: Session, slug: str) -> Tenant:
    t = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if t is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Tenant {slug} not found")
    return t


@router.get("", response_model=list[TenantOut])
def list_tenants():
    with tenancy.session_for(tenancy.DEFAULT) as db:
        rows = db.scalars(select(Tenant)).all()
    rows = sorted(rows, key=lambda t: (t.slug != tenancy.DEFAULT, t.name.lower()))
    return [_out(t) for t in rows]


@router.post("", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
def create_tenant(body: TenantCreate):
    with tenancy.session_for(tenancy.DEFAULT) as db:
        if db.scalar(select(Tenant).where(Tenant.slug == body.slug)) or tenancy.db_path(body.slug).exists():
            raise HTTPException(status.HTTP_409_CONFLICT, "Tenant ID already exists")
        _unique_username(db, body.admin_username, None)
    with tenancy.session_for(body.slug) as tdb:  # creates the database file
        tdb.add(User(username=body.admin_username, name="Tenant admin", email=body.admin_email, role="admin",
                     password_hash=hash_password(body.admin_password), must_change_password=True))
        tdb.commit()
    with tenancy.session_for(tenancy.DEFAULT) as db:
        t = Tenant(slug=body.slug, name=body.name.strip())
        db.add(t)
        db.commit()
    tenancy.load()
    return _out(t)


@router.put("/{slug}", response_model=TenantOut)
def update_tenant(slug: str, body: TenantUpdate):
    with tenancy.session_for(tenancy.DEFAULT) as db:
        t = _get(db, slug)
        if t.slug == tenancy.DEFAULT and not body.active:
            raise HTTPException(status.HTTP_409_CONFLICT, "The admin tenant cannot be disabled")
        t.name, t.active = body.name.strip(), body.active
        db.commit()
    tenancy.load()
    return _out(t)


@router.post("/{slug}/reset-admin")
def reset_admin(slug: str, body: ResetAdminIn):
    if slug == tenancy.DEFAULT or not tenancy.exists(slug):
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Tenant {slug} not found")
    with tenancy.session_for(slug) as db:
        admin = db.scalar(select(User).where(User.role == "admin"))
        if admin is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "This tenant has no admin")
        admin.password_hash = hash_password(body.password)
        admin.must_change_password = True
        admin.active = True
        end_all_sessions(db, admin.id)
        db.commit()
        return {"username": admin.username}


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tenant(slug: str):
    if slug == tenancy.DEFAULT:
        raise HTTPException(status.HTTP_409_CONFLICT, "The admin tenant cannot be deleted")
    with tenancy.session_for(tenancy.DEFAULT) as db:
        db.delete(_get(db, slug))
        db.commit()
    tenancy.load()
    tenancy.archive(slug)
