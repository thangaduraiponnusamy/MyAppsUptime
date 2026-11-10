import asyncio
import json
import re
import ssl
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import dns.asyncresolver
import httpx

from .models import Monitor, aware, utcnow


@dataclass
class CheckResult:
    status: str  # up | down | degraded
    latency_ms: float | None
    message: str
    ssl_expires_at: datetime | None = None


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


async def check_http(m: Monitor) -> CheckResult:
    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=m.timeout_sec, follow_redirects=True) as client:
        resp = await client.request(m.method, m.target)
    latency = _elapsed_ms(start)
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
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
    )
    out, _ = await asyncio.wait_for(proc.communicate(), m.timeout_sec + 2)
    latency = _elapsed_ms(start)
    if proc.returncode != 0:
        return CheckResult("down", None, "No ICMP reply")
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


CHECKS = {
    "http": check_http,
    "keyword": check_http,
    "json": check_http,
    "tcp": check_tcp,
    "ping": check_ping,
    "dns": check_dns,
    "ssl": check_ssl,
    "push": check_push,
}


async def run_check(m: Monitor) -> CheckResult:
    result = CheckResult("down", None, "not checked")
    for _ in range(m.retries + 1):
        try:
            result = await CHECKS[m.type](m)
        except Exception as exc:  # any failure means the target is down
            result = CheckResult("down", None, f"{type(exc).__name__}: {exc}"[:300])
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
