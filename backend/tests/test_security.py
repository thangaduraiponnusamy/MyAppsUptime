import asyncio
import ipaddress
import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

from conftest import PASSWORD, auth
import pytest
from sqlalchemy import select

from app import admin_api, auth as auth_mod, checks, login_guard, tenancy
from app.checks import TargetPolicy
from app.models import User, utcnow

STRICT = TargetPolicy()


def monitor(**extra):
    base = {"type": "http", "target": "", "port": None, "method": "GET", "timeout_sec": 2, "retries": 0}
    return SimpleNamespace(**{**base, **extra})


def server_settings(client, su, **changes):
    body = {k: v for k, v in client.get("/api/settings/server", headers=su).json().items()
            if k not in ("backup_dir", "backups")}
    r = client.put("/api/settings/server", json={**body, **changes}, headers=su)
    assert r.status_code == 200, r.text


# ---------- SSRF guard ----------

def test_metadata_always_blocked():
    for ip in ("169.254.169.254", "fd00:ec2::254", "::ffff:169.254.169.254"):
        assert "metadata" in checks.blocked_reason(ip, TargetPolicy(allow_private=True))
    allow_all = TargetPolicy(allow_cidrs=(ipaddress.ip_network("169.254.0.0/16"),))
    assert checks.blocked_reason("169.254.169.254", allow_all) is not None
    r = asyncio.run(checks.run_check(monitor(target="http://169.254.169.254/latest/meta-data/"),
                                     TargetPolicy(allow_private=True)))
    assert r.status == "down" and r.message.startswith("Blocked by SSRF guard")


def test_private_blocked_unless_allowed():
    for ip in ("127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.1.1", "0.0.0.0", "224.0.0.1",
               "::1", "fe80::1", "fc00::1", "::ffff:10.0.0.1"):
        assert checks.blocked_reason(ip, STRICT), ip
    assert checks.blocked_reason("203.0.113.5", STRICT) is None
    assert checks.blocked_reason("10.1.2.3", TargetPolicy(allow_private=True)) is None
    assert checks.blocked_reason("10.1.2.3", TargetPolicy(allow_cidrs=(ipaddress.ip_network("10.1.0.0/16"),))) is None
    assert checks.blocked_reason("10.2.0.1", TargetPolicy(allow_cidrs=(ipaddress.ip_network("10.1.0.0/16"),)))
    r = asyncio.run(checks.run_check(monitor(type="tcp", target="127.0.0.1", port=9), STRICT))
    assert r.status == "down" and "Blocked by SSRF guard" in r.message


def test_monitor_save_rejects_private_target(client, su):
    body = {"name": "intranet", "type": "http", "target": "http://10.20.30.40/health"}
    r = client.post("/api/monitors", json=body, headers=su)
    assert r.status_code == 422 and "Blocked by SSRF guard" in r.json()["detail"]
    server_settings(client, su, allow_private_targets=True)
    try:
        r = client.post("/api/monitors", json=body, headers=su)
        assert r.status_code == 201, r.text
    finally:
        server_settings(client, su, allow_private_targets=False)
    client.delete(f"/api/monitors/{r.json()['id']}", headers=su)
    server_settings(client, su, allowed_cidrs=["10.20.0.0/16"])
    try:
        assert client.post("/api/monitors", json=body, headers=su).status_code == 201
    finally:
        server_settings(client, su, allowed_cidrs=[])
    assert client.put("/api/settings/server", json={"allowed_cidrs": ["nope"]}, headers=su).status_code == 422


def test_probe_monitors_not_restricted(client, su):
    probe = client.post("/api/probes", json={"name": "lan-probe"}, headers=su).json()
    r = client.post("/api/monitors", json={"name": "lan-nas", "type": "tcp", "target": "192.168.5.5", "port": 445,
                                           "probe_id": probe["id"]}, headers=su)
    assert r.status_code == 201, r.text
    # A probe runs checks without a policy
    r = asyncio.run(checks.run_check(monitor(type="tcp", target="127.0.0.1", port=9, timeout_sec=1)))
    assert "SSRF" not in r.message


def test_every_redirect_hop_is_checked():
    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data/")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        policy = TargetPolicy(allow_cidrs=(ipaddress.ip_network("127.0.0.0/8"),))
        r = asyncio.run(checks.run_check(monitor(target=f"http://127.0.0.1:{server.server_port}/"), policy))
        assert r.status == "down" and "metadata" in r.message
    finally:
        server.shutdown()


# ---------- login protection and sessions ----------

def test_lockout_after_failed_logins(client, su):
    body = {"username": "lock-user", "password": "Lock12345", "role": "member"}
    assert client.post("/api/users", json=body, headers=su).status_code == 201
    wrong = {"username": "lock-user", "password": "nope-nope"}
    for _ in range(3):
        r = client.post("/api/auth/login", json=wrong)
        assert r.status_code == 401
    # Right password, but locked: same generic answer
    r = client.post("/api/auth/login", json={"username": "lock-user", "password": "Lock12345"})
    assert r.status_code == 401 and r.json()["detail"] == "Wrong user name or password"
    rows = client.get("/api/audit", params={"username": "lock-user"}, headers=su).json()
    assert [e["action"] for e in rows][:2] == ["sign-in failed", "locked out"]
    with tenancy.session_for(tenancy.DEFAULT) as db:
        user = db.scalar(select(User).where(User.username == "lock-user"))
        user.locked_until = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.post("/api/auth/login", json={"username": "lock-user", "password": "Lock12345"}).status_code == 200


@pytest.fixture
def mails(monkeypatch):
    """Security emails as (recipients, subject, body), with a mail server and app address set up."""
    sent = []
    monkeypatch.setattr(login_guard, "_mail_config", lambda slug: ({"host": "smtp.test", "from_addr": "up@test"},
                                                                   {"app_url": "https://up.example.com"}))
    monkeypatch.setattr(login_guard, "deliver", lambda smtp, to, subject, body: sent.append((to, subject, body)))
    login_guard._sent.clear()
    with tenancy.session_for(tenancy.DEFAULT) as db:
        db.scalar(select(User).where(User.username == "admin")).email = "boss@example.com"
        db.commit()
    yield sent
    with tenancy.session_for(tenancy.DEFAULT) as db:
        db.scalar(select(User).where(User.username == "admin")).email = None
        db.commit()


def test_super_admin_is_never_locked(client):
    for _ in range(4):
        assert client.post("/api/auth/login", json={"username": "admin", "password": "wrong-one"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}).status_code == 200


def test_failed_logins_page_alerts_and_unlock(client, su, mails):
    body = {"username": "guard-user", "password": "Guard12345", "role": "member"}
    assert client.post("/api/users", json=body, headers=su).status_code == 201
    for _ in range(5):
        client.post("/api/auth/login", json={"username": "guard-user", "password": "bad-bad-bad"})
    mine = [s for to, s, _ in mails if "guard-user" in s]
    # Lock email at the 3rd, the 5-in-an-hour email is held back (one per hour per account)
    assert mine == ["MyAppsUptime security: account guard-user locked"] and mails[0][0] == ["boss@example.com"]
    assert "bad-bad-bad" not in str(mails)

    data = client.get("/api/security/failed-logins", headers=su).json()
    rows = [e for e in data["events"] if e["username"] == "guard-user"]
    assert [e["reason"] for e in rows] == ["locked", "locked", "wrong password", "wrong password", "wrong password"]
    assert rows[0]["tenant"] == tenancy.DEFAULT and rows[0]["tenant_name"]
    assert any(a["username"] == "guard-user" and not a["reported"] for a in data["locked"])
    assert not any(a["username"] == "admin" for a in data["locked"])

    client.post("/api/auth/login", json={"username": "no-such-person", "password": "x"})
    events = client.get("/api/security/failed-logins", headers=su).json()["events"]
    assert events[0]["username"] == "no-such-person" and events[0]["reason"] == "unknown user"
    assert not [s for _, s, _ in mails if "no-such-person" in s]
    for _ in range(2):
        client.post("/api/auth/login", json={"username": "no-such-person", "password": "x"})
    assert [s for _, s, _ in mails if "no-such-person" in s] == ["MyAppsUptime security: 3 failed sign-ins for no-such-person"]

    plain = {"username": "guard-member", "password": "Member1234", "role": "member"}
    assert client.post("/api/users", json=plain, headers=su).status_code == 201
    member = client.post("/api/auth/login", json={"username": "guard-member", "password": "Member1234"}).json()["token"]
    assert client.get("/api/security/failed-logins", headers=auth(member)).status_code == 403
    unlock = {"tenant": tenancy.DEFAULT, "username": "guard-user"}
    assert client.post("/api/security/accounts/unlock", json=unlock, headers=auth(member)).status_code == 403
    assert client.post("/api/security/accounts/unlock", json=unlock, headers=su).status_code == 200
    assert client.post("/api/auth/login", json={"username": "guard-user", "password": "Guard12345"}).status_code == 200
    rows = client.get("/api/audit", params={"q": "guard-user", "kind": "security"}, headers=su).json()
    assert rows[0]["action"] == "unlocked"


def test_new_device_email_and_not_me_link(client, su, mails):
    body = {"username": "travel-user", "password": "Travel1234", "role": "member", "email": "t@example.com"}
    assert client.post("/api/users", json=body, headers=su).status_code == 201
    login = {"username": "travel-user", "password": "Travel1234"}
    assert client.post("/api/auth/login", json=login).status_code == 200
    assert client.post("/api/auth/login", json=login).status_code == 200
    assert not [m for m in mails if m[0] == ["t@example.com"]]  # first sign-in and same place: no email
    token = client.post("/api/auth/login", json=login,
                        headers={"User-Agent": "Mozilla/5.0 (Windows) Firefox/130"}).json()["token"]
    to, subject, text = next(m for m in mails if m[0] == ["t@example.com"])
    assert "Firefox on Windows" in text and "Travel1234" not in text
    link = next(line for line in text.splitlines() if "/api/account/not-me/" in line)
    path = link.removeprefix("https://up.example.com")

    assert client.get(path + "x").status_code == 400
    page = client.get(path)
    assert page.status_code == 200 and "Secure my account" in page.text
    assert client.post(path).status_code == 200
    assert client.get("/api/auth/me", headers=auth(token)).status_code == 401
    assert client.post("/api/auth/login", json=login).status_code == 401
    assert any("travel-user reported" in s for _, s, _ in mails)
    locked = client.get("/api/security/failed-logins", headers=su).json()["locked"]
    assert any(a["username"] == "travel-user" and a["reported"] for a in locked)
    client.post("/api/security/accounts/unlock", json={"tenant": tenancy.DEFAULT, "username": "travel-user"}, headers=su)
    assert client.post("/api/auth/login", json=login).status_code == 200


def test_ip_block(client, su, mails, monkeypatch):
    monkeypatch.setattr(login_guard, "BLOCK_FAILS", 3)
    body = {"username": "block-member", "password": "Block12345", "role": "member"}
    assert client.post("/api/users", json=body, headers=su).status_code == 201
    try:
        for _ in range(3):
            client.post("/api/auth/login", json={"username": "spray-x", "password": "x"})
        data = client.get("/api/security/failed-logins", headers=su).json()
        assert [b["ip"] for b in data["blocked"]] == ["testclient"]
        assert any("testclient blocked" in s for _, s, _ in mails)
        # Blocked: same answer for everyone, even with the right password
        for login in ({"username": "block-member", "password": "Block12345"}, {"username": "nobody-y", "password": "x"},
                      {"username": "admin", "password": "wrong-one"}):
            r = client.post("/api/auth/login", json=login)
            assert r.status_code == 429 and "network" in r.json()["detail"]
        # Super admin with the right password still gets in
        assert client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD}).status_code == 200
        reasons = [e["reason"] for e in client.get("/api/security/failed-logins", headers=su).json()["events"][:3]]
        assert reasons == ["wrong password", "blocked IP", "blocked IP"]

        member = {"username": "block-member", "password": "Block12345"}
        assert client.post("/api/security/ips/unblock", json={"ip": "testclient"}, headers=su).status_code == 200
        assert client.get("/api/security/failed-logins", headers=su).json()["blocked"] == []
        token = client.post("/api/auth/login", json=member).json()["token"]
        assert client.post("/api/security/ips/block", json={"ip": "1.2.3.4"}, headers=auth(token)).status_code == 403
        assert client.post("/api/security/ips/unblock", json={"ip": "1.2.3.4"}, headers=auth(token)).status_code == 403
        assert client.post("/api/security/ips/block", json={"ip": "not-an-ip"}, headers=su).status_code == 422
        assert client.post("/api/security/ips/block", json={"ip": "203.0.113.5", "hours": 2}, headers=su).status_code == 200
        blocked = client.get("/api/security/failed-logins", headers=su).json()["blocked"]
        assert [b["ip"] for b in blocked] == ["203.0.113.5"] and blocked[0]["reason"] == "blocked by a super admin"
        client.post("/api/security/ips/unblock", json={"ip": "203.0.113.5"}, headers=su)
    finally:
        client.post("/api/security/ips/unblock", json={"ip": "testclient"}, headers=su)


def test_login_rate_limit(client, monkeypatch):
    monkeypatch.setattr(admin_api, "LOGIN_USER_PER_MIN", 3)
    codes = [client.post("/api/auth/login", json={"username": "rate-x", "password": "x"}).status_code
             for _ in range(4)]
    assert codes == [401, 401, 401, 429]
    auth_mod._hits.clear()


def test_sessions_list_and_revoke(client, su):
    body = {"username": "sess-user", "password": "Sess12345", "role": "member"}
    assert client.post("/api/users", json=body, headers=su).status_code == 201
    login = {"username": "sess-user", "password": "Sess12345"}
    t1 = client.post("/api/auth/login", json=login, headers={"User-Agent": "Mozilla/5.0 (Windows) Firefox/130"}).json()["token"]
    t2 = client.post("/api/auth/login", json=login).json()["token"]
    t3 = client.post("/api/auth/login", json=login).json()["token"]
    sessions = client.get("/api/auth/sessions", headers=auth(t1)).json()
    assert len(sessions) == 3 and sessions[0]["current"] and sessions[0]["device"] == "Firefox on Windows"
    other = next(s for s in sessions if not s["current"])
    assert client.delete(f"/api/auth/sessions/{other['id']}", headers=auth(t1)).status_code == 204
    assert len(client.get("/api/auth/sessions", headers=auth(t1)).json()) == 2
    # Someone else's session id is not found
    assert client.delete(f"/api/auth/sessions/{other['id']}", headers=su).status_code == 404
    assert client.post("/api/auth/sessions/others", headers=auth(t1)).json() == {"revoked": 1}
    assert client.get("/api/auth/me", headers=auth(t1)).status_code == 200
    assert client.get("/api/auth/me", headers=auth(t2)).status_code == 401
    assert client.get("/api/auth/me", headers=auth(t3)).status_code == 401
    actions = [e["action"] for e in client.get("/api/audit", params={"username": "sess-user"}, headers=su).json()]
    assert "session revoked" in actions and "sessions revoked" in actions


# ---------- audit log ----------

def test_audit_records_changes_and_masks_secrets(client, su):
    r = client.post("/api/notifications", json={"name": "audit-hook", "type": "webhook",
                                                 "url": "https://hooks.example.com/abc?key=s3cret"}, headers=su)
    assert r.status_code == 201, r.text
    channel = r.json()
    r = client.put(f"/api/notifications/{channel['id']}", json={"name": "audit-hook-2", "type": "webhook",
                                                               "url": "https://hooks.example.com/new?key=other"},
                   headers=su)
    assert r.status_code == 200, r.text
    rows = client.get("/api/audit", params={"kind": "channel", "q": "audit-hook"}, headers=su).json()
    updated, created = rows[0], rows[1]
    assert (updated["action"], created["action"]) == ("updated", "created")
    assert updated["object_id"] == str(channel["id"]) and created["object_id"] == str(channel["id"])
    changes = json.loads(updated["changes"])
    assert changes["name"] == ["audit-hook", "audit-hook-2"] and changes["url"] == "changed"
    assert "s3cret" not in json.dumps(rows) and "other" not in updated["changes"]
    assert updated["tenant"] == tenancy.DEFAULT and updated["user_agent"]

    r = client.post("/api/monitors", json={"name": "audit-url", "type": "http",
                                           "target": "https://bob:pw@site.example.com/x?token=abc"}, headers=su)
    assert r.status_code == 201, r.text
    row = client.get("/api/audit", params={"q": "audit-url"}, headers=su).json()[0]
    assert json.loads(row["changes"])["target"] == "https://site.example.com/x?***"

    client.put("/api/settings/smtp", json={"host": "smtp.example.com", "port": 587, "security": "starttls",
                                          "username": "mailer", "password": "Sm7pSecret", "from_addr": ""}, headers=su)
    row = client.get("/api/audit", params={"kind": "settings"}, headers=su).json()[0]
    assert row["target"] == "smtp" and "Sm7pSecret" not in row["changes"]

    csv = client.get("/api/audit/export", params={"q": "audit-hook"}, headers=su)
    assert csv.status_code == 200 and csv.text.startswith("created_at,tenant,username")
    assert "s3cret" not in csv.text


def test_audit_log_is_append_only(client, su):
    for method in ("put", "delete", "patch"):
        assert getattr(client, method)("/api/audit/1", headers=su).status_code in (404, 405)


def test_password_change_audit_has_no_passwords(client, su):
    body = {"username": "pw-audit", "password": "OldPass123", "role": "member"}
    assert client.post("/api/users", json=body, headers=su).status_code == 201
    token = client.post("/api/auth/login", json={"username": "pw-audit", "password": "OldPass123"}).json()["token"]
    r = client.post("/api/auth/password", json={"current": "OldPass123", "new": "NewPass456"}, headers=auth(token))
    assert r.status_code < 300, r.text
    rows = client.get("/api/audit", params={"username": "pw-audit", "action": "changed password"}, headers=su).json()
    assert rows and "OldPass123" not in json.dumps(rows) and "NewPass456" not in json.dumps(rows)


def test_login_events_are_audited(client, su):
    client.post("/api/auth/login", json={"username": "nobody-here", "password": "x"})
    row = client.get("/api/audit", params={"username": "nobody-here"}, headers=su).json()[0]
    assert (row["action"], row["detail"]) == ("sign-in failed", "unknown user")
    rows = client.get("/api/audit", params={"action": "signed in"}, headers=su).json()
    assert rows and all(r["action"] == "signed in" for r in rows)
