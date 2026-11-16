from datetime import timedelta

from conftest import auth

from app import tenancy
from app.models import AuditEvent, Heartbeat, utcnow

BODY = {"name": "Shop", "type": "http", "target": "http://203.0.113.9:9"}


def _monitor(client, su, **extra):
    r = client.post("/api/monitors", json={**BODY, **extra}, headers=su)
    assert r.status_code == 201
    return r.json()


def test_badge_enable_and_disable(client, su):
    m = _monitor(client, su)
    assert m["badge_enabled"] is False and m["badge_token"] is None

    m = client.put(f"/api/monitors/{m['id']}", json={**BODY, "badge_enabled": True}, headers=su).json()
    token = m["badge_token"]
    assert m["badge_enabled"] is True and token.startswith("admin.")

    r = client.get(f"/api/badge/{token}/status.svg")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["cache-control"] == "public, max-age=60"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"].startswith("default-src 'none'")
    assert ">Shop<" in r.text and ">pending<" in r.text
    assert client.get(f"/api/badge/{token}/latency.svg").status_code == 200

    with tenancy.session_for(tenancy.DEFAULT) as db:
        actions = db.query(AuditEvent.action).filter(AuditEvent.object_id == str(m["id"])).all()
    assert ("enabled badge",) in actions

    m = client.put(f"/api/monitors/{m['id']}", json={**BODY, "badge_enabled": False}, headers=su).json()
    assert m["badge_enabled"] is False and m["badge_token"] is None
    r = client.get(f"/api/badge/{token}/status.svg")
    assert r.status_code == 404 and "svg" not in r.headers["content-type"]


def test_badge_label_is_escaped(client, su):
    token = _monitor(client, su, badge_enabled=True)["badge_token"]
    r = client.get(f"/api/badge/{token}/status.svg", params={"label": "<script>alert(1)</script>"})
    assert r.status_code == 200
    assert "<script>" not in r.text and "&lt;script&gt;" in r.text


def test_badge_uptime_ranges(client, su):
    m = _monitor(client, su, badge_enabled=True)
    now = utcnow()
    with tenancy.session_for(tenancy.DEFAULT) as db:
        db.add(Heartbeat(monitor_id=m["id"], status="up", created_at=now - timedelta(hours=1)))
        db.add(Heartbeat(monitor_id=m["id"], status="down", created_at=now - timedelta(days=3)))
        db.commit()
    url = f"/api/badge/{m['badge_token']}/uptime.svg"
    assert ">uptime 24h<" in (day := client.get(url, params={"range": "24h"}).text) and ">100" in day
    assert ">uptime 7d<" in (week := client.get(url, params={"range": "7d"}).text) and ">50" in week
    assert ">uptime 30d<" in client.get(url).text
    assert client.get(url, params={"range": "1y"}).status_code == 422


def test_badge_unknown_or_wrong_tenant(client, su):
    token = _monitor(client, su, badge_enabled=True)["badge_token"]
    body = {"slug": "badgeco", "name": "Badge Co", "admin_username": "badge-admin", "admin_password": "Badge12345"}
    assert client.post("/api/tenants", json=body, headers=su).status_code == 201
    other = client.post("/api/monitors", json={**BODY, "badge_enabled": True},
                        headers=auth(client.token, "badgeco")).json()["badge_token"]
    assert other.startswith("badgeco.")
    assert client.get(f"/api/badge/{other}/status.svg").status_code == 200

    rand = token.partition(".")[2]
    assert client.get(f"/api/badge/badgeco.{rand}/status.svg").status_code == 404
    assert client.get(f"/api/badge/nope.{rand}/status.svg").status_code == 404
    assert client.get("/api/badge/garbage/status.svg").status_code == 404
