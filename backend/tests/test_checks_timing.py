import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

from app import checks

GOOD_TLS = {"version": "TLSv1.3", "cipher": "TLS_AES_128_GCM_SHA256", "days_left": 80}
GOOD_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "Content-Security-Policy": "default-src 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Server": "nginx",
}


def failed(report):
    return {c["name"] for c in report["checks"] if not c["ok"]}


# ---------- security grading (pure function) ----------

def test_all_good_is_a():
    report = checks.grade_security(True, GOOD_HEADERS, GOOD_TLS)
    assert report["grade"] == "A" and not failed(report)


def test_one_missing_header_drops_a_grade():
    headers = {k: v for k, v in GOOD_HEADERS.items() if k != "Content-Security-Policy"}
    report = checks.grade_security(True, headers, GOOD_TLS)
    assert report["grade"] == "B" and failed(report) == {"Content-Security-Policy"}


def test_short_hsts_and_version_leak():
    headers = {**GOOD_HEADERS, "Strict-Transport-Security": "max-age=3600", "Server": "nginx/1.2.3"}
    report = checks.grade_security(True, headers, GOOD_TLS)
    assert report["grade"] == "C"
    assert failed(report) == {"HSTS (max-age 180 days or more)", "No server version shown"}


def test_frame_ancestors_counts_as_clickjacking_protection():
    headers = {k: v for k, v in GOOD_HEADERS.items() if k != "X-Frame-Options"}
    headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'"
    assert checks.grade_security(True, headers, GOOD_TLS)["grade"] == "A"


def test_critical_failures_are_f():
    assert checks.grade_security(False, GOOD_HEADERS, None)["grade"] == "F"
    old = {**GOOD_TLS, "version": "TLSv1", "cipher": "ECDHE-RSA-AES128-SHA"}
    assert checks.grade_security(True, GOOD_HEADERS, old)["grade"] == "F"
    rc4 = {**GOOD_TLS, "version": "TLSv1.2", "cipher": "RC4-SHA"}
    assert "Strong cipher" in failed(checks.grade_security(True, GOOD_HEADERS, rc4))


def test_weak_cipher():
    assert checks.weak_cipher("DES-CBC3-SHA", "TLSv1.2")
    assert checks.weak_cipher("AES128-SHA", "TLSv1")
    assert not checks.weak_cipher("ECDHE-RSA-AES128-GCM-SHA256", "TLSv1.2")
    assert not checks.weak_cipher("TLS_CHACHA20_POLY1305_SHA256", "TLSv1.3")


def test_many_failures_are_f():
    report = checks.grade_security(True, {}, {**GOOD_TLS, "days_left": 3})
    assert report["grade"] == "F" and len(failed(report)) == 6
    headers = {k: v for k, v in GOOD_HEADERS.items() if k in ("Strict-Transport-Security", "Referrer-Policy")}
    assert checks.grade_security(True, headers, GOOD_TLS)["grade"] == "D"


# ---------- timing trace ----------

def monitor(**extra):
    base = {"type": "http", "target": "", "port": None, "method": "GET", "timeout_sec": 5, "retries": 0,
            "expected_status": "200-399", "latency_threshold_ms": None, "keyword": None, "json_path": None,
            "json_expected": None, "ssl_expiry_warn_days": 14, "min_grade": "B"}
    return SimpleNamespace(**{**base, **extra})


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"hello"
        self.send_response(200 if self.path == "/" else 500)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve():
    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_timings_captured():
    server, url = serve()
    try:
        r = asyncio.run(checks.run_check(monitor(target=url + "/")))
    finally:
        server.shutdown()
    assert r.status == "up", r.message
    assert {"connect", "ttfb", "download", "total"} <= set(r.timings)
    assert "dns" not in r.timings and "tls" not in r.timings  # IP target over plain HTTP
    assert all(v >= 0 for v in r.timings.values())


def test_timings_kept_for_failed_check():
    server, url = serve()
    try:
        r = asyncio.run(checks.run_check(monitor(target=url + "/broken")))
    finally:
        server.shutdown()
    assert r.status == "down" and "ttfb" in r.timings


def test_timings_on_connection_error():
    server, url = serve()
    server.shutdown()
    server.server_close()
    r = asyncio.run(checks.run_check(monitor(target=url + "/", timeout_sec=2)))
    assert r.status == "down" and "total" in r.timings


def test_security_monitor_over_http_is_f():
    server, url = serve()
    try:
        r = asyncio.run(checks.run_check(monitor(type="security", target=url + "/")))
    finally:
        server.shutdown()
    assert r.status == "degraded" and r.message.startswith("Grade F")
    assert r.findings["grade"] == "F" and r.timings


def test_api_accepts_security_monitor_and_timings(client, su):
    from app import scheduler, tenancy
    from app.checks import CheckResult

    body = {"name": "headers", "type": "security", "target": "https://example.com", "min_grade": "A"}
    m = client.post("/api/monitors", json=body, headers=su).json()
    assert m["min_grade"] == "A" and m["last_timings"] is None
    timings = {"dns": 1.0, "connect": 2.0, "ttfb": 30.0, "total": 40.0}
    asyncio.run(scheduler.record_result(tenancy.DEFAULT, m["id"], CheckResult(
        "degraded", 40.0, "Grade C", timings=timings, findings={"grade": "C", "tls": None, "checks": []})))
    out = client.get(f"/api/monitors/{m['id']}", headers=su).json()
    assert out["last_timings"] == timings and out["security_findings"]["grade"] == "C"
    beats = client.get(f"/api/monitors/{m['id']}/heartbeats", headers=su).json()
    assert beats[0]["timings"] == timings
    client.delete(f"/api/monitors/{m['id']}", headers=su)
