"""Signed Acknowledge / Snooze links in alerts. GET shows a confirm page, only POST changes anything."""
import base64
import hmac
import html
import json
import time
from datetime import timedelta

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from . import audit, crypto, tenancy
from .models import Incident, IncidentAck, Monitor, utcnow

VALID_DAYS = 7
BY = "via alert link"
ACTIONS = {"ack": ("Acknowledge", None), "snooze1h": ("Snooze 1h", 60), "snooze4h": ("Snooze 4h", 240)}

router = APIRouter(prefix="/api/action")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_token(tenant: str, incident_id: int, monitor_id: int, action: str, days: int = VALID_DAYS) -> str:
    payload = {"t": tenant, "i": incident_id, "m": monitor_id, "a": action, "e": int(time.time()) + days * 86400}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{crypto.sign('alert-action', body)}"


def read_token(token: str) -> dict | None:
    """The payload of a valid, unexpired token, else None."""
    body, _, sig = token.partition(".")
    if not body or not hmac.compare_digest(sig.encode(), crypto.sign("alert-action", body).encode()):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except ValueError:
        return None
    if data.get("a") not in ACTIONS or data.get("e", 0) < time.time() or not tenancy.exists(str(data.get("t"))):
        return None
    return data


def links(app_url: str, tenant: str, incident_id: int, monitor_id: int) -> list[tuple[str, str]]:
    return [(ACTIONS[a][0], f"{app_url}/api/action/{make_token(tenant, incident_id, monitor_id, a)}")
            for a in ("ack", "snooze1h")]


def _page(title: str, text: str, button: str = "", code: int = 200) -> HTMLResponse:
    form = f'<form method="post"><button type="submit">{html.escape(button)}</button></form>' if button else ""
    return HTMLResponse(
        f"<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width\">"
        f"<title>{html.escape(title)}</title></head>"
        f"<body style=\"font-family:sans-serif;max-width:28rem;margin:4rem auto;padding:0 1rem\">"
        f"<h1 style=\"font-size:1.3rem\">{html.escape(title)}</h1><p>{html.escape(text)}</p>{form}</body></html>",
        status_code=code)


def _invalid() -> HTMLResponse:
    return _page("Link not valid", "This link is invalid or has expired. Open the app to act on the alert.", code=400)


def _load(db, data: dict):
    incident, monitor = db.get(Incident, data["i"]), db.get(Monitor, data["m"])
    if incident is None or monitor is None or incident.monitor_id != monitor.id:
        return None
    return incident, monitor


@router.get("/{token}", response_class=HTMLResponse)
def confirm(token: str):
    data = read_token(token)
    if data is None:
        return _invalid()
    with tenancy.session_for(data["t"]) as db:
        found = _load(db, data)
        if found is None:
            return _invalid()
        label = ACTIONS[data["a"]][0]
        return _page(f"{label}: {found[1].name}", f"Press the button to {label.lower()} this alert.", label)


@router.post("/{token}", response_class=HTMLResponse)
def apply(token: str, request: Request):
    data = read_token(token)
    if data is None:
        return _invalid()
    label, minutes = ACTIONS[data["a"]]
    with tenancy.session_for(data["t"]) as db:
        found = _load(db, data)
        if found is None:
            return _invalid()
        incident, monitor = found
        if minutes is None:
            if not incident.acknowledged_at:
                incident.acknowledged_at, incident.acknowledged_by = utcnow(), BY
                incident.acks.append(IncidentAck(by=BY))
            audit.record(db, request, "alert link", "acknowledged", "incident", monitor.name, object_id=incident.id)
            text = "The incident is acknowledged. Repeat alerts stop."
        else:
            monitor.snoozed_until = utcnow() + timedelta(minutes=minutes)
            audit.record(db, request, "alert link", "snoozed", "monitor", monitor.name,
                         f"for {minutes} min", object_id=monitor.id)
            text = f"Alerts are paused until {monitor.snoozed_until:%Y-%m-%d %H:%M} UTC. Checks continue."
        db.commit()
        return _page(f"Done: {label}", text)
