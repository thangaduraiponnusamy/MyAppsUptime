import logging

import httpx

from .models import NotificationChannel

log = logging.getLogger(__name__)


def channel_matches(channel: NotificationChannel, monitor_tags: list[str]) -> bool:
    return not channel.tags or bool(set(channel.tags) & set(monitor_tags or []))


def build_payload(channel: NotificationChannel, monitor_name: str, status: str, message: str) -> dict:
    text = f"[MyAppsUptime] [{status.upper()}] {monitor_name}: {message}"
    if channel.type == "webhook":
        return {"monitor": monitor_name, "status": status, "message": message, "text": text}
    return {"text": text}  # Slack and Teams incoming webhooks


async def send(channels: list[NotificationChannel], monitor_name: str, status: str, message: str) -> None:
    if not channels:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        for channel in channels:
            try:
                resp = await client.post(channel.url, json=build_payload(channel, monitor_name, status, message))
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("Notification '%s' failed: %s", channel.name, exc)
