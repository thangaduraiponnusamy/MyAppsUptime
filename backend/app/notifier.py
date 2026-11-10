import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from html import escape

import httpx

from .models import Monitor, NotificationChannel

log = logging.getLogger(__name__)

COLORS = {"up": "#16a34a", "down": "#dc2626", "degraded": "#d97706", "maintenance": "#2563eb"}


@dataclass
class Alert:
    monitor: str
    status: str
    message: str
    project: str | None = None
    target: str | None = None
    link: str | None = None
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def title(self) -> str:
        word = {"up": "is back UP", "down": "is DOWN", "degraded": "is DEGRADED"}.get(self.status, self.status.upper())
        return f"{self.monitor} {word}"

    def facts(self) -> list[tuple[str, str]]:
        return [("Status", self.status.upper()), ("Project", self.project or "-"), ("Target", self.target or "-"),
                ("Details", self.message or "-"), ("Time (UTC)", self.at.strftime("%Y-%m-%d %H:%M:%S"))]


def channel_matches(channel: NotificationChannel, monitor: Monitor) -> bool:
    tags_ok = not channel.tags or bool(set(channel.tags) & set(monitor.tags or []))
    projects_ok = not channel.project_ids or monitor.project_id in channel.project_ids
    return tags_ok and projects_ok


def teams_payload(alert: Alert) -> dict:
    color = {"up": "Good", "down": "Attention", "degraded": "Warning"}.get(alert.status, "Default")
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "color": color, "text": alert.title, "wrap": True},
            {"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in alert.facts()]},
        ],
    }
    if alert.link:
        card["actions"] = [{"type": "Action.OpenUrl", "title": "Open in MyAppsUptime", "url": alert.link}]
    return {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}


def build_payload(channel: NotificationChannel, alert: Alert) -> dict:
    text = f"[{alert.status.upper()}] {alert.title}: {alert.message}"
    if channel.type == "teams":
        return teams_payload(alert)
    if channel.type == "webhook":
        return {"monitor": alert.monitor, "status": alert.status, "message": alert.message,
                "project": alert.project, "link": alert.link, "text": text}
    return {"text": text + (f"\n{alert.link}" if alert.link else "")}  # Slack


def _one_line(value: str) -> str:
    return " ".join(value.split())


def build_email(alert: Alert, sender: str, recipients: list[str]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = _one_line(f"[{alert.status.upper()}] {alert.title}")
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    lines = [alert.title, ""] + [f"{k}: {v}" for k, v in alert.facts()]
    if alert.link:
        lines += ["", alert.link]
    msg.set_content("\n".join(lines))
    rows = "".join(
        f'<tr><td style="padding:4px 12px 4px 0;color:#64748b">{escape(k)}</td><td style="padding:4px 0">{escape(v)}</td></tr>'
        for k, v in alert.facts()
    )
    button = (f'<p><a href="{escape(alert.link)}" style="background:#2563eb;color:#fff;padding:8px 14px;'
              f'border-radius:6px;text-decoration:none">Open in MyAppsUptime</a></p>') if alert.link else ""
    msg.add_alternative(
        f'<div style="font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#0f172a">'
        f'<h2 style="margin:0 0 12px;color:{COLORS.get(alert.status, "#0f172a")}">{escape(alert.title)}</h2>'
        f'<table style="border-collapse:collapse">{rows}</table>{button}</div>',
        subtype="html",
    )
    return msg


def _send_mail(smtp: dict, msg: EmailMessage) -> None:
    context = ssl.create_default_context()
    port = int(smtp.get("port") or 587)
    if smtp.get("security") == "ssl":
        server = smtplib.SMTP_SSL(smtp["host"], port, timeout=15, context=context)
    else:
        server = smtplib.SMTP(smtp["host"], port, timeout=15)
    with server:
        if smtp.get("security") == "starttls":
            server.starttls(context=context)
        if smtp.get("username"):
            server.login(smtp["username"], smtp.get("password", ""))
        server.send_message(msg)


def smtp_ready(smtp: dict | None) -> bool:
    return bool(smtp and smtp.get("host") and smtp.get("from_addr"))


async def send_email(smtp: dict | None, alert: Alert, recipients: list[str]) -> str | None:
    recipients = sorted({r.strip() for r in recipients if r and r.strip()})
    if not recipients:
        return None
    if not smtp_ready(smtp):
        return "Email is not set up (Settings > Alerts > Email server)"
    try:
        await asyncio.to_thread(_send_mail, smtp, build_email(alert, smtp["from_addr"], recipients))
    except (OSError, smtplib.SMTPException) as exc:
        log.warning("Email to %s failed: %s", recipients, exc)
        return f"Email failed: {exc}"
    return None


async def send_text(smtp: dict | None, recipients: list[str], subject: str, body: str) -> str | None:
    if not smtp_ready(smtp):
        return "Email is not set up"
    msg = EmailMessage()
    msg["Subject"] = _one_line(subject)
    msg["From"] = smtp["from_addr"]
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)
    try:
        await asyncio.to_thread(_send_mail, smtp, msg)
    except (OSError, smtplib.SMTPException) as exc:
        return f"Email failed: {exc}"
    return None


async def send(channels: list[NotificationChannel], alert: Alert, emails: list[str] | None = None,
               smtp: dict | None = None) -> list[str]:
    """Deliver an alert to webhook channels and email recipients. Returns error messages."""
    errors: list[str] = []
    recipients = list(emails or [])
    hooks = []
    for channel in channels:
        if channel.type == "email":
            recipients += channel.recipients or []
        else:
            hooks.append(channel)
    if hooks:
        async with httpx.AsyncClient(timeout=10) as client:
            for channel in hooks:
                try:
                    resp = await client.post(channel.url, json=build_payload(channel, alert))
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    log.warning("Notification '%s' failed: %s", channel.name, exc)
                    errors.append(f"{channel.name}: {exc}")
    error = await send_email(smtp, alert, recipients)
    if error:
        errors.append(error)
    return errors
