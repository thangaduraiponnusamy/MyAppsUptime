import asyncio
import socket
import sys
from datetime import timedelta
from pathlib import Path

import httpx

from app import checks, notifier, scheduler, tenancy
from app.main import app
from app.models import Probe, utcnow

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repository root, for the probe package
from probe.__main__ import Probe as ProbeProgram  # noqa: E402


def make_probe(client, su, name):
    r = client.post("/api/probes", json={"name": name}, headers=su)
    assert r.status_code == 201, r.text
    return r.json()


def make_monitor(client, su, **body):
    r = client.post("/api/monitors", json=body, headers=su)
    assert r.status_code == 201, r.text
    return r.json()


def sync(client, token, results=()):
    return client.post("/api/probe/sync", json={"version": "test", "results": list(results)},
                       headers={"Authorization": f"Bearer {token}"})


def test_probe_sync_and_results(client, su):
    p = make_probe(client, su, "home-a")
    assert p["token"].startswith(f"{tenancy.DEFAULT}.") and not p["online"]
    assert client.post("/api/probes", json={"name": "home-a"}, headers=su).status_code == 409
    mine = make_monitor(client, su, name="nas", type="tcp", target="192.168.1.10", port=445, probe_id=p["id"])
    other = make_monitor(client, su, name="other", type="tcp", target="10.0.0.1", port=22)

    assert sync(client, "default.wrong").status_code == 401
    r = sync(client, p["token"], [
        {"monitor_id": mine["id"], "status": "up", "latency_ms": 3.5, "message": "Port 445 open"},
        {"monitor_id": other["id"], "status": "down", "message": "not mine"},
    ])
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["name"] == "home-a" and [j["id"] for j in data["monitors"]] == [mine["id"]]
    assert client.get(f"/api/monitors/{mine['id']}", headers=su).json()["status"] == "up"
    assert client.get(f"/api/monitors/{other['id']}", headers=su).json()["status"] == "pending"

    listed = {x["name"]: x for x in client.get("/api/probes", headers=su).json()}
    assert listed["home-a"]["online"] and listed["home-a"]["monitors"] == 1 and listed["home-a"]["version"] == "test"

    # Check now is handed to the probe on its next sync
    assert client.post(f"/api/monitors/{mine['id']}/check", headers=su).status_code == 202
    assert sync(client, p["token"]).json()["run_now"] == [mine["id"]]
    assert sync(client, p["token"]).json()["run_now"] == []

    # A new token replaces the old one
    fresh = client.post(f"/api/probes/{p['id']}/token", headers=su).json()["token"]
    assert sync(client, p["token"]).status_code == 401
    assert sync(client, fresh).status_code == 200


def test_probe_validation(client, su):
    body = {"name": "x", "type": "push", "probe_id": 1}
    assert client.post("/api/monitors", json=body, headers=su).status_code == 422
    body = {"name": "x", "type": "tcp", "target": "h", "port": 1, "probe_id": 9999}
    assert client.post("/api/monitors", json=body, headers=su).status_code == 422
    body = {"name": "x", "type": "docker", "target": "bad name!"}
    assert client.post("/api/monitors", json=body, headers=su).status_code == 422
    body = {"name": "x", "type": "docker", "target": "pihole"}
    assert client.post("/api/monitors", json=body, headers=su).status_code == 201


def test_probe_offline_alert_once(client, su, monkeypatch):
    sent = []

    async def fake_send(channels, alert, emails=None, smtp=None):
        sent.append(alert)
        return []

    monkeypatch.setattr(notifier, "send", fake_send)
    p = make_probe(client, su, "home-b")
    m = make_monitor(client, su, name="router", type="ping", target="192.168.1.1", probe_id=p["id"])
    sync(client, p["token"], [{"monitor_id": m["id"], "status": "up", "latency_ms": 1}])

    with tenancy.session_for(tenancy.DEFAULT) as db:
        db.get(Probe, p["id"]).last_seen_at = utcnow() - timedelta(minutes=5)
        db.commit()
    asyncio.run(scheduler.check_probes(tenancy.DEFAULT))
    asyncio.run(scheduler.check_probes(tenancy.DEFAULT))
    assert [(a.monitor, a.status) for a in sent] == [("Private location home-b", "down")]
    got = client.get(f"/api/monitors/{m['id']}", headers=su).json()
    assert got["status"] == "pending" and "offline" in got["last_message"]

    sync(client, p["token"], [{"monitor_id": m["id"], "status": "up", "latency_ms": 1}])
    assert [(a.monitor, a.status) for a in sent][-1] == ("Private location home-b", "up")
    assert client.get(f"/api/monitors/{m['id']}", headers=su).json()["status"] == "up"


def test_delete_probe_moves_monitors_back(client, su):
    p = make_probe(client, su, "home-c")
    m = make_monitor(client, su, name="cam", type="ping", target="192.168.1.20", probe_id=p["id"])
    assert client.delete(f"/api/probes/{p['id']}", headers=su).status_code == 204
    assert client.get(f"/api/monitors/{m['id']}", headers=su).json()["probe_id"] is None


def test_docker_check_without_socket(monkeypatch):
    monkeypatch.setattr(checks, "DOCKER_SOCKET", "/nope/docker.sock")
    m = type("M", (), {"type": "docker", "target": "web", "timeout_sec": 2, "retries": 0,
                       "latency_threshold_ms": None})()
    result = asyncio.run(checks.run_check(m))
    assert result.status == "down" and "not found" in result.message


def test_probe_program_end_to_end(client, su):
    """The real probe loop against the API: it gets its job, runs a TCP check and reports UP."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    p = make_probe(client, su, "home-d")
    m = make_monitor(client, su, name="local", type="tcp", target="127.0.0.1", port=port, probe_id=p["id"])

    async def run():
        program = ProbeProgram("http://test", p["token"])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     headers={"Authorization": f"Bearer {p['token']}"}) as http:
            await program.sync(http)
            assert list(program.jobs) == [m["id"]]
            program.start_due_checks()
            await asyncio.gather(*program.tasks)
            assert len(program.results) == 1
            await program.sync(http)
            assert program.results == []

    try:
        asyncio.run(run())
    finally:
        server.close()
    got = client.get(f"/api/monitors/{m['id']}", headers=su).json()
    assert got["status"] == "up" and got["last_message"] == f"Port {port} open"
