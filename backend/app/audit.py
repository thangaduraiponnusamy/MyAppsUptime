"""Audit log: every successful change made through the API, with who made it and from where."""
import json
import re

from fastapi import Request

from . import tenancy
from .auth import _resolve, client_ip
from .models import (AuditEvent, Incident, MaintenanceWindow, Monitor, NotificationChannel, Probe, Project, StatusPage,
                     User)

# Sign-in has its own history; check-now and probe syncs are not changes
SKIP = re.compile(r"^/api/(auth/(login|logout|forgot|reset)|monitors/\d+/check|probe/)")
KINDS = {
    "monitors": ("monitor", Monitor, "name"), "users": ("user", User, "username"),
    "projects": ("project", Project, "name"), "notifications": ("channel", NotificationChannel, "name"),
    "maintenance": ("maintenance", MaintenanceWindow, "title"), "status-pages": ("status page", StatusPage, "title"),
    "probes": ("private location", Probe, "name"), "incidents": ("incident", Incident, None),
}
VERBS = {"pause": "paused", "resume": "resumed", "ack": "acknowledged", "test": "sent test", "token": "new token",
         "reset-admin": "reset admin", "shrink": "shrank", "clear-stats": "cleared stats", "backup": "backed up"}
METHODS = {"POST": "created", "PUT": "updated", "DELETE": "deleted"}


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
    yield
    action, kind, target, detail = describe(request.method, path, body, before)
    with tenancy.session_for(user.active) as db:
        db.add(AuditEvent(username=user.username[:60], action=action[:20], kind=kind[:30], target=target[:200],
                          detail=detail[:300], ip=client_ip(request)))
        db.commit()
