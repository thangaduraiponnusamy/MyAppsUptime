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


def test_degraded_only_after_slow_checks_in_a_row(client, su):
    m = make_monitor(client, su)

    def check(latency):
        asyncio.run(scheduler.record_result(tenancy.DEFAULT, m["id"], CheckResult("up", latency, "HTTP 200")))
        return monitor(client, su, m["id"])["status"]

    for i in range(25):
        check(100.0 + i % 5)
    assert check(1500.0) == "up"
    assert check(100.0) == "up"
    assert [check(1500.0) for _ in range(3)] == ["up", "up", "degraded"]
    assert check(100.0) == "up"
    sent = [a["status"] for a in client.get("/api/alerts?hours=1", headers=su).json() if a["monitor_id"] == m["id"]]
    assert sent[:2] == ["up", "degraded"]


def test_every_sent_alert_is_listed(client, su):
    m = make_monitor(client, su, down_after=1)
    record(m["id"], "up")
    record(m["id"], "down", "refused")
    record(m["id"], "down", "refused")  # no change: nothing sent
    record(m["id"], "up")
    rows = [a for a in client.get("/api/alerts", headers=su).json() if a["monitor_id"] == m["id"]]
    assert [a["status"] for a in rows][:2] == ["up", "down"]
    assert rows[1]["name"] == "site" and "refused" in rows[1]["message"] and rows[1]["sent_to"] is not None
    team = client.post("/api/projects", json={"name": "Other team"}, headers=su).json()
    assert all(a["monitor_id"] != m["id"] for a in
               client.get(f"/api/alerts?project_id={team['id']}", headers=su).json())


def test_client_ip_behind_cloudflare():
    from starlette.requests import Request
    from app.admin_api import client_ip

    def req(peer, cf=None):
        headers = [(b"cf-connecting-ip", cf.encode())] if cf else []
        return Request({"type": "http", "client": (peer, 1234), "headers": headers})

    assert client_ip(req("172.70.108.30", "122.172.81.116")) == "122.172.81.116"
    assert client_ip(req("2606:4700::1", "2001:db8::5")) == "2001:db8::5"
    assert client_ip(req("203.0.113.9", "1.2.3.4")) == "203.0.113.9"  # not Cloudflare: header ignored
    assert client_ip(req("162.159.124.5", "bogus")) == "162.159.124.5"


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
    acked = client.post(f"/api/incidents/{target['id']}/ack", json=ack, headers=su)
    assert acked.status_code == 200 and acked.json()["acknowledged_by"] == "admin"
    more = client.post(f"/api/incidents/{target['id']}/ack",
                       json={"note": "restarted", "public_note": "We are fixing it"}, headers=su).json()
    assert [(a["by"], a["note"]) for a in more["acks"]] == [("admin", "internal"), ("admin", "restarted")]

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


def test_sign_in_history(client, su):
    body = {"username": "hist-user", "password": "Hist12345", "role": "member"}
    assert client.post("/api/users", json=body, headers=su).status_code == 201
    assert client.post("/api/auth/login", json={"username": "hist-user", "password": "wrong-pass"}).status_code == 401
    phone = {"User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36"}
    token = client.post("/api/auth/login", json={"username": "hist-user", "password": "Hist12345"},
                        headers=phone).json()["token"]
    client.post("/api/auth/password", json={"current": "Hist12345", "new": "Hist123456"}, headers=auth(token))

    own = client.get("/api/logins", headers=auth(token)).json()
    assert [e["success"] for e in own] == [True, False] and {e["username"] for e in own} == {"hist-user"}
    assert own[0]["device"] == "Chrome on Android"
    everyone = client.get("/api/logins", params={"limit": 500}, headers=su).json()
    assert {"admin", "hist-user"} <= {e["username"] for e in everyone}
    assert len(client.get("/api/logins", params={"limit": 1}, headers=su).json()) == 1
    user = next(u for u in client.get("/api/users", headers=su).json() if u["username"] == "hist-user")
    assert user["last_login_at"]


def test_every_monitor_has_a_project(client, su):
    m = make_monitor(client, su, name="loose")
    projects = {p["name"]: p["id"] for p in client.get("/api/projects", headers=su).json()}
    assert m["project_id"] == projects["General"]
    team = client.post("/api/projects", json={"name": "Short lived"}, headers=su).json()
    moved = make_monitor(client, su, name="moved", project_id=team["id"])
    assert client.delete(f"/api/projects/{team['id']}", headers=su).status_code == 204
    assert monitor(client, su, moved["id"])["project_id"] == projects["General"]
    assert client.delete(f"/api/projects/{projects['General']}", headers=su).status_code == 409


def test_filter_by_several_projects(client, su):
    a = client.post("/api/projects", json={"name": "Multi A"}, headers=su).json()["id"]
    b = client.post("/api/projects", json={"name": "Multi B"}, headers=su).json()["id"]
    c = client.post("/api/projects", json={"name": "Multi C"}, headers=su).json()["id"]
    ma, mb, mc = (make_monitor(client, su, name=f"multi-{x}", project_id=p)["id"] for x, p in (("a", a), ("b", b), ("c", c)))
    seen = {m["id"] for m in client.get(f"/api/monitors?project_id={a}&project_id={b}", headers=su).json()}
    assert ma in seen and mb in seen and mc not in seen
    one = {m["id"] for m in client.get(f"/api/monitors?project_id={c}", headers=su).json()}
    assert one == {mc}
    assert client.get(f"/api/dashboard?project_id={a}&project_id={b}", headers=su).status_code == 200


def test_user_access_to_projects_and_single_services(client, su):
    shop = client.post("/api/projects", json={"name": "Shop", "email_members": True}, headers=su).json()
    ops = client.post("/api/projects", json={"name": "Ops", "email_members": True}, headers=su).json()
    cart = make_monitor(client, su, name="cart", project_id=shop["id"])
    db_mon = make_monitor(client, su, name="db", project_id=ops["id"])
    other = make_monitor(client, su, name="hidden", project_id=ops["id"])
    body = {"username": "access-user", "password": "Access12345", "email": "access@example.com",
            "project_ids": [shop["id"]], "monitor_ids": [db_mon["id"]]}
    r = client.post("/api/users", json=body, headers=su)
    assert r.status_code == 201, r.text
    assert r.json()["project_ids"] == [shop["id"]] and r.json()["monitor_ids"] == [db_mon["id"]]
    assert client.post("/api/users", json={**body, "username": "bad-access", "project_ids": [99999]},
                       headers=su).status_code == 422

    token = client.post("/api/auth/login", json={"username": "access-user", "password": "Access12345"}).json()["token"]
    client.post("/api/auth/password", json={"current": "Access12345", "new": "Access123456"}, headers=auth(token))
    seen = {x["id"] for x in client.get("/api/monitors", headers=auth(token)).json()}
    assert cart["id"] in seen and db_mon["id"] in seen and other["id"] not in seen
    assert {p["name"] for p in client.get("/api/projects", headers=auth(token)).json()} == {"Shop", "Ops"}

    with tenancy.session_for(tenancy.DEFAULT) as db:
        _, _, emails, _ = scheduler.build_alert(db, tenancy.DEFAULT, db.get(Monitor, db_mon["id"]), "down", "x")
        assert "access@example.com" in emails
        _, _, emails, _ = scheduler.build_alert(db, tenancy.DEFAULT, db.get(Monitor, other["id"]), "down", "x")
        assert "access@example.com" not in emails

    uid = r.json()["id"]
    user = next(u for u in client.get("/api/users", headers=su).json() if u["id"] == uid)
    update = {k: user[k] for k in ("username", "name", "email", "role", "active")}
    r = client.put(f"/api/users/{uid}", json={**update, "project_ids": [], "monitor_ids": []}, headers=su)
    assert r.status_code == 200 and r.json()["project_ids"] == [] and r.json()["monitor_ids"] == []
    assert client.get("/api/monitors", headers=auth(token)).json() == []


def test_user_alert_channels(client, su):
    tg = client.post("/api/notifications", json={"name": "my-tg", "type": "ntfy", "url": "https://ntfy.sh/my-tg"},
                     headers=su).json()["id"]
    team = client.post("/api/projects", json={"name": "Kinds", "email_members": True}, headers=su).json()
    m = make_monitor(client, su, name="kinds", project_id=team["id"])
    r = client.post("/api/users", json={"username": "kinds-user", "password": "Kinds12345", "email": "kinds@example.com",
                                        "project_ids": [team["id"]]}, headers=su)
    assert r.json()["alert_channels"] is None
    token = client.post("/api/auth/login", json={"username": "kinds-user", "password": "Kinds12345"}).json()["token"]
    me = client.get("/api/auth/me", headers=auth(token)).json()
    assert {"id": -1, "name": "Email"} in me["alert_options"] and tg in [o["id"] for o in me["alert_options"]]

    def sent():
        with tenancy.session_for(tenancy.DEFAULT) as db:
            channels, _, emails, _ = scheduler.build_alert(db, tenancy.DEFAULT, db.get(Monitor, m["id"]), "down", "x")
        return "kinds@example.com" in emails, tg in [c.id for c in channels]

    assert sent() == (True, True)
    r = client.put("/api/auth/alerts", json={"alert_channels": [tg]}, headers=auth(token))
    assert r.status_code == 200 and r.json()["alert_channels"] == [tg]
    assert sent() == (False, True)
    client.put("/api/auth/alerts", json={"alert_channels": [-1]}, headers=auth(token))
    assert sent() == (True, False)
    client.delete(f"/api/notifications/{tg}", headers=su)


def test_audit_log(client, su):
    m = make_monitor(client, su, name="audited")
    client.post(f"/api/monitors/{m['id']}/pause", headers=su)
    client.put("/api/monitors/999999", json={"name": "x", "type": "http", "target": "https://x.example"}, headers=su)
    client.delete(f"/api/monitors/{m['id']}", headers=su)
    rows = [e for e in client.get("/api/audit", params={"q": "audited"}, headers=su).json()]
    assert [(e["action"], e["kind"], e["target"]) for e in rows] == [
        ("deleted", "monitor", "audited"), ("paused", "monitor", "audited"), ("created", "monitor", "audited")]
    assert rows[0]["username"] == "admin" and rows[0]["ip"] == "testclient"
    general = client.get("/api/settings/general", headers=su).json()
    client.put("/api/settings/general", json=general, headers=su)
    top = client.get("/api/audit", params={"limit": 1}, headers=su).json()[0]
    assert (top["action"], top["kind"], top["target"]) == ("updated", "settings", "general")
    token = client.post("/api/auth/login", json={"username": "hist-user", "password": "Hist123456"}).json()["token"]
    assert client.get("/api/audit", headers=auth(token)).status_code == 403


def test_describe_agent():
    from app.admin_api import describe_agent
    win = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36 Edg/131.0"
    iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Mobile Safari/604.1"
    mac = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:132.0) Gecko/20100101 Firefox/132.0"
    assert describe_agent(win) == "Edge on Windows"
    assert describe_agent(iphone) == "Safari on iPhone"
    assert describe_agent(mac) == "Firefox on macOS"
    assert describe_agent("curl/8.5.0") == "curl"
    assert describe_agent("") == ""


def test_push_monitor(client, su):
    m = make_monitor(client, su, name="cron", type="push", target="")
    token = m["push_token"]
    assert token
    assert client.get(f"/api/public/push/{token}", params={"status": "up", "msg": "OK", "ping": 12}).json()["ok"]
    got = monitor(client, su, m["id"])
    assert got["status"] == "up" and got["last_push_at"]
    assert client.get("/api/public/push/not-a-token").status_code == 404
