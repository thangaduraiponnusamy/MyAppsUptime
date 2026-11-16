import asyncio
import ipaddress
import json
import os
import re
import socket
import ssl
import sys
import time
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse

import dns.asyncresolver
import httpx

# No database imports: the probe reuses this file
from .timeutil import aware, utcnow

Monitor = Any  # a Monitor row on the server, a simple namespace on the probe
DOCKER_SOCKET = os.getenv("DOCKER_SOCKET", "/var/run/docker.sock")


@dataclass
class CheckResult:
    status: str  # up | down | degraded
    latency_ms: float | None
    message: str
    ssl_expires_at: datetime | None = None
    timings: dict | None = None  # ms per phase: dns, connect, tls, ttfb, download, total
    findings: dict | None = None  # security monitors: grade and checklist


# ---------- SSRF guard: checks run by the central server must not reach its own network ----------

ALLOW_METADATA = os.getenv("ALLOW_METADATA") == "1"
METADATA = {ipaddress.ip_address("169.254.169.254"), ipaddress.ip_address("fd00:ec2::254")}
PRIVATE_NETS = [ipaddress.ip_network(n) for n in (
    "0.0.0.0/8", "10.0.0.0/8", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12", "192.168.0.0/16",
    "224.0.0.0/4", "255.255.255.255/32", "::/128", "::1/128", "fe80::/10", "fc00::/7", "ff00::/8",
)]
HTTP_TYPES = ("http", "keyword", "json")
URL_TYPES = (*HTTP_TYPES, "security")
MAX_REDIRECTS = 10


class Blocked(Exception):
    pass


@dataclass
class TargetPolicy:
    allow_private: bool = False
    allow_cidrs: tuple = ()  # ip_network objects that are always allowed


def blocked_reason(address: str, policy: TargetPolicy) -> str | None:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip in METADATA:
        return None if ALLOW_METADATA else f"{ip} is a cloud metadata address"
    if policy.allow_private or any(ip.version == n.version and ip in n for n in policy.allow_cidrs):
        return None
    if any(ip.version == n.version and ip in n for n in PRIVATE_NETS):
        return f"{ip} is a private or local address"
    return None


def guard_host(host: str, port: int | None, policy: TargetPolicy) -> None:
    """Raise Blocked when the host resolves to a forbidden address (OSError when it does not resolve)."""
    host = host.strip("[]")
    for info in socket.getaddrinfo(host, port or 0, 0, socket.SOCK_STREAM):
        reason = blocked_reason(info[4][0], policy)
        if reason:
            raise Blocked(reason)


def target_host(m: Monitor) -> tuple[str, int | None] | None:
    """Host and port a check connects to; None for checks that open no connection to the target."""
    if m.type in URL_TYPES:
        url = urlparse(m.target)
        return (url.hostname, url.port) if url.hostname else None
    if m.type == "ssl":
        return urlparse(m.target).hostname or m.target, m.port or 443
    if m.type in ("tcp", "ping") and m.target:
        return m.target, m.port
    return None


async def _guard(host: str | None, port: int | None, policy: TargetPolicy | None) -> None:
    if policy is not None and host:
        await asyncio.to_thread(guard_host, host, port, policy)


def status_matches(code: int, spec: str) -> bool:
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            low, high = part.split("-")
            if int(low) <= code <= int(high):
                return True
        elif part and int(part) == code:
            return True
    return False


def json_lookup(data: Any, path: str) -> Any:
    for part in path.strip().removeprefix("$").strip(".").split("."):
        data = data[int(part)] if isinstance(data, list) else data[part]
    return data


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value)


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 1)


# ---------- timing trace (DNS, TCP connect, TLS, time to first byte, download) ----------

PHASES = ("dns", "connect", "tls", "ttfb", "download")
_PHASE_START = {"connection.connect_tcp": "connect", "connection.start_tls": "tls",
                "http11.send_request_headers": "ttfb", "http2.send_request_headers": "ttfb",
                "http11.receive_response_body": "download", "http2.receive_response_body": "download"}
_PHASE_END = {"connection.connect_tcp": "connect", "connection.start_tls": "tls",
              "http11.receive_response_headers": "ttfb", "http2.receive_response_headers": "ttfb",
              "http11.receive_response_body": "download", "http2.receive_response_body": "download"}


class Timer:
    """Phase durations from httpx trace events; with redirects the last hop wins."""

    def __init__(self) -> None:
        self.start = time.perf_counter()
        self.dns: float | None = None
        self.phases: dict[str, float] = {}
        self._open: dict[str, float] = {}

    async def trace(self, name: str, info: dict) -> None:
        event, _, stage = name.rpartition(".")
        now = time.perf_counter()
        if stage == "started" and event in _PHASE_START:
            self._open[_PHASE_START[event]] = now
        elif stage in ("complete", "failed") and _PHASE_END.get(event) in self._open:
            phase = _PHASE_END[event]
            self.phases[phase] = round((now - self._open.pop(phase)) * 1000, 1)

    def result(self) -> dict:
        out = {"dns": self.dns} if self.dns is not None else {}
        out.update((p, self.phases[p]) for p in PHASES if p in self.phases)
        out["total"] = _elapsed_ms(self.start)
        return out


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


async def _lookup(host: str, port: int | None, policy: TargetPolicy | None) -> float | None:
    """Run the SSRF guard (or a plain lookup) and return how long name resolution took."""
    start = time.perf_counter()
    if policy is not None:
        await _guard(host, port, policy)
    elif not _is_ip(host):
        try:
            await asyncio.to_thread(socket.getaddrinfo, host, port or 0, 0, socket.SOCK_STREAM)
        except OSError:
            return None  # httpx reports the failure
    return None if _is_ip(host) else _elapsed_ms(start)


def _tls_info(resp: httpx.Response) -> tuple[dict | None, datetime | None]:
    stream = resp.extensions.get("network_stream")
    sslobj = stream.get_extra_info("ssl_object") if stream else None
    if sslobj is None:
        return None, None
    cert = sslobj.getpeercert() or {}
    expires = (datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), tz=timezone.utc)
               if cert.get("notAfter") else None)
    days = (expires - datetime.now(timezone.utc)).days if expires else None
    return {"version": sslobj.version(), "cipher": (sslobj.cipher() or ("",))[0], "days_left": days}, expires


async def _fetch(m: Monitor, policy: TargetPolicy | None, timer: Timer, verify: Any = True):
    """Final response and its TLS details. With a policy every redirect hop is checked before it is followed."""
    async with httpx.AsyncClient(timeout=m.timeout_sec, follow_redirects=policy is None, verify=verify) as client:
        request = client.build_request(m.method, m.target)
        timer.dns = await _lookup(request.url.host, request.url.port, policy)
        for hop in range(MAX_REDIRECTS + 1):
            if hop:
                await _guard(request.url.host, request.url.port, policy)
            request.extensions["trace"] = timer.trace
            resp = await client.send(request)
            if resp.next_request is None:
                break
            request = resp.next_request
        else:
            raise httpx.TooManyRedirects("Too many redirects", request=request)
        tls, expires = _tls_info(resp)
    return resp, tls, expires


async def check_http(m: Monitor, policy: TargetPolicy | None = None, timer: Timer | None = None) -> CheckResult:
    timer = timer or Timer()
    resp, _, _ = await _fetch(m, policy, timer)
    latency = _elapsed_ms(timer.start)
    if not status_matches(resp.status_code, m.expected_status):
        return CheckResult("down", latency, f"HTTP {resp.status_code}")
    if m.type == "keyword" and m.keyword not in resp.text:
        return CheckResult("down", latency, f"Keyword '{m.keyword}' not found")
    if m.type == "json":
        try:
            value = _as_text(json_lookup(resp.json(), m.json_path))
        except (KeyError, IndexError, ValueError, TypeError):
            return CheckResult("down", latency, f"JSON path '{m.json_path}' not found")
        if m.json_expected and value != m.json_expected:
            return CheckResult("down", latency, f"{m.json_path} = {value[:80]} (expected {m.json_expected})")
        return CheckResult("up", latency, f"{m.json_path} = {value[:80]}")
    return CheckResult("up", latency, f"HTTP {resp.status_code}")


# ---------- security headers ----------

GRADES = "ABCDF"
HSTS_MIN_AGE = 15552000  # 180 days
WEAK_CIPHER_PARTS = ("RC4", "3DES", "DES-CBC", "NULL", "EXPORT", "EXP-", "ANON", "MD5")
VERSION_LEAK = re.compile(r"\d+\.\d+")


def weak_cipher(name: str, version: str) -> bool:
    name = name.upper()
    if any(part in name for part in WEAK_CIPHER_PARTS):
        return True
    # CBC ciphers on TLS 1.0 (BEAST); AEAD ciphers are GCM, CCM and ChaCha20
    return version == "TLSv1" and not any(a in name for a in ("GCM", "CCM", "CHACHA20"))


def grade_security(https: bool, headers, tls: dict | None, warn_days: int = 14) -> dict:
    """Grade A-F from response headers and TLS details. Pure function, no network."""
    h = {k.lower(): v for k, v in headers.items()}
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str, critical: bool = False) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "critical": critical})

    add("HTTPS", https, "Served over HTTPS" if https else "Not using HTTPS", critical=True)
    if https:
        version, cipher = (tls or {}).get("version") or "", (tls or {}).get("cipher") or ""
        add("TLS 1.2 or newer", version in ("TLSv1.2", "TLSv1.3"), version or "unknown", critical=True)
        add("Strong cipher", bool(cipher) and not weak_cipher(cipher, version), cipher or "unknown", critical=True)
        days = (tls or {}).get("days_left")
        add("Certificate not expiring soon", days is not None and days > warn_days,
            "unknown" if days is None else f"{days} days left")
    hsts = h.get("strict-transport-security", "")
    age = re.search(r"max-age\s*=\s*\"?(\d+)", hsts, re.I)
    add("HSTS (max-age 180 days or more)", bool(age) and int(age.group(1)) >= HSTS_MIN_AGE, hsts[:120] or "missing")
    csp = h.get("content-security-policy", "")
    add("Content-Security-Policy", bool(csp.strip()), "present" if csp.strip() else "missing")
    nosniff = h.get("x-content-type-options", "").strip().lower() == "nosniff"
    add("X-Content-Type-Options: nosniff", nosniff, h.get("x-content-type-options") or "missing")
    xfo = h.get("x-frame-options", "").strip().upper()
    framing = xfo in ("DENY", "SAMEORIGIN") or "frame-ancestors" in csp.lower()
    add("Clickjacking protection", framing,
        f"X-Frame-Options {xfo}" if xfo else ("CSP frame-ancestors" if framing else "missing"))
    referrer = h.get("referrer-policy", "").strip()
    add("Referrer-Policy", bool(referrer), referrer or "missing")
    leak = " ".join(v for v in (h.get("server", ""), h.get("x-powered-by", "")) if VERSION_LEAK.search(v))
    add("No server version shown", not leak, leak[:80] or "ok")

    if any(c["critical"] and not c["ok"] for c in checks):
        grade = "F"
    else:
        failed = sum(not c["ok"] for c in checks)
        grade = GRADES[min(failed, 4)]
    return {"grade": grade, "tls": tls, "checks": checks}


def grading_tls_context() -> ssl.SSLContext:
    # Still verifies the certificate; old TLS is allowed only so it can be graded
    ctx = httpx.create_ssl_context()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        ctx.minimum_version = ssl.TLSVersion.TLSv1
    try:
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
    except ssl.SSLError:
        pass
    return ctx


async def check_security(m: Monitor, policy: TargetPolicy | None = None, timer: Timer | None = None) -> CheckResult:
    timer = timer or Timer()
    resp, tls, expires = await _fetch(m, policy, timer, verify=grading_tls_context())
    report = grade_security(resp.url.scheme == "https", resp.headers, tls, m.ssl_expiry_warn_days)
    minimum = getattr(m, "min_grade", None) or "B"
    status = "up" if GRADES.index(report["grade"]) <= GRADES.index(minimum) else "degraded"
    failed = [c["name"] for c in report["checks"] if not c["ok"]]
    message = f"Grade {report['grade']}" + (f": failed {', '.join(failed)}" if failed else ": all checks passed")
    return CheckResult(status, _elapsed_ms(timer.start), message[:300], expires, findings=report)


async def check_tcp(m: Monitor) -> CheckResult:
    start = time.perf_counter()
    _, writer = await asyncio.wait_for(asyncio.open_connection(m.target, m.port), m.timeout_sec)
    latency = _elapsed_ms(start)
    writer.close()
    await writer.wait_closed()
    return CheckResult("up", latency, f"Port {m.port} open")


async def check_ping(m: Monitor) -> CheckResult:
    if sys.platform == "win32":
        args = ["ping", "-n", "1", "-w", str(m.timeout_sec * 1000), m.target]
    else:
        args = ["ping", "-c", "1", "-W", str(m.timeout_sec), m.target]
    start = time.perf_counter()
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await asyncio.wait_for(proc.communicate(), m.timeout_sec + 2)
    latency = _elapsed_ms(start)
    if proc.returncode != 0:
        detail = err.decode(errors="ignore").strip()
        return CheckResult("down", None, f"No ICMP reply ({detail[:150]})" if detail else "No ICMP reply")
    match = re.search(r"time[=<]\s*([\d.]+)\s*ms", out.decode(errors="ignore"))
    if match:
        latency = float(match.group(1))
    return CheckResult("up", latency, f"Reply in {latency} ms")


async def check_dns(m: Monitor) -> CheckResult:
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = m.timeout_sec
    start = time.perf_counter()
    answer = await resolver.resolve(m.target, "A")
    return CheckResult("up", _elapsed_ms(start), ", ".join(r.to_text() for r in answer)[:300])


async def check_ssl(m: Monitor) -> CheckResult:
    host = urlparse(m.target).hostname or m.target
    port = m.port or 443
    start = time.perf_counter()
    _, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port, ssl=ssl.create_default_context(), server_hostname=host),
        m.timeout_sec,
    )
    latency = _elapsed_ms(start)
    cert = writer.get_extra_info("peercert")
    writer.close()
    await writer.wait_closed()

    expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), tz=timezone.utc)
    days_left = (expires - datetime.now(timezone.utc)).days
    status = "degraded" if days_left <= m.ssl_expiry_warn_days else "up"
    return CheckResult(status, latency, f"Certificate expires in {days_left} days", expires)


async def check_push(m: Monitor) -> CheckResult:
    # New push monitors get one interval of grace before going DOWN
    last = aware(m.last_push_at) or aware(m.created_at)
    if last and (utcnow() - last).total_seconds() <= m.interval_sec:
        return CheckResult("up", None, "Push received")
    return CheckResult("down", None, f"No push received within {m.interval_sec}s")


async def check_docker(m: Monitor) -> CheckResult:
    if not os.path.exists(DOCKER_SOCKET):
        return CheckResult("down", None, f"Docker socket {DOCKER_SOCKET} not found")
    transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCKET)
    async with httpx.AsyncClient(transport=transport, base_url="http://docker", timeout=m.timeout_sec) as client:
        resp = await client.get(f"/containers/{quote(m.target, safe='')}/json")
    if resp.status_code == 404:
        return CheckResult("down", None, f"Container '{m.target}' not found")
    resp.raise_for_status()
    state = resp.json().get("State") or {}
    run = state.get("Status", "unknown")
    health = (state.get("Health") or {}).get("Status")
    if run != "running":
        return CheckResult("down", None, f"Container is {run} (exit code {state.get('ExitCode', '?')})")
    if health == "unhealthy":
        return CheckResult("degraded", None, "Container is running but unhealthy")
    return CheckResult("up", None, f"Container is running ({health})" if health else "Container is running")


CHECKS = {
    "http": check_http,
    "keyword": check_http,
    "json": check_http,
    "security": check_security,
    "tcp": check_tcp,
    "ping": check_ping,
    "dns": check_dns,
    "ssl": check_ssl,
    "push": check_push,
    "docker": check_docker,
}


async def run_check(m: Monitor, policy: TargetPolicy | None = None) -> CheckResult:
    """policy None means no target restrictions (private locations check their own network)."""
    result = CheckResult("down", None, "not checked")
    for _ in range(m.retries + 1):
        timer = Timer() if m.type in URL_TYPES else None
        try:
            if timer is not None:
                result = await CHECKS[m.type](m, policy, timer)
            else:
                await _guard(*(target_host(m) or (None, None)), policy)
                result = await CHECKS[m.type](m)
        except Blocked as exc:
            result = CheckResult("down", None, f"Blocked by SSRF guard: {exc}")
            break
        except Exception as exc:  # any failure means the target is down
            result = CheckResult("down", None, f"{type(exc).__name__}: {exc}"[:300])
        if timer is not None:
            result.timings = timer.result()  # also kept for failed requests: shows where it stopped
        if result.status != "down" or m.type == "push":
            break

    if (
        result.status == "up"
        and m.latency_threshold_ms
        and result.latency_ms is not None
        and result.latency_ms > m.latency_threshold_ms
    ):
        result.status = "degraded"
        result.message += f" (slow, > {m.latency_threshold_ms} ms)"
    return result
