import asyncio
import base64
import logging
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from html import escape
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from .models import Monitor, NotificationChannel

log = logging.getLogger(__name__)

COLORS = {"up": "#16a34a", "down": "#dc2626", "degraded": "#d97706", "warning": "#ea580c", "maintenance": "#2563eb"}
LOGO_PNG = Path(__file__).with_name("logo.png").read_bytes()
# Inline image, so it shows even when the server is not reachable from Teams
LOGO_DATA_URI = "data:image/png;base64," + base64.b64encode(LOGO_PNG).decode()


def server_url(general: dict) -> str:
    return (general.get("app_url") or os.getenv("APP_URL", "")).rstrip("/")


def server_tz(general: dict) -> str:
    return general.get("timezone") or os.getenv("TZ") or "UTC"


def alert_context(general: dict) -> dict:
    return {"app_url": server_url(general), "tz": server_tz(general)}


@dataclass
class Alert:
    monitor: str
    status: str
    message: str
    project: str | None = None
    target: str | None = None
    link: str | None = None
    app_url: str = ""
    tz: str = "UTC"
    headline: str = ""  # replaces the default title, e.g. for expiry warnings
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def title(self) -> str:
        if self.headline:
            return f"{self.monitor}: {self.headline}"
        word = {"up": "is back UP", "down": "is DOWN", "degraded": "is DEGRADED"}.get(self.status, self.status.upper())
        return f"{self.monitor} {word}"

    @property
    def local_time(self) -> str:
        try:
            zone, name = ZoneInfo(self.tz), self.tz
        except (ZoneInfoNotFoundError, ValueError):
            zone, name = timezone.utc, "UTC"
        return f"{self.at.astimezone(zone):%Y-%m-%d %H:%M:%S} ({name})"

    @property
    def server(self) -> str:
        return urlsplit(self.app_url).netloc if self.app_url else ""

    def facts(self) -> list[tuple[str, str]]:
        rows = [("Status", self.status.upper()), ("Monitor", self.monitor), ("Team", self.project),
                ("Target", self.target), ("Details", self.message), ("Time", self.local_time)]
        # Empty rows are left out ("-" shows as a bullet in Teams)
        return [(k, v) for k, v in rows if v]

    def buttons(self) -> list[tuple[str, str]]:
        out = []
        if self.link and self.link != self.app_url:
            out.append(("View monitor", self.link))
        if self.app_url:
            out.append(("Open MyAppsUptime", self.app_url))
        return out


def channel_matches(channel: NotificationChannel, monitor: Monitor) -> bool:
    tags_ok = not channel.tags or bool(set(channel.tags) & set(monitor.tags or []))
    projects_ok = not channel.project_ids or monitor.project_id in channel.project_ids
    return tags_ok and projects_ok


def teams_payload(alert: Alert) -> dict:
    style = {"up": "good", "down": "attention", "degraded": "warning", "warning": "warning", "maintenance": "accent"}.get(alert.status, "emphasis")
    color = {"up": "Good", "down": "Attention", "degraded": "Warning", "warning": "Warning", "maintenance": "Accent"}.get(alert.status, "Default")
    body = [
        {"type": "Container", "style": style, "bleed": True, "items": [{"type": "ColumnSet", "columns": [
            {"type": "Column", "width": "auto", "verticalContentAlignment": "Center", "items": [
                {"type": "Image", "url": LOGO_DATA_URI, "width": "40px", "height": "40px", "altText": "MyAppsUptime"}]},
            {"type": "Column", "width": "stretch", "verticalContentAlignment": "Center", "items": [
                {"type": "TextBlock", "text": alert.title, "size": "Large", "weight": "Bolder", "color": color, "wrap": True},
                {"type": "TextBlock", "text": "MyAppsUptime alert", "isSubtle": True, "spacing": "None", "wrap": True}]},
        ]}]},
        {"type": "FactSet", "spacing": "Medium", "facts": [{"title": k, "value": v} for k, v in alert.facts()]},
    ]
    if alert.app_url:
        body.append({"type": "TextBlock", "text": f"Sent by MyAppsUptime on [{alert.server}]({alert.app_url})",
                     "size": "Small", "isSubtle": True, "wrap": True, "separator": True, "spacing": "Medium"})
    card = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "msteams": {"width": "Full"},
        "body": body,
    }
    if alert.buttons():
        card["actions"] = [{"type": "Action.OpenUrl", "title": t, "url": u} for t, u in alert.buttons()]
    return {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}


def telegram_payload(alert: Alert, chat_id: str) -> dict:
    dot = {"up": "\U0001F7E2", "down": "\U0001F534", "degraded": "\U0001F7E0", "warning": "\U0001F7E0", "maintenance": "\U0001F535"}.get(alert.status, "\u26AA")
    lines = [f"{dot} <b>{escape(alert.title)}</b>", ""]
    lines += [f"<b>{escape(k)}:</b> {escape(v)}" for k, v in alert.facts()]
    lines += ["", f"<i>MyAppsUptime{' on ' + escape(alert.server) if alert.server else ''}</i>"]
    payload = {"chat_id": chat_id, "text": "\n".join(lines), "parse_mode": "HTML",
               "link_preview_options": {"is_disabled": True}}
    if alert.buttons():
        payload["reply_markup"] = {"inline_keyboard": [[{"text": t, "url": u} for t, u in alert.buttons()]]}
    return payload


def split_telegram(url: str) -> tuple[str, str]:
    """Stored as https://api.telegram.org/bot<token>/sendMessage?chat_id=<id>"""
    base, _, query = url.partition("?chat_id=")
    return base, query


async def _send_telegram(client: httpx.AsyncClient, channel: NotificationChannel, alert: Alert) -> None:
    url, chat_id = split_telegram(channel.url)
    payload = telegram_payload(alert, chat_id)
    resp = await client.post(url, json=payload)
    if resp.status_code == 400 and "reply_markup" in payload:
        # Telegram rejects buttons with local addresses (localhost, IPs); send without them
        payload.pop("reply_markup")
        resp = await client.post(url, json=payload)
    if resp.is_error:
        try:
            detail = resp.json().get("description", "")
        except ValueError:
            detail = ""
        # Own message: the httpx error text would include the bot token
        raise RuntimeError(f"Telegram HTTP {resp.status_code} {detail}".strip())


def discord_payload(alert: Alert) -> dict:
    color = {"up": 0x22C55E, "down": 0xEF4444, "degraded": 0xF59E0B, "warning": 0xF97316, "maintenance": 0x3B82F6}.get(alert.status, 0x64748B)
    embed = {
        "title": alert.title[:256], "color": color, "timestamp": alert.at.isoformat(),
        "fields": [{"name": k, "value": v[:1024], "inline": k in ("Status", "Team", "Time")} for k, v in alert.facts()],
        "footer": {"text": f"MyAppsUptime{' on ' + alert.server if alert.server else ''}"},
    }
    if alert.buttons():
        embed["description"] = "  |  ".join(f"[{t}]({u})" for t, u in alert.buttons())
    return {"username": "MyAppsUptime", "embeds": [embed], "allowed_mentions": {"parse": []}}


def ntfy_payload(alert: Alert, topic: str) -> dict:
    tag = {"up": "green_circle", "down": "red_circle", "degraded": "orange_circle", "warning": "warning",
           "maintenance": "large_blue_circle"}.get(alert.status, "white_circle")
    payload = {"topic": topic, "title": alert.title, "tags": [tag], "priority": 5 if alert.status == "down" else 3,
               "message": "\n".join(f"{k}: {v}" for k, v in alert.facts() if k != "Monitor")}
    if alert.buttons():
        payload["click"] = alert.buttons()[0][1]
        payload["actions"] = [{"action": "view", "label": t, "url": u} for t, u in alert.buttons()]
    return payload


def endpoint(channel: NotificationChannel) -> str:
    if channel.type == "ntfy":
        # JSON publishing goes to the server root; the topic is in the body
        return channel.url.rstrip("/").rsplit("/", 1)[0] + "/"
    return channel.url


def build_payload(channel: NotificationChannel, alert: Alert) -> dict:
    text = f"[{alert.status.upper()}] {alert.title}: {alert.message}"
    if channel.type == "teams":
        return teams_payload(alert)
    if channel.type == "discord":
        return discord_payload(alert)
    if channel.type == "ntfy":
        return ntfy_payload(alert, channel.url.rstrip("/").rsplit("/", 1)[1])
    if channel.type == "telegram":
        return telegram_payload(alert, split_telegram(channel.url)[1])
    if channel.type == "webhook":
        return {"monitor": alert.monitor, "status": alert.status, "message": alert.message,
                "project": alert.project, "target": alert.target, "link": alert.link, "server": alert.app_url or None,
                "time": alert.at.isoformat(), "time_local": alert.local_time, "text": text}
    lines = [f"*{alert.title}*", alert.message, alert.local_time] + [f"<{u}|{t}>" for t, u in alert.buttons()]
    return {"text": "\n".join(x for x in lines if x)}  # Slack, Google Chat


def _one_line(value: str) -> str:
    return " ".join(value.split())


def build_email(alert: Alert, sender: str, recipients: list[str]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = _one_line(f"[{alert.status.upper()}] {alert.title}")
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    lines = [alert.title, ""] + [f"{k}: {v}" for k, v in alert.facts()]
    lines += [""] + [f"{t}: {u}" for t, u in alert.buttons()]
    msg.set_content("\n".join(lines))

    color = COLORS.get(alert.status, "#475569")
    rows = "".join(
        f'<tr><td style="padding:6px 16px 6px 0;color:#64748b;white-space:nowrap;vertical-align:top">{escape(k)}</td>'
        f'<td style="padding:6px 0;color:#0f172a">{escape(v)}</td></tr>'
        for k, v in alert.facts()
    )
    buttons = "".join(
        f'<a href="{escape(u)}" style="display:inline-block;margin:0 8px 8px 0;padding:9px 16px;border-radius:6px;'
        f'text-decoration:none;font-weight:600;{"background:#2563eb;color:#ffffff" if i == 0 else "border:1px solid #cbd5e1;color:#0f172a"}">'
        f'{escape(t)}</a>'
        for i, (t, u) in enumerate(alert.buttons())
    )
    footer = (f'Sent by MyAppsUptime on <a href="{escape(alert.app_url)}" style="color:#64748b">{escape(alert.server)}</a>'
              if alert.app_url else "Sent by MyAppsUptime")
    msg.add_alternative(
        f'<div style="background:#f1f5f9;padding:24px 12px;font-family:Segoe UI,Arial,sans-serif;font-size:14px">'
        f'<table role="presentation" width="100%" style="max-width:560px;margin:0 auto;background:#ffffff;'
        f'border:1px solid #e2e8f0;border-radius:8px;border-collapse:separate;overflow:hidden">'
        f'<tr><td style="border-top:4px solid {color};padding:20px 24px 8px">'
        f'<table role="presentation"><tr>'
        f'<td style="padding-right:12px"><img src="cid:mau-logo" width="40" height="40" alt="MyAppsUptime" '
        f'style="display:block;border-radius:8px"></td>'
        f'<td><div style="font-size:18px;font-weight:700;color:{color}">{escape(alert.title)}</div>'
        f'<div style="color:#64748b;font-size:12px">MyAppsUptime alert</div></td></tr></table></td></tr>'
        f'<tr><td style="padding:8px 24px"><table role="presentation" style="border-collapse:collapse">{rows}</table></td></tr>'
        f'<tr><td style="padding:8px 24px 12px">{buttons}</td></tr>'
        f'<tr><td style="padding:12px 24px;border-top:1px solid #e2e8f0;color:#64748b;font-size:12px">{footer}</td></tr>'
        f'</table></div>',
        subtype="html",
    )
    msg.get_payload()[1].add_related(LOGO_PNG, "image", "png", cid="<mau-logo>")
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
                    if channel.type == "telegram":
                        await _send_telegram(client, channel, alert)
                        continue
                    resp = await client.post(endpoint(channel), json=build_payload(channel, alert))
                    resp.raise_for_status()
                except (httpx.HTTPError, RuntimeError) as exc:
                    # httpx error texts contain the URL, which holds the channel's secret token
                    if isinstance(exc, httpx.HTTPStatusError):
                        exc = f"HTTP {exc.response.status_code}"
                    elif isinstance(exc, httpx.HTTPError):
                        exc = type(exc).__name__
                    log.warning("Notification '%s' failed: %s", channel.name, exc)
                    errors.append(f"{channel.name}: {exc}")
    error = await send_email(smtp, alert, recipients)
    if error:
        errors.append(error)
    return errors
