import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import notifier
from .auth import (RESET_MIN, Principal, bearer, check_lock, clear_fails, create_reset_token, create_session,
                   current_user, end_all_sessions, end_session, hash_password, record_fail, require_admin,
                   require_user, use_reset_token, verify_password)
from .config import APP_VERSION, get_setting, put_setting
from .database import get_db
from .models import Monitor, NotificationChannel, Project, User
from .schemas import (ForgotIn, GeneralSettings, LoginIn, LoginOut, MeOut, PasswordIn, ProjectIn, ProjectOut,
                      ResetIn, SmtpOut, SmtpSettings, SmtpTestIn, UserCreate, UserOut, UserUpdate)

log = logging.getLogger(__name__)

auth = APIRouter(prefix="/api/auth")
admin = APIRouter(prefix="/api")


def _get(db: Session, model, obj_id: int):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__name__} {obj_id} not found")
    return obj


def _me(db: Session, user: Principal) -> MeOut:
    row = db.get(User, user.id) if user.id else None
    return MeOut(id=user.id, username=user.username, role=user.role, name=row.name if row else "",
                 email=row.email if row else None, must_change_password=user.must_change_password,
                 refresh_sec=get_setting(db, "general")["refresh_sec"])


# ---------- sign in ----------

@auth.post("/login", response_model=LoginOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    key = f"{body.username.lower()}|{request.client.host if request.client else ''}"
    check_lock(key)
    user = db.scalar(select(User).where(func.lower(User.username) == body.username.lower()))
    if user is None or not user.active or not verify_password(body.password, user.password_hash):
        record_fail(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong user name or password")
    clear_fails(key)
    token = create_session(db, user)
    me = _me(db, Principal(user.id, user.username, user.role, user.must_change_password))
    return LoginOut(**me.model_dump(), token=token)


@auth.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(authorization: str | None = Header(default=None), db: Session = Depends(get_db)):
    token = bearer(authorization)
    if token:
        end_session(db, token)


@auth.get("/me", response_model=MeOut)
def me(user: Principal = Depends(current_user), db: Session = Depends(get_db)):
    return _me(db, user)


def _email_reset_ready(db: Session) -> bool:
    # The link must use the configured address, never the request Host header
    return notifier.smtp_ready(get_setting(db, "smtp")) and bool(get_setting(db, "general")["app_url"])


@auth.get("/options")
def options(db: Session = Depends(get_db)):
    return {"version": APP_VERSION, "email_reset": _email_reset_ready(db),
            "refresh_sec": get_setting(db, "general")["refresh_sec"]}


@auth.post("/forgot")
async def forgot_password(body: ForgotIn, request: Request, db: Session = Depends(get_db)):
    key = f"forgot|{request.client.host if request.client else ''}"
    check_lock(key)
    record_fail(key)  # counts every request, so this cannot be used to spam mailboxes
    name = body.username.strip().lower()
    user = db.scalar(select(User).where(
        (func.lower(User.username) == name) | (func.lower(User.email) == name), User.active.is_(True)))
    if user and user.email and _email_reset_ready(db):
        token = create_reset_token(db, user)
        link = f"{get_setting(db, 'general')['app_url']}/reset-password?token={token}"
        error = await notifier.send_text(
            get_setting(db, "smtp"), [user.email], "Reset your MyAppsUptime password",
            f"Hello {user.name or user.username},\n\nUse this link to set a new password. "
            f"It works once and expires in {RESET_MIN} minutes:\n\n{link}\n\n"
            "If you did not ask for this, you can ignore this email.")
        if error:
            log.warning("Reset email for %s failed: %s", user.username, error)
    return {"ok": True}


@auth.post("/reset")
def reset_password(body: ResetIn, db: Session = Depends(get_db)):
    user = use_reset_token(db, body.token)
    if user is None:
        db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This link is invalid or has expired")
    user.password_hash = hash_password(body.new)
    user.must_change_password = False
    end_all_sessions(db, user.id)
    db.commit()
    return {"ok": True}


@auth.post("/password", response_model=MeOut)
def change_password(body: PasswordIn, user: Principal = Depends(current_user), db: Session = Depends(get_db)):
    row = db.get(User, user.id) if user.id else None
    if row is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This account has no password")
    if not verify_password(body.current, row.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is wrong")
    if body.new == body.current:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a new password")
    row.password_hash = hash_password(body.new)
    row.must_change_password = False
    db.commit()
    user.must_change_password = False
    return _me(db, user)


# ---------- users ----------

def _active_admins(db: Session, exclude: int) -> int:
    return db.scalar(select(func.count()).select_from(User).where(
        User.role == "admin", User.active.is_(True), User.id != exclude)) or 0


def _unique_username(db: Session, username: str, self_id: int | None) -> None:
    other = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if other and other.id != self_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "User name already exists")


@admin.get("/users", response_model=list[UserOut], dependencies=[Depends(require_admin)])
def list_users(db: Session = Depends(get_db)):
    return db.scalars(select(User).order_by(User.username)).all()


@admin.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_admin)])
def create_user(body: UserCreate, db: Session = Depends(get_db)):
    _unique_username(db, body.username, None)
    user = User(**body.model_dump(exclude={"password"}), password_hash=hash_password(body.password),
                must_change_password=True)
    db.add(user)
    db.commit()
    return user


@admin.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, db: Session = Depends(get_db), me: Principal = Depends(require_admin)):
    user = _get(db, User, user_id)
    _unique_username(db, body.username, user.id)
    losing_admin = user.role == "admin" and (body.role != "admin" or not body.active)
    if losing_admin and _active_admins(db, user.id) == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "At least one active admin is required")
    for key, value in body.model_dump(exclude={"password"}).items():
        setattr(user, key, value)
    if body.password:
        user.password_hash = hash_password(body.password)
        user.must_change_password = user.id != me.id
    if body.password or not body.active:
        end_all_sessions(db, user.id)
    db.commit()
    return user


@admin.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: Session = Depends(get_db), me: Principal = Depends(require_admin)):
    user = _get(db, User, user_id)
    if user.id == me.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot delete yourself")
    if user.role == "admin" and _active_admins(db, user.id) == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "At least one active admin is required")
    end_all_sessions(db, user.id)
    for project in db.scalars(select(Project)).all():
        if user.id in (project.member_ids or []):
            project.member_ids = [i for i in project.member_ids if i != user.id]
    db.delete(user)
    db.commit()


# ---------- projects ----------

def _project_out(db: Session, project: Project) -> ProjectOut:
    out = ProjectOut.model_validate(project)
    rows = db.execute(select(Monitor.status, func.count()).where(
        Monitor.project_id == project.id).group_by(Monitor.status)).all()
    out.monitor_count = sum(c for _, c in rows)
    out.down_count = sum(c for s, c in rows if s == "down")
    return out


def _check_members(db: Session, member_ids: list[int]) -> list[int]:
    ids = sorted(set(member_ids))
    found = set(db.scalars(select(User.id).where(User.id.in_(ids))).all())
    if found != set(ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown user in members")
    return ids


@admin.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), user: Principal = Depends(require_user)):
    projects = db.scalars(select(Project).order_by(Project.name)).all()
    if not user.is_admin:
        projects = [p for p in projects if user.id in (p.member_ids or [])]
    return [_project_out(db, p) for p in projects]


@admin.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED,
            dependencies=[Depends(require_admin)])
def create_project(body: ProjectIn, db: Session = Depends(get_db)):
    if db.scalar(select(Project).where(Project.name == body.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Project name already exists")
    project = Project(**body.model_dump(exclude={"member_ids"}), member_ids=_check_members(db, body.member_ids))
    db.add(project)
    db.commit()
    return _project_out(db, project)


@admin.put("/projects/{project_id}", response_model=ProjectOut, dependencies=[Depends(require_admin)])
def update_project(project_id: int, body: ProjectIn, db: Session = Depends(get_db)):
    project = _get(db, Project, project_id)
    other = db.scalar(select(Project).where(Project.name == body.name))
    if other and other.id != project.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Project name already exists")
    project.name, project.description, project.email_members = body.name, body.description, body.email_members
    project.member_ids = _check_members(db, body.member_ids)
    db.commit()
    return _project_out(db, project)


@admin.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = _get(db, Project, project_id)
    for m in db.scalars(select(Monitor).where(Monitor.project_id == project.id)).all():
        m.project_id = None
    for channel in db.scalars(select(NotificationChannel)).all():
        if project.id in (channel.project_ids or []):
            channel.project_ids = [i for i in channel.project_ids if i != project.id]
            if not channel.project_ids:
                channel.active = False  # an empty list would mean "all projects"
    db.delete(project)
    db.commit()


# ---------- settings ----------

@admin.get("/settings/general", response_model=GeneralSettings)
def general_settings(db: Session = Depends(get_db), _: Principal = Depends(require_user)):
    return get_setting(db, "general")


@admin.put("/settings/general", response_model=GeneralSettings, dependencies=[Depends(require_admin)])
def save_general(body: GeneralSettings, db: Session = Depends(get_db)):
    return put_setting(db, "general", body.model_dump())


def _smtp_out(value: dict) -> SmtpOut:
    return SmtpOut(**{k: value[k] for k in ("host", "port", "security", "username", "from_addr")},
                   has_password=bool(value.get("password")))


@admin.get("/settings/smtp", response_model=SmtpOut, dependencies=[Depends(require_admin)])
def smtp_settings(db: Session = Depends(get_db)):
    return _smtp_out(get_setting(db, "smtp"))


@admin.put("/settings/smtp", response_model=SmtpOut, dependencies=[Depends(require_admin)])
def save_smtp(body: SmtpSettings, db: Session = Depends(get_db)):
    data = body.model_dump()
    if data["password"] is None:
        data.pop("password")  # keep the saved one
    return _smtp_out(put_setting(db, "smtp", data))


@admin.post("/settings/smtp/test", dependencies=[Depends(require_admin)])
async def test_smtp(body: SmtpTestIn, db: Session = Depends(get_db)):
    alert = notifier.Alert(monitor="Test monitor", status="up", message="Email alerts are working",
                           project="Test", link=get_setting(db, "general")["app_url"] or None)
    error = await notifier.send_email(get_setting(db, "smtp"), alert, [body.to])
    if error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, error)
    return {"sent": True}
