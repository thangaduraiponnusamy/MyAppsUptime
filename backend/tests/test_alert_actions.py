from test_features import make_monitor, monitor, record

from app import actions, notifier, tenancy
from app.models import Incident, Monitor


def open_incident(client, su, **extra):
    m = make_monitor(client, su, down_after=1, **extra)
    record(m["id"], "down", "refused")
    with tenancy.session_for(tenancy.DEFAULT) as db:
        incident = db.query(Incident).filter_by(monitor_id=m["id"], resolved_at=None).one()
        return m, incident.id


def incident(incident_id):
    with tenancy.session_for(tenancy.DEFAULT) as db:
        return db.get(Incident, incident_id)


def test_valid_token_get_does_not_change_state(client, su):
    m, iid = open_incident(client, su)
    token = actions.make_token(tenancy.DEFAULT, iid, m["id"], "ack")
    r = client.get(f"/api/action/{token}")
    assert r.status_code == 200 and '<form method="post">' in r.text and "<script" not in r.text
    assert incident(iid).acknowledged_at is None


def test_post_ack_via_link(client, su):
    m, iid = open_incident(client, su)
    r = client.post(f"/api/action/{actions.make_token(tenancy.DEFAULT, iid, m['id'], 'ack')}")
    assert r.status_code == 200
    got = incident(iid)
    assert got.acknowledged_at is not None and got.acknowledged_by == "via alert link"


def test_post_snooze_via_link(client, su):
    m, iid = open_incident(client, su)
    r = client.post(f"/api/action/{actions.make_token(tenancy.DEFAULT, iid, m['id'], 'snooze1h')}")
    assert r.status_code == 200
    assert monitor(client, su, m["id"])["snoozed_until"]


def test_bad_tokens_are_rejected(client, su, monkeypatch):
    m, iid = open_incident(client, su)
    good = actions.make_token(tenancy.DEFAULT, iid, m["id"], "ack")
    expired = actions.make_token(tenancy.DEFAULT, iid, m["id"], "ack", days=-1)
    body, sig = good.split(".")
    tampered = body[:-2] + ("AA" if body[-2:] != "AA" else "BB") + "." + sig
    wrong_tenant = actions.make_token("no-such-tenant", iid, m["id"], "ack")
    wrong_monitor = actions.make_token(tenancy.DEFAULT, iid, m["id"] + 999, "ack")
    for token in (expired, tampered, wrong_tenant, wrong_monitor, good + "x", "garbage"):
        assert client.get(f"/api/action/{token}").status_code == 400
        assert client.post(f"/api/action/{token}").status_code == 400
    assert incident(iid).acknowledged_at is None


def test_alert_has_action_links_and_snooze_suppresses_send(client, su, monkeypatch):
    sent = []

    async def fake_send(channels, alert, emails, smtp):
        sent.append(alert)
        return []

    monkeypatch.setattr(notifier, "send", fake_send)
    monkeypatch.setattr(notifier, "server_url", lambda general: "https://app.example.com")
    m, iid = open_incident(client, su)
    titles = [t for t, _ in sent[-1].buttons()]
    assert "Acknowledge" in titles and "Snooze 1h" in titles

    assert client.post(f"/api/monitors/{m['id']}/snooze", json={"minutes": 60}, headers=su).status_code == 200
    count = len(sent)
    record(m["id"], "up")
    assert len(sent) == count  # recovery alert skipped while snoozed
    assert monitor(client, su, m["id"])["status"] == "up"  # checks continue
    assert client.post(f"/api/monitors/{m['id']}/snooze", json={"minutes": 0}, headers=su).json()["snoozed_until"] is None
    record(m["id"], "down")
    assert len(sent) == count + 1


def test_ack_stops_repeat_alerts(client, su, monkeypatch):
    sent = []

    async def fake_send(channels, alert, emails, smtp):
        sent.append(alert)
        return []

    monkeypatch.setattr(notifier, "send", fake_send)
    m, iid = open_incident(client, su, notify_repeat_min=5)
    with tenancy.session_for(tenancy.DEFAULT) as db:
        db.get(Monitor, m["id"]).last_notified_at = None
        db.commit()
    assert client.post(f"/api/incidents/{iid}/ack", json={}, headers=su).status_code == 200
    count = len(sent)
    record(m["id"], "down")
    assert len(sent) == count
    assert incident(iid).acknowledged_by


def test_audit_for_snooze(client, su):
    m = make_monitor(client, su)
    client.post(f"/api/monitors/{m['id']}/snooze", json={"minutes": 240}, headers=su)
    client.post(f"/api/monitors/{m['id']}/snooze", json={"minutes": 0}, headers=su)
    actions_seen = [e["action"] for e in client.get("/api/audit", params={"limit": 5}, headers=su).json()]
    assert "snoozed" in actions_seen and "unsnoozed" in actions_seen
