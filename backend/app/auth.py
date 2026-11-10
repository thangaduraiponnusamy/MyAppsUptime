import hashlib
import hmac
import logging
import os
import re
import secrets
import time
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .database import get_db
from .models import AuthToken, Monitor, Project, User, aware, utcnow

log = logging.getLogger(__name__)

API_TOKEN = os.getenv("API_TOKEN")
SESSION_DAYS = int(os.getenv("SESSION_DAYS", "7"))
RESET_MIN = 30
MAX_FAILS, LOCK_SEC = 5, 300

# Members may only change these (everything else that is not GET needs an admin)
MEMBER_WRITES = re.compile(r"^/api/(incidents/\d+/ack|monitors/\d+/check)$")


@dataclass
class Principal:
    id: int
    username: str
    role: str
    must_change_password: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


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


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.execute(delete(AuthToken).where(AuthToken.expires_at < utcnow()))
    db.add(AuthToken(token_hash=_token_hash(token), user_id=user.id, expires_at=utcnow() + timedelta(days=SESSION_DAYS)))
    db.commit()
    return token


def create_reset_token(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
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


def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> Principal:
    token = bearer(authorization)
    if token and API_TOKEN and secrets.compare_digest(token, API_TOKEN):
        return Principal(id=0, username="api-token", role="admin")
    if token:
        row = db.execute(
            select(AuthToken, User).join(User, User.id == AuthToken.user_id)
            .where(AuthToken.token_hash == _token_hash(token), AuthToken.kind == "session")
        ).first()
        if row and aware(row.AuthToken.expires_at) > utcnow() and row.User.active:
            u = row.User
            return Principal(id=u.id, username=u.username, role=u.role, must_change_password=u.must_change_password)
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Please sign in")


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


def visible_monitor_ids(db: Session, user: Principal) -> set[int] | None:
    """None means every monitor; members only see monitors of their projects."""
    if user.is_admin:
        return None
    projects = [p.id for p in db.scalars(select(Project)).all() if user.id in (p.member_ids or [])]
    if not projects:
        return set()
    return set(db.scalars(select(Monitor.id).where(Monitor.project_id.in_(projects))).all())


def ensure_default_admin(db: Session) -> None:
    if db.scalar(select(User.id).limit(1)) is None:
        db.add(User(username="admin", name="Administrator", role="admin",
                    password_hash=hash_password("admin"), must_change_password=True))
        db.commit()
        log.warning("Created default user admin / admin - change the password at first sign-in")
