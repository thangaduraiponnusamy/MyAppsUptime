import asyncio
import socket
from datetime import date, datetime, timedelta, timezone

from conftest import auth

from app import api, backup, dnscache, expiry, notifier, scheduler, tenancy
from app.checks import CheckResult
from app.models import Incident, Monitor

NOW = datetime.now(timezone.utc)


def make_monitor(client, su, **extra):
    body = {"name": "site", "type": "http", "target": "https://site.example.com", **extra}
    r = client.post("/api/monitors", json=body, headers=su)
    assert r.status_code == 201, r.text
    return r.json()


def record(monitor_id, status, message="x"):
    asyncio.run(scheduler.record_result(tenancy.DEFAULT, monitor_id, CheckResult(status, 10.0, message)))


def monitor(client, su, monitor_id):
    return client.get(f"/api/monitors/{monitor_id}", headers=su).json()


def test_team_picks_its_channels(client, su):
    def add(name):
        body = {"name": name, "type": "ntfy", "url": f"https://ntfy.sh/{name}"}
        r = client.post("/api/notifications", json=body, headers=su)
        assert r.status_code == 201, r.text
        return r.json()["id"]

    mail, tg = add("team-mail"), add("team-tg")
    team = client.post("/api/projects", json={"name": "Picky", "channel_ids": [tg]}, headers=su).json()
    assert team["channel_ids"] == [tg]
    m = make_monitor(client, su, project_id=team["id"])
    with tenancy.session_for(tenancy.DEFAULT) as db:
        channels, *_ = scheduler.build_alert(db, tenancy.DEFAULT, db.get(Monitor, m["id"]), "down", "x")
    assert [c.id for c in channels] == [tg]
    assert client.post("/api/projects", json={"name": "Bad", "channel_ids": [99999]}, headers=su).status_code == 422
    client.delete(f"/api/notifications/{tg}", headers=su)
    team = next(p for p in client.get("/api/projects", headers=su).json() if p["id"] == team["id"])
    assert team["channel_ids"] == []
    client.delete(f"/api/notifications/{mail}", headers=su)


def test_down_only_after_n_failed_checks(client, su):
    m = make_monitor(client, su, down_after=3)
    record(m["id"], "up")
    for n in (1, 2):
        record(m["id"], "down", "refused")
        got = monitor(client, su, m["id"])
        assert got["status"] == "pending" and got["fail_count"] == n
        assert f"({n} of 3)" in got["last_message"]
    record(m["id"], "down", "refused")
    assert monitor(client, su, m["id"])["status"] == "down"
    record(m["id"], "up")
    got = monitor(client, su, m["id"])
    assert got["status"] == "up" and got["fail_count"] == 0
    with tenancy.session_for(tenancy.DEFAULT) as db:
        assert db.query(Incident).filter_by(monitor_id=m["id"]).count() == 1


def test_general_default_for_failed_checks(client, su):
    general = client.get("/api/settings/general", headers=su).json()
    assert client.put("/api/settings/general", json={**general, "down_after_checks": 1}, headers=su).status_code == 200
    m = make_monitor(client, su)
    record(m["id"], "down")
    assert monitor(client, su, m["id"])["status"] == "down"
    client.put("/api/settings/general", json=general, headers=su)


def test_expiry_levels():
    levels = [30, 14, 7]
    assert expiry.next_warning(40, None, levels) == (False, None)
    assert expiry.next_warning(29, None, levels) == (True, 30)
    assert expiry.next_warning(20, 30, levels) == (False, 30)
    assert expiry.next_warning(13, 30, levels) == (True, 14)
    assert expiry.next_warning(-2, 7, levels) == (False, 7)
    assert expiry.next_warning(90, 7, levels) == (False, None)  # renewed
    assert expiry.domain_candidates("a.b.example.co.uk")[:2] == ["co.uk", "example.co.uk"]
    assert "expired 2 days ago" in expiry.describe("Domain", NOW - timedelta(days=2, hours=1), NOW)


def test_expiry_job_warns_once_per_level(client, su, monkeypatch):
    m = make_monitor(client, su, name="cert")
    sent = []
    days = {"cert": 13}

    async def fake_cert(host, port, timeout=10):
        return NOW + timedelta(days=days["cert"], hours=1)

    async def fake_domain(host, client_):
        return NOW + timedelta(days=200)

    async def fake_send(channels, alert, emails=None, smtp=None):
        sent.append(alert)
        return []

    monkeypatch.setattr(expiry, "cert_expiry", fake_cert)
    monkeypatch.setattr(expiry, "domain_expiry", fake_domain)
    monkeypatch.setattr(notifier, "send", fake_send)

    def run():
        with tenancy.session_for(tenancy.DEFAULT) as db:
            db.get(Monitor, m["id"]).expiry_checked_at = None
            db.commit()
        asyncio.run(scheduler.check_expiry(tenancy.DEFAULT, None, {}))
        return [a for a in sent if a.monitor == "cert"]

    assert len(run()) == 1 and sent[-1].status == "warning" and "expires in 13 days" in sent[-1].title
    assert len(run()) == 1  # same level: no repeat
    days["cert"] = 6
    assert len(run()) == 2 and "6 days" in sent[-1].title
    got = monitor(client, su, m["id"])
    assert got["domain_expires_at"] and got["ssl_expires_at"]


def test_backup_and_prune(client, data_dir):
    folder = backup.run(date(2026, 1, 10))
    assert (folder / "main.db").stat().st_size > 0 and (folder / "secret.key").exists()
    for day in range(1, 10):
        backup.run(date(2026, 1, day))
    removed = backup.prune(7, date(2026, 1, 10))
    assert sorted(removed) == [f"2026-01-0{d}" for d in (1, 2, 3)]
    assert len(backup.list_all()) == 7
    assert not backup.due(date(2026, 1, 10)) and backup.due(date(2026, 1, 11))


def test_dns_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(dnscache, "_original", lambda *a: calls.append(a) or [("ok",)])
    dnscache.enable(True)
    try:
        assert socket.getaddrinfo("example.test", 443) == [("ok",)]
        socket.getaddrinfo("example.test", 443)
        assert len(calls) == 1
    finally:
        dnscache.enable(False)
    assert not dnscache.enabled()


def test_status_page_domain_logo_and_public_note(client, su):
    m = make_monitor(client, su, name="public")
    record(m["id"], "up")
    page = {"slug": "acme-status", "title": "Acme", "monitor_ids": [m["id"]], "domain": "Status.Example.com"}
    r = client.post("/api/status-pages", json=page, headers=su)
    assert r.status_code == 201 and r.json()["domain"] == "status.example.com"
    assert client.post("/api/status-pages", json={**page, "slug": "other"}, headers=su).status_code == 409
    assert api.page_for_host("status.example.com:443") == "acme-status"
    assert api.page_for_host("unknown.example.com") is None

    logo = "data:image/png;base64,iVBORw0KGgo="
    upd = client.put(f"/api/status-pages/{r.json()['id']}", json={**page, "title": "Acme 2", "logo": logo}, headers=su)
    assert upd.status_code == 200 and upd.json()["title"] == "Acme 2"
    bad = client.put(f"/api/status-pages/{r.json()['id']}", json={**page, "logo": "javascript:alert(1)"}, headers=su)
    assert bad.status_code == 422

    for _ in range(3):
        record(m["id"], "down", "secret cause")
    incident = client.get("/api/incidents", headers=su).json()
    target = next(i for i in incident if i["monitor_id"] == m["id"])
    ack = {"by": "ops", "note": "internal", "public_note": "We are fixing it"}
    assert client.post(f"/api/incidents/{target['id']}/ack", json=ack, headers=su).status_code == 200

    public = client.get("/api/public/status/acme-status").json()
    assert public["logo"] == logo
    assert public["incidents"][0]["note"] == "We are fixing it"
    assert "secret cause" not in str(public) and "internal" not in str(public)
    row = public["monitors"][0]
    assert len(row["bars"]) == len(row["times"]) > 0


def test_heartbeats_custom_range(client, su):
    m = make_monitor(client, su, name="range")
    record(m["id"], "up")
    start, end = (NOW - timedelta(hours=1)).isoformat(), (NOW + timedelta(hours=1)).isoformat()
    r = client.get(f"/api/monitors/{m['id']}/heartbeats", params={"start": start, "end": end}, headers=su)
    assert r.status_code == 200 and len(r.json()) == 1
    old = client.get(f"/api/monitors/{m['id']}/heartbeats", headers=su, params={
        "start": (NOW - timedelta(days=3)).isoformat(), "end": (NOW - timedelta(days=2)).isoformat()})
    assert old.json() == []


def test_shrink_and_clear_stats(client, su):
    m = make_monitor(client, su, name="stats")
    record(m["id"], "up")
    info = client.get("/api/settings/database", headers=su).json()
    assert info["heartbeats"] > 0 and info["size_kb"] > 0
    assert client.post("/api/settings/database/shrink", headers=su).status_code == 200
    cleared = client.post("/api/settings/database/clear-stats", headers=su).json()
    assert cleared["heartbeats"] == 0 and cleared["incidents"] == 0


def test_server_settings_super_admin_only(client, su):
    r = client.get("/api/settings/server", headers=su)
    assert r.status_code == 200 and r.json()["backup_keep_days"] == 7
    saved = client.put("/api/settings/server", json={"backup_enabled": True, "backup_keep_days": 5, "dns_cache": True},
                       headers=su)
    assert saved.json()["backup_keep_days"] == 5 and dnscache.enabled()
    client.put("/api/settings/server", json={"backup_enabled": True, "backup_keep_days": 7, "dns_cache": False},
               headers=su)
    body = {"slug": "gamma", "name": "Gamma", "admin_username": "gamma-admin", "admin_password": "Gamma12345"}
    client.post("/api/tenants", json=body, headers=su)
    token = client.post("/api/auth/login", json={"username": "gamma-admin", "password": "Gamma12345"}).json()["token"]
    client.post("/api/auth/password", json={"current": "Gamma12345", "new": "Gamma123456"}, headers=auth(token))
    assert client.get("/api/settings/server", headers=auth(token)).status_code == 403
    assert client.get("/api/settings/database", headers=auth(token)).status_code == 200


def test_push_monitor(client, su):
    m = make_monitor(client, su, name="cron", type="push", target="")
    token = m["push_token"]
    assert token
    assert client.get(f"/api/public/push/{token}", params={"status": "up", "msg": "OK", "ping": 12}).json()["ok"]
    got = monitor(client, su, m["id"])
    assert got["status"] == "up" and got["last_push_at"]
    assert client.get("/api/public/push/not-a-token").status_code == 404
