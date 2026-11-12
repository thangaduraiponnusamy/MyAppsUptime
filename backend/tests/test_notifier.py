import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from app import notifier
from app.schemas import ChannelIn, GeneralSettings

TOKEN = "123456:" + "A" * 35
TG_URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage?chat_id=-100123"


def channel(type_, url=""):
    return type("C", (), {"type": type_, "url": url, "name": type_})()


@pytest.fixture
def alert():
    ctx = notifier.alert_context({"app_url": "https://uptime.example.com", "timezone": "Asia/Kolkata"})
    return notifier.Alert(monitor="G Drive", status="up", message="HTTP 200", project="Team",
                          target="https://drive.example.com/", link="https://uptime.example.com/monitors/7",
                          at=datetime(2026, 11, 12, 11, 8, 56, tzinfo=timezone.utc), **ctx)


def mock_client(monkeypatch, handler):
    orig = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: orig(transport=httpx.MockTransport(handler)))


def test_teams_and_time(alert):
    assert alert.local_time == "2026-11-12 16:38:56 (Asia/Kolkata)"
    card = notifier.teams_payload(alert)["attachments"][0]["content"]
    assert [x["title"] for x in card["actions"]] == ["View monitor", "Open MyAppsUptime"]
    bad = notifier.Alert(monitor="X", status="down", message="m", tz="Bad/Zone")
    assert bad.local_time.endswith("(UTC)") and bad.buttons() == []


def test_email(alert):
    raw = notifier.build_email(alert, "from@x.com", ["to@x.com"]).as_string()
    assert "cid:mau-logo" in raw and "16:38:56" in raw


def test_warning_alert_headline():
    a = notifier.Alert(monitor="Site", status="warning", message="m", headline="SSL certificate expires in 7 days")
    assert a.title == "Site: SSL certificate expires in 7 days"
    assert notifier.discord_payload(a)["embeds"][0]["color"] == 0xF97316
    assert notifier.ntfy_payload(a, "t")["tags"] == ["warning"]


def test_validators():
    assert GeneralSettings(timezone="Asia/Kolkata").timezone == "Asia/Kolkata"
    with pytest.raises(ValueError):
        GeneralSettings(timezone="Nope/Zone")
    assert GeneralSettings(expiry_warn_days=[7, 30, 14, 7]).expiry_warn_days == [30, 14, 7]
    ChannelIn(name="tg", type="telegram", url=TG_URL)
    for typ, good, bad in [
        ("telegram", TG_URL, TG_URL.replace("-100123", "x y")),
        ("discord", "https://discord.com/api/webhooks/123/abc-DEF_1", "https://evil.com/api/webhooks/1/x"),
        ("googlechat", "https://chat.googleapis.com/v1/spaces/AAA/messages?key=k&token=t", "https://chat.googleapis.com/x"),
        ("ntfy", "https://ntfy.sh/my-alerts", "https://ntfy.sh/"),
    ]:
        ChannelIn(name="c", type=typ, url=good)
        with pytest.raises(ValueError):
            ChannelIn(name="c", type=typ, url=bad)


def test_payloads(alert):
    tp = notifier.telegram_payload(alert, "-100123")
    assert "<b>Status:</b>" in tp["text"] and len(tp["reply_markup"]["inline_keyboard"][0]) == 2
    d = notifier.build_payload(channel("discord"), alert)["embeds"][0]
    assert d["color"] == 0x22C55E and "[View monitor](" in d["description"]
    n = notifier.build_payload(channel("ntfy", "https://n.example.com/sub/my-alerts"), alert)
    assert n["topic"] == "my-alerts" and len(n["actions"]) == 2
    assert notifier.endpoint(channel("ntfy", "https://n.example.com/sub/my-alerts")) == "https://n.example.com/sub/"


def test_telegram_retry_and_token_masked(monkeypatch, alert):
    calls = []

    def handler(req):
        calls.append(req.content)
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})

    mock_client(monkeypatch, handler)

    async def run():
        async with httpx.AsyncClient() as c:
            with pytest.raises(RuntimeError) as err:
                await notifier._send_telegram(c, channel("telegram", TG_URL), alert)
            return str(err.value)

    message = asyncio.run(run())
    assert TOKEN not in message and "chat not found" in message
    assert len(calls) == 2 and b"reply_markup" not in calls[1]


def test_errors_hide_secrets(monkeypatch, alert):
    mock_client(monkeypatch, lambda req: httpx.Response(404))
    errors = asyncio.run(notifier.send([channel("discord", "https://discord.com/api/webhooks/1/SECRET")], alert))
    assert errors == ["discord: HTTP 404"]
