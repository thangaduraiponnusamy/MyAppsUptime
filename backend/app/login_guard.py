"""Failed sign-ins: log for the super admin, email alerts, unlock. Plus "new device" emails with a "This wasn't me" link."""
import asyncio
import base64
import hmac
import ipaddress
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import audit, crypto, notifier, tenancy
from .actions import _page
from .auth import ACCOUNT_LOCK_AFTER, ACCOUNT_LOCK_MIN, end_all_sessions, require_super
from .config import get_setting
from .models import BlockedIp, FailedLogin, LoginEvent, User, aware, utcnow
from .schemas import BlockedIpOut, FailedLoginOut, FailedLoginsOut, IpBlockIn, LockedAccountOut, UnlockIn

log = logging.getLogger(__name__)

ALERT_FAILS, ALERT_WINDOW = 5, timedelta(hours=1)
MAIL_EVERY = 3600  # seconds between emails about the same account or address
KEEP = timedelta(days=90)
LINK_DAYS = 7
REPORTED_LOCK = timedelta(days=3650)  # "This wasn't me": locked until a super admin unlocks or the password is reset
BLOCK_FAILS = int(os.getenv("LOGIN_BLOCK_FAILS", "10"))
BLOCK_WINDOW = timedelta(minutes=15)
BLOCK_FOR = timedelta(minutes=int(os.getenv("LOGIN_BLOCK_MIN", "60")))

admin = APIRouter(prefix="/api/security")
links = APIRouter(prefix="/api/account")

_sent: dict[str, float] = {}
_sent_lock = threading.Lock()


def lock_exempt(slug: str, user: User) -> bool:
    """Super admins are never locked, so the system always has someone who can sign in."""
    return slug == tenancy.DEFAULT and user.role == "superadmin"


# ---------- email ----------

def deliver(smtp: dict, recipients: list[str], subject: str, body: str) -> None:
    """Send in the background so a slow mail server does not slow down sign-in."""
    def run():
        error = asyncio.run(notifier.send_text(smtp, recipients, subject, body))
        if error:
            log.warning("Security email failed: %s", error)
    threading.Thread(target=run, daemon=True).start()


def _mail_config(slug: str) -> tuple[dict, dict] | None:
    """(smtp, general) of the tenant, or of the admin tenant when the tenant has no mail server."""
    for s in dict.fromkeys((slug, tenancy.DEFAULT)):
        with tenancy.session_for(s) as db:
            smtp, general = get_setting(db, "smtp"), get_setting(db, "general")
        if notifier.smtp_ready(smtp):
            return smtp, general
    return None


def _throttled(key: str) -> bool:
    now = time.monotonic()
    with _sent_lock:
        if key in _sent and now - _sent[key] < MAIL_EVERY:
            return True
        _sent[key] = now
        return False


def _local(general: dict, at: datetime) -> str:
    tz = notifier.server_tz(general)
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        zone, tz = timezone.utc, "UTC"
    return f"{at.astimezone(zone):%Y-%m-%d %H:%M:%S} ({tz})"


def notify_super_admins(key: str, subject: str, lines: list[str]) -> None:
    config = _mail_config(tenancy.DEFAULT)
    if config is None:
        return
    with tenancy.session_for(tenancy.DEFAULT) as db:
        emails = [e for e in db.scalars(select(User.email).where(
            User.role == "superadmin", User.active.is_(True), User.email.is_not(None))) if e]
    if not emails or _throttled(key):
        return
    smtp, general = config
    app_url = notifier.server_url(general)
    body = "\n".join(lines + ["", f"Time: {_local(general, utcnow())}"]
                     + ([f"Details: {app_url}/settings/security"] if app_url else []))
    deliver(smtp, emails, f"MyAppsUptime security: {subject}", body)


# ---------- failed sign-ins ----------

def ip_blocked(ip: str) -> bool:
    if not ip:
        return False
    with tenancy.session_for(tenancy.DEFAULT) as db:
        row = db.get(BlockedIp, ip)
        return row is not None and aware(row.until) > utcnow()


def record_failure(username: str, tenant: str, ip: str, device: str, reason: str, locked_now: bool = False) -> None:
    """Log one failed sign-in, block the address after too many, and email the super admins when it looks like an attack."""
    now, name = utcnow(), username.strip().lower()[:60]
    since = now - ALERT_WINDOW
    blocked_now = 0
    with tenancy.session_for(tenancy.DEFAULT) as db:
        db.execute(delete(FailedLogin).where(FailedLogin.created_at < now - KEEP))
        db.add(FailedLogin(username=name, tenant=tenant, ip=ip[:64], device=device[:80], reason=reason))
        db.commit()
        by_user = db.scalar(select(func.count(FailedLogin.id)).where(
            FailedLogin.created_at >= since, FailedLogin.username == name))
        by_ip, users = db.execute(select(func.count(FailedLogin.id), func.count(func.distinct(FailedLogin.username))).where(
            FailedLogin.created_at >= since, FailedLogin.ip == ip)).one() if ip else (0, 0)
        if ip and reason != "blocked IP":
            row = db.get(BlockedIp, ip[:64])
            # Count again from the end of the last block, so an unblocked address gets a fresh start
            start = now - BLOCK_WINDOW
            if row is not None and aware(row.until) > start:
                start = aware(row.until)
            recent = db.scalar(select(func.count(FailedLogin.id)).where(
                FailedLogin.created_at >= start, FailedLogin.ip == ip))
            if recent >= BLOCK_FAILS and (row is None or aware(row.until) <= now):
                db.merge(BlockedIp(ip=ip[:64], until=now + BLOCK_FOR, created_at=now,
                                   reason=f"{recent} failed sign-ins in {int(BLOCK_WINDOW.total_seconds() // 60)} minutes"))
                db.commit()
                blocked_now = recent
    team = f"team {tenancy.name(tenant)}" if tenant else "no such user"
    where = [f"User name: {name} ({team})", f"IP address: {ip or '-'}", f"Device: {device or '-'}"]
    if locked_now:
        notify_super_admins(f"user|{name}", f"account {name} locked",
                            [f"The account was locked for {ACCOUNT_LOCK_MIN} minutes after "
                             f"{ACCOUNT_LOCK_AFTER} wrong passwords in a row.", ""] + where)
    # Unknown user names cannot lock, so alert as early as a real account would lock
    elif by_user >= (ALERT_FAILS if tenant else ACCOUNT_LOCK_AFTER):
        notify_super_admins(f"user|{name}", f"{by_user} failed sign-ins for {name}",
                            [f"{by_user} failed sign-ins for this user name in the last hour.",
                             f"Last reason: {reason}.", ""] + where)
    # One address trying several user names
    if blocked_now:
        minutes = int(BLOCK_FOR.total_seconds() // 60)
        notify_super_admins(f"block|{ip}", f"address {ip} blocked",
                            [f"{blocked_now} failed sign-ins in the last {int(BLOCK_WINDOW.total_seconds() // 60)} minutes, "
                             f"so this address may not sign in for {minutes} minutes (super admins still can).",
                             "Unblock it in Settings > Security if it is yours.", "",
                             f"IP address: {ip}", f"Device: {device or '-'}", f"Last user name: {name}"])
    elif by_ip >= ALERT_FAILS and users > 1:
        notify_super_admins(f"ip|{ip}", f"{by_ip} failed sign-ins from {ip}",
                            [f"{by_ip} failed sign-ins for {users} user names from one address in the last hour.", "",
                             f"IP address: {ip}", f"Device: {device or '-'}"])


@admin.get("/failed-logins", response_model=FailedLoginsOut, dependencies=[Depends(require_super)])
def failed_logins(hours: int = 24, start: datetime | None = None, end: datetime | None = None, limit: int = 500):
    now = utcnow()
    since = start or now - timedelta(hours=max(1, min(hours, 24 * 90)))
    query = select(FailedLogin).where(FailedLogin.created_at >= since)
    if end:
        query = query.where(FailedLogin.created_at < end)
    names = {s: tenancy.name(s) for s in tenancy.slugs(active_only=False)}
    with tenancy.session_for(tenancy.DEFAULT) as db:
        rows = db.scalars(query.order_by(FailedLogin.created_at.desc(), FailedLogin.id.desc())
                          .limit(max(1, min(limit, 5000)))).all()
        events = [FailedLoginOut.model_validate(r).model_copy(update={"tenant_name": names.get(r.tenant, r.tenant)})
                  for r in rows]
    locked = []
    for slug, team in names.items():
        with tenancy.session_for(slug) as db:
            for user in db.scalars(select(User).where(User.locked_until > now).order_by(User.username)):
                if lock_exempt(slug, user):
                    continue
                until = aware(user.locked_until)
                locked.append(LockedAccountOut(username=user.username, tenant=slug, tenant_name=team,
                                               locked_until=until, reported=until - now > timedelta(days=1)))
    with tenancy.session_for(tenancy.DEFAULT) as db:
        db.execute(delete(BlockedIp).where(BlockedIp.until <= now - BLOCK_WINDOW))
        db.commit()
        blocked = [BlockedIpOut.model_validate(r) for r in db.scalars(
            select(BlockedIp).where(BlockedIp.until > now).order_by(BlockedIp.ip))]
    return FailedLoginsOut(events=events, locked=locked, blocked=blocked)


@admin.post("/ips/block", dependencies=[Depends(require_super)])
def block_ip(body: IpBlockIn):
    try:
        ip = str(ipaddress.ip_address(body.ip.strip()))
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Not a valid IP address")
    with tenancy.session_for(tenancy.DEFAULT) as db:
        db.merge(BlockedIp(ip=ip, until=utcnow() + timedelta(hours=body.hours), created_at=utcnow(),
                           reason="blocked by a super admin"))
        db.commit()
    return {"ok": True}


@admin.post("/ips/unblock", dependencies=[Depends(require_super)])
def unblock_ip(body: IpBlockIn):
    with tenancy.session_for(tenancy.DEFAULT) as db:
        row = db.get(BlockedIp, body.ip.strip())
        if row is not None:
            row.until = utcnow()
            db.commit()
    return {"ok": True}


@admin.post("/accounts/unlock", dependencies=[Depends(require_super)])
def unlock(body: UnlockIn):
    if not tenancy.exists(body.tenant):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Tenant not found")
    with tenancy.session_for(body.tenant) as db:
        user = db.scalar(select(User).where(func.lower(User.username) == body.username.strip().lower()))
        if user is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
        user.failed_logins, user.locked_until = 0, None
        db.commit()
    return {"ok": True}


# ---------- sign-in from a new device or address ----------

def is_new_place(db: Session, user: User, ip: str, device: str) -> bool:
    """True when the user signed in before, but never from this address or this device. Call before logging this one."""
    ok = select(LoginEvent.id).where(LoginEvent.user_id == user.id, LoginEvent.username == user.username,
                                     LoginEvent.success.is_(True)).limit(1)
    if db.scalar(ok) is None:
        return False
    return db.scalar(ok.where(LoginEvent.ip == ip)) is None or db.scalar(ok.where(LoginEvent.device == device)) is None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_token(slug: str, user: User) -> str:
    payload = {"t": slug, "u": user.id, "n": user.username, "e": int(time.time()) + LINK_DAYS * 86400}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{crypto.sign('not-me', body)}"


def read_token(token: str) -> dict | None:
    body, _, sig = token.partition(".")
    if not body or not hmac.compare_digest(sig.encode(), crypto.sign("not-me", body).encode()):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError:
        return None
    if data.get("e", 0) < time.time() or not tenancy.exists(str(data.get("t"))):
        return None
    return data


def new_place_email(slug: str, user: User, ip: str, device: str) -> None:
    if not user.email:
        return
    config = _mail_config(slug)
    if config is None:
        return
    smtp, general = config
    app_url = notifier.server_url(general)
    exempt = lock_exempt(slug, user)
    what = "sign out all sessions" if exempt else "sign out all sessions and lock the account"
    lines = [f"Hello {user.name or user.username},", "",
             f"Your MyAppsUptime account {user.username} was just used to sign in from a new device or address.", "",
             f"Time: {_local(general, utcnow())}", f"Device: {device or '-'}", f"IP address: {ip or '-'}", "",
             "If this was you, you can ignore this email.", ""]
    if app_url:
        lines += [f"If this was NOT you, open this link to {what} (valid {LINK_DAYS} days):",
                  f"{app_url}/api/account/not-me/{make_token(slug, user)}", ""]
    lines.append("If this was NOT you, change your password now." if exempt or not app_url
                 else "Then reset your password with \"Forgot password?\" on the sign-in page.")
    deliver(smtp, [user.email], "New sign-in to your MyAppsUptime account", "\n".join(lines))


def _invalid() -> HTMLResponse:
    return _page("Link not valid", "This link is invalid or has expired. Change your password and contact your administrator.",
                 code=400)


def _user(db: Session, data: dict) -> User | None:
    user = db.get(User, data.get("u"))
    return user if user is not None and user.username == data.get("n") else None


@links.get("/not-me/{token}", response_class=HTMLResponse)
def not_me_confirm(token: str):
    data = read_token(token)
    if data is None:
        return _invalid()
    with tenancy.session_for(data["t"]) as db:
        user = _user(db, data)
        if user is None:
            return _invalid()
        exempt = lock_exempt(data["t"], user)
    what = "sign out all sessions" if exempt else "sign out all sessions and lock the account"
    return _page(f"This wasn't me: {user.username}", f"Press the button to {what}.", "Secure my account")


@links.post("/not-me/{token}", response_class=HTMLResponse)
def not_me(token: str, request: Request):
    data = read_token(token)
    if data is None:
        return _invalid()
    slug = data["t"]
    with tenancy.session_for(slug) as db:
        user = _user(db, data)
        if user is None:
            return _invalid()
        exempt = lock_exempt(slug, user)
        end_all_sessions(db, user.id)
        if not exempt:
            user.failed_logins, user.locked_until = 0, utcnow() + REPORTED_LOCK
        done = "all sessions signed out" + ("" if exempt else ", account locked")
        audit.record(db, request, user.username, "reported sign-in", "account", user.username,
                     f"This wasn't me link: {done}", user.id)
        db.commit()
        name = user.username
    notify_super_admins(f"report|{name}", f"{name} reported a sign-in that was not them",
                        [f"The user clicked \"This wasn't me\" in a new sign-in email: {done}.",
                         "" if exempt else "Unlock the account in Settings > Security once it is safe.", "",
                         f"User name: {name} (team {tenancy.name(slug)})"])
    text = ("All sessions are signed out. Change your password now." if exempt else
            "All sessions are signed out and the account is locked. Reset your password with \"Forgot password?\" "
            "on the sign-in page, or ask an administrator to unlock it.")
    return _page("Account secured", text)
