"""Browser push notifications (Web Push with VAPID keys)."""
import asyncio
import base64
import hashlib
import json
import logging
import os
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pywebpush import WebPushException, webpush
from sqlalchemy import delete, select

from . import audit, tenancy
from .auth import Principal, require_user
from .checks import Blocked, TargetPolicy, guard_host
from .crypto import data_dir
from .models import Monitor, Project, PushSubscription, User
from .schemas import PushSubscribeIn

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/push")
PUSH_STATUSES = ("down", "up", "warning")
_keys: dict | None = None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _generate() -> dict:
    key = ec.generate_private_key(ec.SECP256R1())
    return {"private_key": _b64(key.private_numbers().private_value.to_bytes(32, "big")),
            "public_key": _b64(key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint))}


def vapid_keys() -> dict:
    """VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY, else vapid.json in the data folder (created once)."""
    global _keys
    if _keys is None:
        subject = os.getenv("VAPID_SUBJECT") or "mailto:admin@localhost"
        if os.getenv("VAPID_PRIVATE_KEY") and os.getenv("VAPID_PUBLIC_KEY"):
            _keys = {"private_key": os.environ["VAPID_PRIVATE_KEY"], "public_key": os.environ["VAPID_PUBLIC_KEY"]}
        else:
            path = data_dir() / "vapid.json"
            if not path.is_file():
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "w") as f:
                        json.dump(_generate(), f)
                    log.warning("Created push keys %s - back it up, or set VAPID_PRIVATE_KEY/VAPID_PUBLIC_KEY", path)
                except FileExistsError:
                    pass
            _keys = json.loads(path.read_text())
        _keys = {**_keys, "subject": subject}
    return _keys


def endpoint_hash(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode()).hexdigest()


def check_endpoint(endpoint: str) -> None:
    """The browser picks the endpoint; never send to private or local addresses."""
    host = urlsplit(endpoint).hostname or ""
    guard_host(host, 443, TargetPolicy())


def _send_one(sub: PushSubscription, payload: dict) -> int | None:
    """Send one message. Returns the HTTP status of a failed send, None when it worked."""
    keys = vapid_keys()
    try:
        check_endpoint(sub.endpoint)
        webpush(subscription_info={"endpoint": sub.endpoint, "keys": json.loads(sub.keys or "{}")},
                data=json.dumps(payload), vapid_private_key=keys["private_key"],
                vapid_claims={"sub": keys["subject"]}, ttl=3600, timeout=10)
    except WebPushException as exc:
        return exc.response.status_code if exc.response is not None else 0
    except (Blocked, OSError, ValueError) as exc:
        log.info("Push to %s skipped: %s", urlsplit(sub.endpoint).hostname, type(exc).__name__)
        return 0
    return None


async def send_to(tenant: str, user_ids: set[int] | None, payload: dict) -> tuple[int, list[str]]:
    """Send to the subscriptions of these users (None = all). Gone subscriptions (404/410) are removed."""
    with tenancy.session_for(tenant) as db:
        query = select(PushSubscription)
        if user_ids is not None:
            query = query.where(PushSubscription.user_id.in_(user_ids))
        subs = db.scalars(query).all()
    sent, errors, gone = 0, [], []
    for sub in subs:
        code = await asyncio.to_thread(_send_one, sub, payload)
        if code is None:
            sent += 1
        elif code in (404, 410):
            gone.append(sub.id)
        else:
            errors.append(f"Push: HTTP {code}" if code else "Push: failed")
    if gone:
        with tenancy.session_for(tenant) as db:
            db.execute(delete(PushSubscription).where(PushSubscription.id.in_(gone)))
            db.commit()
    return sent, errors


def allowed_users(db, m: Monitor) -> set[int]:
    """Active users that can see this monitor: admins, project members and users given the service."""
    project = db.get(Project, m.project_id) if m.project_id else None
    members = set(project.member_ids or []) if project else set()
    return {u.id for u in db.scalars(select(User).where(User.active.is_(True))).all()
            if u.role in ("admin", "superadmin") or u.id in members or m.id in (u.monitor_ids or [])}


async def notify_monitor(tenant: str, monitor_id: int, alert) -> tuple[int, list[str]]:
    if alert.status not in PUSH_STATUSES:
        return 0, []
    with tenancy.session_for(tenant) as db:
        if db.scalar(select(PushSubscription.id).limit(1)) is None:
            return 0, []
        m = db.get(Monitor, monitor_id)
        if m is None:
            return 0, []
        users = allowed_users(db, m)
    payload = {"title": alert.title, "body": alert.message or "", "tag": f"monitor-{monitor_id}",
               "url": f"/monitors/{monitor_id}"}
    return await send_to(tenant, users, payload)


def _own(user: Principal) -> Principal:
    if user.id <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Push needs a signed-in user")
    return user


@router.get("/key")
def public_key(_: Principal = Depends(require_user)):
    return {"public_key": vapid_keys()["public_key"]}


@router.post("/subscribe", status_code=status.HTTP_204_NO_CONTENT)
def subscribe(body: PushSubscribeIn, request: Request, user: Principal = Depends(require_user)):
    _own(user)
    if body.keys is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "keys are required")
    from .admin_api import describe_agent
    device = (body.device or describe_agent(request.headers.get("user-agent", "")))[:80]
    digest = endpoint_hash(body.endpoint)
    with tenancy.session_for(user.tenant) as db:
        db.execute(delete(PushSubscription).where(PushSubscription.endpoint_hash == digest))
        db.add(PushSubscription(user_id=user.id, endpoint_hash=digest, endpoint=body.endpoint,
                                keys=json.dumps(body.keys.model_dump()), device=device))
        audit.record(db, request, user.username, "enabled", "push", device)
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/subscribe", status_code=status.HTTP_204_NO_CONTENT)
def unsubscribe(body: PushSubscribeIn, request: Request, user: Principal = Depends(require_user)):
    _own(user)
    with tenancy.session_for(user.tenant) as db:
        sub = db.scalar(select(PushSubscription).where(PushSubscription.endpoint_hash == endpoint_hash(body.endpoint),
                                                       PushSubscription.user_id == user.id))
        if sub is not None:
            audit.record(db, request, user.username, "disabled", "push", sub.device)
            db.delete(sub)
            db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/test")
async def test(user: Principal = Depends(require_user)):
    _own(user)
    payload = {"title": "MyAppsUptime test", "body": "Push notifications work on this device.",
               "tag": "test", "url": "/settings/notifications"}
    sent, errors = await send_to(user.tenant, {user.id}, payload)
    if not sent and not errors:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No device is subscribed")
    return {"sent": sent, "errors": errors}
