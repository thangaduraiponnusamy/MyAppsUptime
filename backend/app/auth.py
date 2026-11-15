import hashlib
import hmac
import ipaddress
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import tenancy
from .models import AuthToken, Monitor, Project, User, aware, utcnow

log = logging.getLogger(__name__)

API_TOKEN = os.getenv("API_TOKEN")
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "7"))
RESET_MIN = 30
MAX_FAILS, LOCK_SEC = 5, 300

# Published at https://www.cloudflare.com/ips/
CLOUDFLARE = [ipaddress.ip_network(n) for n in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18",
    "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17",
    "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32",
    "2a06:98c0::/29", "2c0f:f248::/32",
)]


def _parse_ip(value: str):
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def client_ip(request: Request) -> str:
    """The visitor's address. Behind Cloudflare the peer is a Cloudflare server, so its
    CF-Connecting-IP header is used, but only when the request really came from Cloudflare."""
    peer = request.client.host if request.client else ""
    addr = _parse_ip(peer)
    if addr is not None and any(addr in net for net in CLOUDFLARE):
        real = _parse_ip(request.headers.get("cf-connecting-ip", ""))
        if real is not None:
            return str(real)
    return peer[:64]

# Members may only change these (everything else that is not GET needs an admin)
MEMBER_WRITES = re.compile(r"^/api/(incidents/\d+/ack|monitors/\d+/check)$")


@dataclass
class Principal:
    id: int
    username: str
    role: str
    must_change_password: bool = False
    tenant: str = tenancy.DEFAULT  # where the account lives
    active: str = tenancy.DEFAULT  # tenant being worked on (a super admin can switch)

    @property
    def is_admin(self) -> bool:
        return self.role in ("admin", "superadmin")

    @property
    def is_super(self) -> bool:
        return self.role == "superadmin" and self.tenant == tenancy.DEFAULT

    def is_self(self, user_id: int) -> bool:
        return self.tenant == self.active and self.id == user_id


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        check = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
    except ValueError:
        return False
    return hmac.compare_digest(check.hex(), digest)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_token(db: Session) -> str:
    # The tenant prefix tells which database holds the token
    return f"{tenancy.tenant_of(db)}.{secrets.token_urlsafe(32)}"


def token_tenant(token: str) -> str:
    return token.split(".", 1)[0] if "." in token else tenancy.DEFAULT


def create_session(db: Session, user: User) -> str:
    token = _new_token(db)
    db.execute(delete(AuthToken).where(AuthToken.expires_at < utcnow()))
    db.add(AuthToken(token_hash=_token_hash(token), user_id=user.id, expires_at=utcnow() + timedelta(days=SESSION_DAYS)))
    db.commit()
    return token


def create_reset_token(db: Session, user: User) -> str:
    token = _new_token(db)
    db.execute(delete(AuthToken).where(AuthToken.user_id == user.id, AuthToken.kind == "reset"))
    db.add(AuthToken(token_hash=_token_hash(token), user_id=user.id, kind="reset",
                     expires_at=utcnow() + timedelta(minutes=RESET_MIN)))
    db.commit()
    return token


def use_reset_token(db: Session, token: str) -> User | None:
    row = db.get(AuthToken, _token_hash(token))
    if row is None or row.kind != "reset" or aware(row.expires_at) <= utcnow():
        return None
    user = db.get(User, row.user_id)
    db.delete(row)
    return user if user and user.active else None


def end_session(db: Session, token: str) -> None:
    db.execute(delete(AuthToken).where(AuthToken.token_hash == _token_hash(token)))
    db.commit()


def end_all_sessions(db: Session, user_id: int) -> None:
    db.execute(delete(AuthToken).where(AuthToken.user_id == user_id))


# ---------- brute-force protection (per user name + client address) ----------

_fails: dict[str, tuple[int, float]] = {}


def check_lock(key: str) -> None:
    count, until = _fails.get(key, (0, 0.0))
    if count >= MAX_FAILS and until > time.monotonic():
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many failed logins. Try again in a few minutes.")


def record_fail(key: str) -> None:
    count, _ = _fails.get(key, (0, 0.0))
    _fails[key] = (count + 1, time.monotonic() + LOCK_SEC)


def clear_fails(key: str) -> None:
    _fails.pop(key, None)


# ---------- dependencies ----------

def bearer(authorization: str | None) -> str | None:
    if authorization and authorization.startswith("Bearer "):
        return authorization[7:].strip() or None
    return None


def _resolve(request: Request) -> Principal | None:
    """Find the signed-in user once per request, in the database of the tenant the token belongs to."""
    if hasattr(request.state, "principal"):
        return request.state.principal
    principal = None
    token = bearer(request.headers.get("authorization"))
    if token and API_TOKEN and secrets.compare_digest(token, API_TOKEN):
        principal = Principal(id=0, username="api-token", role="admin")
    elif token:
        slug = token_tenant(token)
        if tenancy.is_active(slug):
            with tenancy.session_for(slug) as db:
                row = db.execute(
                    select(AuthToken, User).join(User, User.id == AuthToken.user_id)
                    .where(AuthToken.token_hash == _token_hash(token), AuthToken.kind == "session")
                ).first()
                if row and aware(row.AuthToken.expires_at) > utcnow() and row.User.active:
                    u = row.User
                    principal = Principal(u.id, u.username, u.role, u.must_change_password, slug, slug)
    if principal and principal.is_super:
        wanted = request.headers.get("x-tenant")
        if wanted and tenancy.exists(wanted):
            principal.active = wanted
    request.state.principal = principal
    return principal


def get_db(request: Request):
    """Session for the tenant of the signed-in user; anonymous calls get the default tenant."""
    principal = _resolve(request)
    db = tenancy.session_for(principal.active if principal else tenancy.DEFAULT)
    try:
        yield db
    finally:
        db.close()


def current_user(request: Request) -> Principal:
    principal = _resolve(request)
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in")
    return principal


def require_user(request: Request, user: Principal = Depends(current_user)) -> Principal:
    if user.must_change_password:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Password change required")
    if request.method != "GET" and not user.is_admin and not MEMBER_WRITES.match(request.url.path):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only admins can change this")
    request.state.user = user
    return user


def require_admin(user: Principal = Depends(require_user)) -> Principal:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only")
    return user


def require_super(user: Principal = Depends(require_user)) -> Principal:
    if not user.is_super:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Super admin only")
    return user


def visible_monitor_ids(db: Session, user: Principal) -> set[int] | None:
    """None means every monitor; members see monitors of their projects plus services given to them."""
    if user.is_admin:
        return None
    projects = [p.id for p in db.scalars(select(Project)).all() if user.id in (p.member_ids or [])]
    row = db.get(User, user.id) if user.tenant == user.active else None
    extra = set(row.monitor_ids or []) if row else set()
    if projects:
        extra |= set(db.scalars(select(Monitor.id).where(Monitor.project_id.in_(projects))).all())
    return extra


def ensure_default_admin(db: Session) -> None:
    """The default tenant always has a super admin (admin / admin on a fresh install)."""
    if db.scalar(select(User.id).limit(1)) is None:
        db.add(User(username="admin", name="Super admin", role="superadmin",
                    password_hash=hash_password("admin"), must_change_password=True))
        db.commit()
        log.warning("Created default user admin / admin - change the password at first sign-in")
        return
    if db.scalar(select(User.id).where(User.role == "superadmin").limit(1)) is None:
        first = db.scalar(select(User).where(User.role == "admin").order_by(User.id).limit(1))
        if first is not None:
            first.role = "superadmin"
            db.commit()
            log.warning("User %s is now the super admin", first.username)
