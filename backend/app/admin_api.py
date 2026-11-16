import asyncio
import csv
import io
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from . import audit as audit_log
from . import backup, dnscache, notifier, tenancy
from .api import DEFAULT_PROJECT, default_project
from .auth import (ACCOUNT_LOCK_AFTER, ACCOUNT_LOCK_MIN, LOGIN_IP_PER_MIN, LOGIN_USER_PER_MIN, RESET_MIN, Principal,
                   bearer, check_lock, client_ip, create_reset_token, create_session, current_user,
                   end_all_sessions, end_session, get_db, hash_password, rate_limit, record_fail,
                   require_admin, require_super, require_user, token_tenant, use_reset_token, verify_password,
                   visible_monitor_ids)
from .config import APP_VERSION, get_setting, put_setting
from .models import (AlertEvent, AuditEvent, AuthToken, Heartbeat, Incident, IncidentAck, LoginEvent, Monitor,
                     NotificationChannel, Project, User, aware, utcnow)
from .schemas import (ALERT_EMAIL, AlertChannelsIn, AlertOption, AuditEventOut, DatabaseInfo, ForgotIn, GeneralSettings, LoginEventOut, LoginIn, LoginOut, MeOut, PasswordIn,
                      ProjectIn, ProjectOut, ResetIn, ServerOut, ServerSettings, SessionOut, SmtpOut, SmtpSettings,
                      SmtpTestIn, UserCreate, UserOut, UserUpdate)

log = logging.getLogger(__name__)

auth = APIRouter(prefix="/api/auth")
admin = APIRouter(prefix="/api")


def _get(db: Session, model, obj_id: int):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__name__} {obj_id} not found")
    return obj


def _alert_options(db: Session) -> list[AlertOption]:
    """Ways a user of this tenant can get alerts: email plus the tenant's active channels."""
    channels = db.scalars(select(NotificationChannel).where(NotificationChannel.active.is_(True))
                          .order_by(NotificationChannel.name)).all()
    return [AlertOption(id=ALERT_EMAIL, name="Email")] + [AlertOption(id=c.id, name=c.name) for c in channels]


def _alert_channels(db: Session, picked: list[int]) -> list[int] | None:
    """None when everything is picked, so channels added later are included too."""
    ids = [o.id for o in _alert_options(db)]
    return None if set(ids) <= set(picked) else [i for i in ids if i in picked]


def _me(db: Session, user: Principal) -> MeOut:
    """db is the tenant being worked on; the account itself lives in the user's home tenant."""
    with tenancy.session_for(user.tenant) as home:
        row = home.get(User, user.id) if user.id else None
        name, email = (row.name, row.email) if row else ("", None)
        picked = row.alert_channels if row else None
        options = _alert_options(home)
    return MeOut(id=user.id, username=user.username, role=user.role, name=name, email=email,
                 must_change_password=user.must_change_password, alert_channels=picked, alert_options=options,
                 refresh_sec=get_setting(db, "general")["refresh_sec"],
                 tenant=user.tenant, tenant_name=tenancy.name(user.tenant),
                 active_tenant=user.active, active_tenant_name=tenancy.name(user.active))


# ---------- sign in ----------

# Short label like "Chrome on Android"; the full user agent is not stored
def describe_agent(ua: str) -> str:
    if not ua:
        return ""
    os = next((name for key, name in (
        ("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"), ("CrOS", "ChromeOS"),
        ("Windows", "Windows"), ("Macintosh", "macOS"), ("Linux", "Linux"),
    ) if key in ua), "")
    browser = next((name for key, name in (
        ("Edg", "Edge"), ("OPR/", "Opera"), ("SamsungBrowser", "Samsung Internet"), ("Firefox/", "Firefox"),
        ("FxiOS", "Firefox"), ("CriOS", "Chrome"), ("Chrome/", "Chrome"), ("Safari/", "Safari"),
        ("curl/", "curl"), ("python", "Script"),
    ) if key in ua), "Other")
    return f"{browser} on {os}" if os else browser


@auth.post("/login", response_model=LoginOut)
def login(body: LoginIn, request: Request):
    ip = client_ip(request)
    name = body.username.strip().lower()
    device = describe_agent(request.headers.get("user-agent", ""))[:80]
    rate_limit(f"ip|{ip}", LOGIN_IP_PER_MIN)
    rate_limit(f"user|{name}", LOGIN_USER_PER_MIN)
    # User names are unique across tenants, so the first match is the account
    for slug in tenancy.slugs(active_only=False):
        with tenancy.session_for(slug) as db:
            user = db.scalar(select(User).where(func.lower(User.username) == name))
            if user is None:
                continue
            now = utcnow()
            locked = user.locked_until is not None and aware(user.locked_until) > now
            # A locked account gets the same answer as a wrong password
            if locked or not user.active or not verify_password(body.password, user.password_hash):
                db.add(LoginEvent(user_id=user.id, username=user.username, ip=ip, device=device, success=False))
                audit_log.record(db, request, user.username, "sign-in failed", "account", user.username,
                                 "account is locked" if locked else "", user.id)
                if not locked and user.active:
                    user.failed_logins = (user.failed_logins or 0) + 1
                    if user.failed_logins >= ACCOUNT_LOCK_AFTER:
                        user.failed_logins, user.locked_until = 0, now + timedelta(minutes=ACCOUNT_LOCK_MIN)
                        audit_log.record(db, request, user.username, "locked out", "account", user.username,
                                         f"{ACCOUNT_LOCK_MIN} min after {ACCOUNT_LOCK_AFTER} failed sign-ins", user.id)
                db.commit()
                break
            if not tenancy.is_active(slug):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "Your organization is disabled. Contact the administrator.")
            user.last_login_at = now
            user.failed_logins, user.locked_until = 0, None
            db.add(LoginEvent(user_id=user.id, username=user.username, ip=ip, device=device, success=True))
            audit_log.record(db, request, user.username, "signed in", "account", user.username, device, user.id)
            token = create_session(db, user, ip, device)
            me = _me(db, Principal(user.id, user.username, user.role, user.must_change_password, slug, slug))
            return LoginOut(**me.model_dump(), token=token)
    else:
        with tenancy.session_for(tenancy.DEFAULT) as db:
            audit_log.record(db, request, body.username, "sign-in failed", "account", body.username, "unknown user")
            db.commit()
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong user name or password")


# ---------- active sessions of the signed-in user ----------

SESSION_ID = re.compile(r"^[0-9a-f]{16}$")


def _my_sessions(user: Principal):
    return select(AuthToken).where(AuthToken.user_id == user.id, AuthToken.kind == "session",
                                   AuthToken.expires_at > utcnow())


@auth.get("/sessions", response_model=list[SessionOut])
def list_sessions(user: Principal = Depends(current_user)):
    if not user.session:
        return []
    with tenancy.session_for(user.tenant) as home:
        rows = home.scalars(_my_sessions(user)).all()
        out = [SessionOut(id=r.token_hash[:16], device=r.device or "", ip=r.ip or "", created_at=r.created_at,
                          last_used_at=r.last_used_at, current=r.token_hash == user.session) for r in rows]
    out.sort(key=lambda s: str(s.last_used_at or s.created_at or ""), reverse=True)
    return sorted(out, key=lambda s: not s.current)


@auth.delete("/sessions/{sid}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(sid: str, request: Request, user: Principal = Depends(current_user)):
    if not SESSION_ID.match(sid) or not user.session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    with tenancy.session_for(user.tenant) as home:
        row = home.scalar(_my_sessions(user).where(AuthToken.token_hash.startswith(sid)))
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
        audit_log.record(home, request, user.username, "session revoked", "account", user.username,
                         " ".join(filter(None, (row.device, row.ip))), sid)
        home.delete(row)
        home.commit()


@auth.post("/sessions/others")
def revoke_other_sessions(request: Request, user: Principal = Depends(current_user)):
    """Sign out everywhere except here."""
    if not user.session:
        return {"revoked": 0}
    with tenancy.session_for(user.tenant) as home:
        rows = home.scalars(_my_sessions(user).where(AuthToken.token_hash != user.session)).all()
        for row in rows:
            home.delete(row)
        audit_log.record(home, request, user.username, "sessions revoked", "account", user.username,
                         f"{len(rows)} other sessions")
        home.commit()
    return {"revoked": len(rows)}


@auth.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(authorization: str | None = Header(default=None)):
    token = bearer(authorization)
    if token and tenancy.exists(token_tenant(token)):
        with tenancy.session_for(token_tenant(token)) as db:
            end_session(db, token)


@auth.get("/me", response_model=MeOut)
def me(user: Principal = Depends(current_user), db: Session = Depends(get_db)):
    return _me(db, user)


def _email_reset_ready(db: Session) -> bool:
    # The link must use the configured address, never the request Host header
    return notifier.smtp_ready(get_setting(db, "smtp")) and bool(notifier.server_url(get_setting(db, "general")))


@auth.get("/options")
def options(db: Session = Depends(get_db)):
    return {"version": APP_VERSION, "email_reset": _email_reset_ready(db),
            "refresh_sec": get_setting(db, "general")["refresh_sec"]}


@auth.post("/forgot")
async def forgot_password(body: ForgotIn, request: Request):
    key = f"forgot|{client_ip(request)}"
    check_lock(key)
    record_fail(key)  # counts every request, so this cannot be used to spam mailboxes
    name = body.username.strip().lower()
    for slug in tenancy.slugs():
        with tenancy.session_for(slug) as db:
            user = db.scalar(select(User).where(
                (func.lower(User.username) == name) | (func.lower(User.email) == name), User.active.is_(True)))
            if user is None:
                continue
            # A tenant without its own mail server uses the one of the admin tenant
            config = next((c for c in (slug, tenancy.DEFAULT) if _mail_ready(c)), None)
            if user.email and config:
                with tenancy.session_for(config) as cfg:
                    smtp, app_url = get_setting(cfg, "smtp"), notifier.server_url(get_setting(cfg, "general"))
                token = create_reset_token(db, user)
                error = await notifier.send_text(
                    smtp, [user.email], "Reset your MyAppsUptime password",
                    f"Hello {user.name or user.username},\n\nUse this link to set a new password. "
                    f"It works once and expires in {RESET_MIN} minutes:\n\n{app_url}/reset-password?token={token}\n\n"
                    "If you did not ask for this, you can ignore this email.")
                if error:
                    log.warning("Reset email for %s failed: %s", user.username, error)
            break
    return {"ok": True}


def _mail_ready(slug: str) -> bool:
    with tenancy.session_for(slug) as db:
        return _email_reset_ready(db)


@auth.post("/reset")
def reset_password(body: ResetIn):
    slug = token_tenant(body.token)
    if not tenancy.is_active(slug):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "This link is invalid or has expired")
    with tenancy.session_for(slug) as db:
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
    with tenancy.session_for(user.tenant) as home:
        row = home.get(User, user.id) if user.id else None
        if row is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This account has no password")
        if not verify_password(body.current, row.password_hash):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is wrong")
        if body.new == body.current:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a new password")
        row.password_hash = hash_password(body.new)
        row.must_change_password = False
        home.commit()
    user.must_change_password = False
    return _me(db, user)


@auth.put("/alerts", response_model=MeOut)
def my_alerts(body: AlertChannelsIn, user: Principal = Depends(current_user), db: Session = Depends(get_db)):
    """How the signed-in user wants to get alerts (email, Telegram, ...)."""
    with tenancy.session_for(user.tenant) as home:
        row = home.get(User, user.id) if user.id else None
        if row is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "This account has no settings")
        row.alert_channels = _alert_channels(home, body.alert_channels)
        home.commit()
    return _me(db, user)


# ---------- users ----------

def _active_admins(db: Session, exclude: int) -> int:
    return db.scalar(select(func.count()).select_from(User).where(
        User.role.in_(("admin", "superadmin")), User.active.is_(True), User.id != exclude)) or 0


def _unique_username(db: Session, username: str, self_id: int | None) -> None:
    """User names are unique across all tenants, so sign-in knows which tenant to use."""
    here = tenancy.tenant_of(db)
    query = select(User).where(func.lower(User.username) == username.lower())
    for slug in tenancy.slugs(active_only=False):
        if slug == here:
            other = db.scalar(query)
            taken = other is not None and other.id != self_id
        else:
            with tenancy.session_for(slug) as other_db:
                taken = other_db.scalar(query) is not None
        if taken:
            raise HTTPException(status.HTTP_409_CONFLICT, "User name already exists")


def _check_role(db: Session, me: Principal, role: str, user: User | None) -> None:
    if user is not None and user.role == role:
        return
    if tenancy.tenant_of(db) == tenancy.DEFAULT:
        if role == "admin":
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "The admin tenant has super admins and members only")
        if role == "superadmin" and not me.is_super:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a super admin can add super admins")
        return
    if role == "superadmin":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Super admins belong to the admin tenant only")


@admin.get("/users", response_model=list[UserOut], dependencies=[Depends(require_admin)])
def list_users(db: Session = Depends(get_db)):
    projects = db.scalars(select(Project)).all()
    return [_user_out(u, projects) for u in db.scalars(select(User).order_by(User.username)).all()]


def _user_out(user: User, projects) -> UserOut:
    out = UserOut.model_validate(user)
    out.project_ids = [p.id for p in projects if user.id in (p.member_ids or [])]
    out.monitor_ids = user.monitor_ids or []
    return out


def _set_access(db: Session, user: User, project_ids: list[int] | None, monitor_ids: list[int] | None) -> None:
    """Projects the user is a member of, plus single services from other projects."""
    if project_ids is not None:
        wanted = set(project_ids)
        projects = db.scalars(select(Project)).all()
        if not wanted <= {p.id for p in projects}:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown project")
        for p in projects:
            members = set(p.member_ids or [])
            if (user.id in members) != (p.id in wanted):
                p.member_ids = sorted(members ^ {user.id})
    if monitor_ids is not None:
        ids = sorted(set(monitor_ids))
        if len(db.scalars(select(Monitor.id).where(Monitor.id.in_(ids))).all()) != len(ids):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown service")
        user.monitor_ids = ids


@admin.get("/logins", response_model=list[LoginEventOut])
def list_logins(limit: int = 20, db: Session = Depends(get_db), me: Principal = Depends(require_user)):
    """Sign-in history: admins see everyone in the tenant, members only themselves."""
    query = select(LoginEvent).order_by(LoginEvent.created_at.desc(), LoginEvent.id.desc())
    if not me.is_admin:
        query = query.where(LoginEvent.user_id == me.id, LoginEvent.username == me.username)
    return db.scalars(query.limit(max(1, min(limit, 500)))).all()


def _audit_query(q: str, action: str, kind: str, username: str, since: datetime | None, until: datetime | None):
    query = select(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.where(AuditEvent.username.ilike(like) | AuditEvent.target.ilike(like) |
                            AuditEvent.kind.ilike(like) | AuditEvent.action.ilike(like) | AuditEvent.ip.ilike(like))
    for column, value in ((AuditEvent.action, action), (AuditEvent.kind, kind), (AuditEvent.username, username)):
        if value.strip():
            query = query.where(func.lower(column) == value.strip().lower())
    if since:
        query = query.where(AuditEvent.created_at >= since)
    if until:
        query = query.where(AuditEvent.created_at < until)
    return query


@admin.get("/audit", response_model=list[AuditEventOut], dependencies=[Depends(require_admin)])
def list_audit(limit: int = 50, offset: int = 0, q: str = "", action: str = "", kind: str = "", username: str = "",
               since: datetime | None = None, until: datetime | None = None, db: Session = Depends(get_db)):
    """Who created, changed, paused or deleted what, newest first."""
    query = _audit_query(q, action, kind, username, since, until)
    return db.scalars(query.offset(max(0, offset)).limit(max(1, min(limit, 1000)))).all()


def _csv_cell(value) -> str:
    text = "" if value is None else str(value)
    # Spreadsheets run cells that start like a formula
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


@admin.get("/audit/export", dependencies=[Depends(require_admin)])
def export_audit(q: str = "", action: str = "", kind: str = "", username: str = "", since: datetime | None = None,
                 until: datetime | None = None, db: Session = Depends(get_db)):
    out = io.StringIO()
    writer = csv.writer(out)
    fields = ("created_at", "tenant", "username", "action", "kind", "target", "object_id", "detail", "changes", "ip",
              "user_agent")
    writer.writerow(fields)
    for row in db.scalars(_audit_query(q, action, kind, username, since, until).limit(100000)):
        writer.writerow([_csv_cell(getattr(row, f)) for f in fields])
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'})


@admin.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, db: Session = Depends(get_db), me: Principal = Depends(require_admin)):
    _unique_username(db, body.username, None)
    _check_role(db, me, body.role, None)
    user = User(**body.model_dump(exclude={"password", "project_ids", "monitor_ids", "alert_channels"}),
                password_hash=hash_password(body.password), must_change_password=True)
    if body.alert_channels is not None:
        user.alert_channels = _alert_channels(db, body.alert_channels)
    db.add(user)
    db.flush()
    _set_access(db, user, body.project_ids, body.monitor_ids)
    db.commit()
    return _user_out(user, db.scalars(select(Project)).all())


@admin.put("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, db: Session = Depends(get_db), me: Principal = Depends(require_admin)):
    user = _get(db, User, user_id)
    _unique_username(db, body.username, user.id)
    _check_role(db, me, body.role, user)
    if user.role == "superadmin" and not me.is_super:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only a super admin can change a super admin")
    losing_admin = user.role in ("admin", "superadmin") and (body.role != user.role or not body.active)
    if losing_admin and _active_admins(db, user.id) == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "At least one active admin is required")
    for key, value in body.model_dump(exclude={"password", "project_ids", "monitor_ids", "alert_channels"}).items():
        setattr(user, key, value)
    if body.alert_channels is not None:
        user.alert_channels = _alert_channels(db, body.alert_channels)
    _set_access(db, user, body.project_ids, body.monitor_ids)
    if body.password:
        user.password_hash = hash_password(body.password)
        user.must_change_password = not me.is_self(user.id)
    if body.password or not body.active:
        end_all_sessions(db, user.id)
    db.commit()
    return _user_out(user, db.scalars(select(Project)).all())


@admin.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(user_id: int, db: Session = Depends(get_db), me: Principal = Depends(require_admin)):
    user = _get(db, User, user_id)
    if me.is_self(user.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "You cannot delete yourself")
    if user.role in ("admin", "superadmin") and _active_admins(db, user.id) == 0:
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


def _check_channels(db: Session, channel_ids: list[int] | None) -> list[int] | None:
    if channel_ids is None:
        return None
    ids = sorted(set(channel_ids))
    found = set(db.scalars(select(NotificationChannel.id).where(NotificationChannel.id.in_(ids))).all())
    if found != set(ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unknown notification channel")
    return ids


@admin.get("/projects", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), user: Principal = Depends(require_user)):
    projects = db.scalars(select(Project).order_by(Project.name)).all()
    if not user.is_admin:
        visible = visible_monitor_ids(db, user) or set()
        shown = set(db.scalars(select(Monitor.project_id).where(Monitor.id.in_(visible))).all())
        projects = [p for p in projects if user.id in (p.member_ids or []) or p.id in shown]
    return [_project_out(db, p) for p in projects]


@admin.post("/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED,
            dependencies=[Depends(require_admin)])
def create_project(body: ProjectIn, db: Session = Depends(get_db)):
    if db.scalar(select(Project).where(Project.name == body.name)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Project name already exists")
    project = Project(**body.model_dump(exclude={"member_ids", "channel_ids"}), member_ids=_check_members(db, body.member_ids),
                      channel_ids=_check_channels(db, body.channel_ids))
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
    project.channel_ids = _check_channels(db, body.channel_ids)
    db.commit()
    return _project_out(db, project)


@admin.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
def delete_project(project_id: int, db: Session = Depends(get_db)):
    project = _get(db, Project, project_id)
    monitors = db.scalars(select(Monitor).where(Monitor.project_id == project.id)).all()
    if monitors and project.name == DEFAULT_PROJECT:
        raise HTTPException(status.HTTP_409_CONFLICT, "Move the services of this project to another project first")
    if monitors:
        general = default_project(db)
        for m in monitors:
            m.project_id = general
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


@admin.get("/settings/defaults", dependencies=[Depends(require_admin)])
def admin_defaults(db: Session = Depends(get_db)):
    """Admin tenant values used when this tenant leaves a setting empty."""
    if tenancy.tenant_of(db) == tenancy.DEFAULT:
        return {"app_url": "", "timezone": "", "smtp": False}
    with tenancy.session_for(tenancy.DEFAULT) as base:
        general = get_setting(base, "general")
        return {"app_url": general["app_url"], "timezone": general["timezone"],
                "smtp": notifier.smtp_ready(get_setting(base, "smtp"))}


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
    from .scheduler import tenant_config
    general, smtp = tenant_config(db, tenancy.tenant_of(db))
    alert = notifier.Alert(monitor="Test monitor", status="up", message="Email alerts are working",
                           project="Test", target="https://example.com", **notifier.alert_context(general))
    error = await notifier.send_email(smtp, alert, [body.to])
    if error:
        # 400, not 502: proxies like Cloudflare replace 502 bodies with their own page
        raise HTTPException(status.HTTP_400_BAD_REQUEST, error)
    return {"sent": True}


# ---------- database (per tenant) ----------

def _db_info(db: Session) -> DatabaseInfo:
    path = db.get_bind().url.database
    size = Path(path).stat().st_size if path and Path(path).is_file() else 0
    return DatabaseInfo(size_kb=round(size / 1024), heartbeats=db.scalar(select(func.count()).select_from(Heartbeat)),
                        incidents=db.scalar(select(func.count()).select_from(Incident)))


@admin.get("/settings/database", response_model=DatabaseInfo, dependencies=[Depends(require_admin)])
def database_info(db: Session = Depends(get_db)):
    return _db_info(db)


@admin.post("/settings/database/shrink", response_model=DatabaseInfo, dependencies=[Depends(require_admin)])
def shrink_database(db: Session = Depends(get_db)):
    if db.get_bind().dialect.name == "sqlite":
        with db.get_bind().connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.exec_driver_sql("VACUUM")
    return _db_info(db)


@admin.post("/settings/database/clear-stats", response_model=DatabaseInfo, dependencies=[Depends(require_admin)])
def clear_stats(db: Session = Depends(get_db)):
    db.execute(delete(Heartbeat))
    db.execute(delete(IncidentAck))
    db.execute(delete(Incident))
    db.execute(delete(AlertEvent))
    db.execute(update(Monitor).values(fail_count=0))
    db.commit()
    return _db_info(db)


# ---------- server (super admin) ----------

def _server_out(value: dict) -> ServerOut:
    return ServerOut(**value, backup_dir=str(backup.backup_dir()), backups=backup.list_all())


@admin.get("/settings/server", response_model=ServerOut, dependencies=[Depends(require_super)])
def server_settings():
    with tenancy.session_for(tenancy.DEFAULT) as db:
        return _server_out(get_setting(db, "server"))


@admin.put("/settings/server", response_model=ServerOut, dependencies=[Depends(require_super)])
def save_server(body: ServerSettings):
    with tenancy.session_for(tenancy.DEFAULT) as db:
        value = put_setting(db, "server", body.model_dump())
    dnscache.enable(value["dns_cache"])
    return _server_out(value)


@admin.post("/settings/server/backup", response_model=ServerOut, dependencies=[Depends(require_super)])
async def backup_now():
    if not backup.supported():
        raise HTTPException(status.HTTP_409_CONFLICT, "Backups work with SQLite databases only")
    with tenancy.session_for(tenancy.DEFAULT) as db:
        value = get_setting(db, "server")
    await asyncio.to_thread(backup.run)
    await asyncio.to_thread(backup.prune, int(value["backup_keep_days"]))
    return _server_out(value)
