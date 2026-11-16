import asyncio

from pywebpush import WebPushException

from conftest import auth

from app import notifier, push, scheduler, tenancy
from app.models import PushSubscription

KEYS = {"p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM",
        "auth": "tBHItJI5svbpez7KI4CCXg"}


class FakeResponse:
    def __init__(self, code):
        self.status_code = code


def fake_push(monkeypatch, fail=None):
    sent = []

    def webpush(subscription_info, data, **_):
        if fail and fail(subscription_info["endpoint"]):
            raise WebPushException("gone", response=FakeResponse(fail(subscription_info["endpoint"])))
        sent.append((subscription_info["endpoint"], data))

    monkeypatch.setattr(push, "webpush", webpush)
    monkeypatch.setattr(push, "check_endpoint", lambda endpoint: None)
    return sent


def member(client, su, name, **access):
    body = {"username": name, "password": "Push12345", "role": "member", **access}
    assert client.post("/api/users", json=body, headers=su).status_code == 201
    token = client.post("/api/auth/login", json={"username": name, "password": "Push12345"}).json()["token"]
    client.post("/api/auth/password", json={"current": "Push12345", "new": "Push123456"}, headers=auth(token))
    return auth(token)


def subscribe(client, headers, endpoint):
    r = client.post("/api/push/subscribe", json={"endpoint": endpoint, "keys": KEYS}, headers=headers)
    assert r.status_code == 204, r.text


def test_key_and_subscription(client, su, monkeypatch):
    sent = fake_push(monkeypatch)
    key = client.get("/api/push/key", headers=su).json()["public_key"]
    assert len(key) == 87 and client.get("/api/push/key").status_code == 401

    bad = client.post("/api/push/subscribe", json={"endpoint": "http://push.example.com/x", "keys": KEYS}, headers=su)
    assert bad.status_code == 422
    subscribe(client, su, "https://push.example.com/admin-1")
    subscribe(client, su, "https://push.example.com/admin-1")  # same device twice = one row
    with tenancy.session_for(tenancy.DEFAULT) as db:
        rows = db.query(PushSubscription).filter_by(endpoint_hash=push.endpoint_hash("https://push.example.com/admin-1")).all()
        assert len(rows) == 1 and rows[0].endpoint == "https://push.example.com/admin-1"

    assert client.post("/api/push/test", headers=su).json() == {"sent": 1, "errors": []}
    assert sent[-1][0] == "https://push.example.com/admin-1" and '"url": "/settings/notifications"' in sent[-1][1]

    events = client.get("/api/audit", headers=su).json()
    items = events["items"] if isinstance(events, dict) else events
    assert any(e.get("kind") == "push" for e in items)
    assert not any("push.example.com" in str(e) for e in items)

    r = client.request("DELETE", "/api/push/subscribe", json={"endpoint": "https://push.example.com/admin-1"}, headers=su)
    assert r.status_code == 204
    assert client.post("/api/push/test", headers=su).status_code == 404


def test_alerts_go_to_permitted_users_only(client, su, monkeypatch):
    sent = fake_push(monkeypatch, fail=lambda e: 410 if e.endswith("/gone") else None)
    monkeypatch.setattr(notifier, "send", lambda *a, **k: asyncio.sleep(0, result=[]))
    m = client.post("/api/monitors", json={"name": "push-site", "type": "http", "target": "https://site.example.com"},
                    headers=su).json()
    allowed = member(client, su, "push-allowed", monitor_ids=[m["id"]])
    other = member(client, su, "push-other")
    subscribe(client, allowed, "https://push.example.com/allowed")
    subscribe(client, other, "https://push.example.com/other")
    subscribe(client, su, "https://push.example.com/gone")

    alert = notifier.Alert(monitor="push-site", status="down", message="timeout")
    asyncio.run(scheduler.deliver(tenancy.DEFAULT, m["id"], [], alert, [], None))
    assert [s[0] for s in sent] == ["https://push.example.com/allowed"]
    assert f'"/monitors/{m["id"]}"' in sent[0][1]
    with tenancy.session_for(tenancy.DEFAULT) as db:  # 410 = the browser dropped it
        assert db.query(PushSubscription).filter_by(endpoint_hash=push.endpoint_hash("https://push.example.com/gone")).count() == 0

    sent.clear()  # no push for other statuses
    asyncio.run(scheduler.deliver(tenancy.DEFAULT, m["id"], [], notifier.Alert("push-site", "degraded", "slow"), [], None))
    assert sent == []

    assert client.post(f"/api/monitors/{m['id']}/snooze", json={"minutes": 30}, headers=su).status_code == 200
    asyncio.run(scheduler.deliver(tenancy.DEFAULT, m["id"], [], alert, [], None))
    assert sent == []


def test_errors_are_masked(client, su, monkeypatch):
    fake_push(monkeypatch, fail=lambda e: 500)
    subscribe(client, su, "https://push.example.com/secret-token-123")
    body = client.post("/api/push/test", headers=su).json()
    assert body == {"sent": 0, "errors": ["Push: HTTP 500"]}
    client.request("DELETE", "/api/push/subscribe", json={"endpoint": "https://push.example.com/secret-token-123"}, headers=su)


def test_private_endpoints_are_not_called(monkeypatch):
    called = []
    monkeypatch.setattr(push, "webpush", lambda **k: called.append(k))
    sub = PushSubscription(endpoint="https://127.0.0.1/x", keys="{}")
    assert push._send_one(sub, {}) == 0 and called == []
