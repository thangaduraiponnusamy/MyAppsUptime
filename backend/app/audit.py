"""Audit log: every successful change made through the API, with who made it and from where.
Rows are only ever added; there is no API to change or delete them (old rows go after audit_keep_days)."""
import json
import re
from urllib.parse import urlsplit, urlunsplit

from fastapi import Request
from sqlalchemy import func, select

from . import tenancy
from .auth import _resolve, client_ip
from .config import DEFAULTS, get_setting
from .models import (AuditEvent, Incident, MaintenanceWindow, Monitor, NotificationChannel, Probe, Project, StatusPage,
                     User)

# Sign-in and sessions are recorded by their endpoints; check-now and probe syncs are not changes
SKIP = re.compile(r"^/api/(auth/(login|logout|forgot|reset|sessions)|monitors/\d+/check|probe/)")
# Values of these fields are never stored
SECRET = re.compile(r"pass|token|secret|api_?key|hash|^url$|webhook|auth|cookie|signature", re.I)
KINDS = {
    "monitors": ("monitor", Monitor, "name"), "users": ("user", User, "username"),
    "projects": ("project", Project, "name"), "notifications": ("channel", NotificationChannel, "name"),
    "maintenance": ("maintenance", MaintenanceWindow, "title"), "status-pages": ("status page", StatusPage, "title"),
    "probes": ("private location", Probe, "name"), "incidents": ("incident", Incident, None),
}
VERBS = {"pause": "paused", "resume": "resumed", "ack": "acknowledged", "snooze": "snoozed", "test": "sent test", "token": "new token",
         "reset-admin": "reset admin", "shrink": "shrank", "clear-stats": "cleared stats", "backup": "backed up"}
METHODS = {"POST": "created", "PUT": "updated", "DELETE": "deleted"}


def mask(value):
    """Copy of a value that is safe to store: secrets hidden, URLs without credentials or query."""
    if isinstance(value, dict):
        return {k: ("***" if v and SECRET.search(str(k)) else mask(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask(v) for v in value]
    if isinstance(value, str) and "://" in value:
        try:
            url = urlsplit(value)
            host = url.hostname or ""
            if url.port:
                host += f":{url.port}"
            value = urlunsplit((url.scheme, host, url.path, "***" if url.query else "", ""))
        except ValueError:
            return "***"
    if isinstance(value, str) and len(value) > 200:
        return value[:200] + "..."
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def changes(method: str, body, before: dict | None, after: dict | None) -> dict:
    """Changed fields: {field: [old, new]} for updates, {field: value} for new objects. Secrets only say "changed"."""
    if isinstance(body, dict):
        keys = list(body)
    elif before and after:
        keys = list(after)
    else:
        return {}
    out = {}
    for key in keys:
        new = (after or {}).get(key, body.get(key) if isinstance(body, dict) else None)
        old = (before or {}).get(key)
        if SECRET.search(key):
            if isinstance(body, dict) and body.get(key):
                out[key] = "changed"
        elif before is None:
            if new not in (None, "", [], {}):
                out[key] = mask(new)
        elif old != new:
            out[key] = [mask(old), mask(new)]
    return out


def _row(row) -> dict:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}


def snapshot(db, parts: list[str]) -> dict | None:
    """Current fields of the object a path points at (None when there is none)."""
    if parts[0] == "settings" and len(parts) > 1 and parts[1] in DEFAULTS:
        if parts[1] == "server":
            with tenancy.session_for(tenancy.DEFAULT) as admin_db:
                return dict(get_setting(admin_db, "server"))
        return dict(get_setting(db, parts[1]))
    if parts[0] in KINDS and len(parts) > 1 and parts[1].isdigit():
        row = db.get(KINDS[parts[0]][1], int(parts[1]))
        if row is None:
            return None
        data = _row(row)
        if isinstance(row, Monitor):
            data["badge_enabled"] = bool(row.badge_token)
        return data
    return None


def record(db, request: Request | None, username: str, action: str, kind: str, target: str = "", detail: str = "",
           object_id="", changed: dict | None = None) -> None:
    """Add one audit row; the caller commits."""
    db.add(AuditEvent(
        username=username[:60], action=action[:20], kind=kind[:30], target=target[:200], detail=detail[:300],
        ip=client_ip(request) if request else "", tenant=tenancy.tenant_of(db)[:30],
        user_agent=(request.headers.get("user-agent", "") if request else "")[:200], object_id=str(object_id)[:40],
        changes=json.dumps(changed, default=str)[:8000] if changed else ""))


def _name(db, kind: str, obj_id: str) -> str:
    if kind not in KINDS or not obj_id.isdigit():
        return ""
    _, model, field = KINDS[kind]
    row = db.get(model, int(obj_id))
    if row is None:
        return ""
    if model is Incident:
        monitor = db.get(Monitor, row.monitor_id)
        return monitor.name if monitor else f"#{obj_id}"
    return getattr(row, field) or ""


def describe(method: str, path: str, body, before: str) -> tuple[str, str, str, str]:
    """(action, kind, target, detail) for one change. Only names and counts are kept, never other values."""
    parts = path.removeprefix("/api/").split("/")
    body = body if isinstance(body, dict) else {}
    named = next((str(body[k]) for k in ("name", "username", "title") if body.get(k)), "")
    head = parts[0]
    if head == "settings":
        verb = parts[-1] if method == "POST" else ""
        return VERBS.get(verb, verb or "updated"), "settings", "/".join(parts[1:2]), ""
    if head == "auth":
        if parts[1:2] == ["alerts"]:
            return "updated", "account", "alert preferences", ""
        return "changed password", "account", "", ""
    if head == "import":
        counts = ", ".join(f"{len(body.get(k) or [])} {k}" for k in ("monitors", "channels", "projects"))
        return "imported", "config", "", counts
    if head == "tenants":
        verb = parts[2] if len(parts) > 2 else ""
        return VERBS.get(verb, METHODS.get(method, method)), "organization", parts[1] if len(parts) > 1 else named, ""
    if head == "monitors" and len(parts) > 1 and parts[1] == "bulk":
        action = {"pause": "paused", "resume": "resumed", "delete": "deleted", "project": "moved"}.get(
            body.get("action"), "changed")
        return action, "", f"{len(body.get('ids') or [])} services", ""
    kind = KINDS.get(head, (head,))[0]
    verb = parts[2] if len(parts) > 2 else ""
    action = VERBS.get(verb, verb) if verb else METHODS.get(method, method.lower())
    if verb == "snooze" and body.get("minutes") == 0:
        action = "unsnoozed"
    target = named or before
    detail = f"was {before}" if method == "PUT" and named and before and named != before else ""
    return action, kind, target, detail


async def audit(request: Request):
    """Router dependency: records the change after the endpoint succeeded (errors skip the code after yield)."""
    path = request.url.path
    user = _resolve(request) if request.method in METHODS and not SKIP.match(path) else None
    if user is None:
        yield
        return
    try:
        body = json.loads(await request.body() or b"null")
    except ValueError:
        body = None
    parts = path.removeprefix("/api/").split("/")
    with tenancy.session_for(user.active) as db:
        before = _name(db, parts[0], parts[1]) if len(parts) > 1 else ""
        old = snapshot(db, parts)
    yield
    action, kind, target, detail = describe(request.method, path, body, before)
    with tenancy.session_for(user.active) as db:
        new = snapshot(db, parts) if request.method != "DELETE" else None
        object_id = parts[1] if len(parts) > 1 and parts[1].isdigit() else ""
        if not object_id and len(parts) == 1 and request.method == "POST" and parts[0] in KINDS:
            object_id = db.scalar(select(func.max(KINDS[parts[0]][1].id))) or ""  # the row just created
        record(db, request, user.username, action, kind, target, detail, object_id,
               {} if parts[0] == "auth" else changes(request.method, body, old, new))
        db.commit()
