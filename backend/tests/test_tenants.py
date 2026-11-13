import pytest
from conftest import auth

from app import tenancy


def test_tenant_lifecycle(client, su, data_dir):
    body = {"slug": "acme", "name": "Acme", "admin_username": "acme-admin", "admin_password": "Acme12345"}
    assert client.post("/api/tenants", json=body, headers=su).status_code == 201
    assert (data_dir / "tenants" / "acme.db").exists()
    assert client.post("/api/tenants", json=body, headers=su).status_code == 409

    token = client.post("/api/auth/login", json={"username": "acme-admin", "password": "Acme12345"}).json()["token"]
    client.post("/api/auth/password", json={"current": "Acme12345", "new": "Acme123456"}, headers=auth(token))
    r = client.post("/api/monitors", json={"name": "acme", "type": "http", "target": "http://127.0.0.1:9"},
                    headers=auth(token))
    assert r.status_code == 201
    assert len(client.get("/api/monitors", headers=auth(token)).json()) == 1
    assert len(client.get("/api/monitors", headers=auth(token, "admin")).json()) == 1  # X-Tenant ignored
    assert len(client.get("/api/monitors", headers=auth(client.token, "acme")).json()) == 1
    assert client.get("/api/tenants", headers=auth(token)).status_code == 403

    assert client.delete("/api/tenants/acme", headers=su).status_code == 204
    assert not (data_dir / "tenants" / "acme.db").exists()
    assert client.get("/api/monitors", headers=auth(token)).status_code == 401


def test_tenant_allows_multiple_admins(client, su):
    body = {"slug": "multi", "name": "Multi", "admin_username": "multi-admin", "admin_password": "Multi12345"}
    assert client.post("/api/tenants", json=body, headers=su).status_code == 201
    as_su = auth(client.token, "multi")
    second = {"username": "multi-admin2", "role": "admin", "password": "Multi12345"}
    r = client.post("/api/users", json=second, headers=as_su)
    assert r.status_code == 201
    member = client.post("/api/users", json={"username": "multi-member", "password": "Multi12345"}, headers=as_su)
    assert client.put(f"/api/users/{member.json()['id']}", json={"username": "multi-member", "role": "admin"},
                      headers=as_su).status_code == 200
    listed = {t["slug"]: t for t in client.get("/api/tenants", headers=su).json()}
    assert listed["multi"]["admins"] == ["multi-admin", "multi-admin2", "multi-member"]


def test_tenant_admin_resets_password(client, su):
    body = {"slug": "rst", "name": "Rst", "admin_username": "rst-admin", "admin_password": "Rst123456"}
    assert client.post("/api/tenants", json=body, headers=su).status_code == 201
    token = client.post("/api/auth/login", json={"username": "rst-admin", "password": "Rst123456"}).json()["token"]
    client.post("/api/auth/password", json={"current": "Rst123456", "new": "Rst1234567"}, headers=auth(token))
    user = client.post("/api/users", json={"username": "rst-user", "password": "User12345"}, headers=auth(token))
    uid = user.json()["id"]
    r = client.put(f"/api/users/{uid}", json={"username": "rst-user", "password": "NewPass123"}, headers=auth(token))
    assert r.status_code == 200
    assert client.post("/api/auth/login", json={"username": "rst-user", "password": "User12345"}).status_code == 401
    login = client.post("/api/auth/login", json={"username": "rst-user", "password": "NewPass123"})
    assert login.status_code == 200 and login.json()["must_change_password"] is True


def test_deleted_tenant_is_not_recreated():
    with pytest.raises(LookupError):
        tenancy.session_for("ghost")
    with pytest.raises(LookupError):
        tenancy.session_for("../evil", create=True)
    assert not tenancy.db_path("ghost").exists()


def test_tenant_alerts_fall_back_to_admin_settings(client, su):
    from app.scheduler import tenant_config
    client.put("/api/settings/general", json={"app_url": "https://up.example.com", "timezone": "Europe/Berlin"},
               headers=su)
    body = {"slug": "beta", "name": "Beta", "admin_username": "beta-admin", "admin_password": "Beta12345"}
    assert client.post("/api/tenants", json=body, headers=su).status_code == 201
    with tenancy.session_for("beta") as db:
        general, _ = tenant_config(db, "beta")
    assert general["app_url"] == "https://up.example.com" and general["timezone"] == "Europe/Berlin"
    defaults = client.get("/api/settings/defaults", headers=auth(client.token, "beta")).json()
    assert defaults["app_url"] == "https://up.example.com" and defaults["timezone"] == "Europe/Berlin"
    assert client.get("/api/settings/defaults", headers=su).json()["app_url"] == ""


def test_missing_tenant_file_is_skipped(client, su, data_dir):
    body = {"slug": "gone", "name": "Gone", "admin_username": "gone-admin", "admin_password": "Gone12345"}
    assert client.post("/api/tenants", json=body, headers=su).status_code == 201
    tenancy.archive("gone")  # file removed by hand, row still there
    tenancy.load()
    assert "gone" not in tenancy.slugs(active_only=False)
    listed = {t["slug"]: t for t in client.get("/api/tenants", headers=su).json()}
    assert listed["gone"]["db_file"] == "missing"
    assert client.get("/api/monitors", headers=auth(client.token, "gone")).status_code == 200
    assert client.delete("/api/tenants/gone", headers=su).status_code == 204


def test_disabled_tenant_sign_in_message(client, su):
    body = {"slug": "paused", "name": "Paused", "admin_username": "paused-admin", "admin_password": "Paused123"}
    assert client.post("/api/tenants", json=body, headers=su).status_code == 201
    client.put("/api/tenants/paused", json={"name": "Paused", "active": False}, headers=su)
    ok = client.post("/api/auth/login", json={"username": "paused-admin", "password": "Paused123"})
    assert ok.status_code == 403 and "disabled" in ok.json()["detail"]
    bad = client.post("/api/auth/login", json={"username": "paused-admin", "password": "wrong-pass"})
    assert bad.status_code == 401
