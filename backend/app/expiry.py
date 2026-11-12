"""Certificate and domain registration expiry lookups."""
import asyncio
import ipaddress
import ssl
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from cryptography import x509

RDAP_URL = "https://rdap.org/domain/{}"


def host_port(monitor) -> tuple[str, int] | None:
    """Host and TLS port for monitors that use HTTPS, else None."""
    if monitor.type == "ssl":
        parsed = urlparse(monitor.target if "://" in monitor.target else f"https://{monitor.target}")
        return (parsed.hostname, monitor.port or parsed.port or 443) if parsed.hostname else None
    if monitor.type in ("http", "keyword", "json"):
        parsed = urlparse(monitor.target)
        if parsed.scheme == "https" and parsed.hostname:
            return parsed.hostname, parsed.port or 443
    return None


def is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


async def cert_expiry(host: str, port: int, timeout: float = 10) -> datetime:
    # no verification here: the date of an already expired certificate is still wanted
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    _, writer = await asyncio.wait_for(asyncio.open_connection(host, port, ssl=ctx, server_hostname=host), timeout)
    try:
        der = writer.get_extra_info("ssl_object").getpeercert(binary_form=True)
    finally:
        writer.close()
    return x509.load_der_x509_certificate(der).not_valid_after_utc


def domain_candidates(host: str) -> list[str]:
    """example.com first, then longer names (for suffixes like co.uk)."""
    labels = host.lower().rstrip(".").split(".")
    return [".".join(labels[-n:]) for n in range(2, len(labels) + 1)]


async def domain_expiry(host: str, client: httpx.AsyncClient) -> datetime | None:
    if is_ip(host) or "." not in host:
        return None
    for name in domain_candidates(host)[:3]:
        resp = await client.get(RDAP_URL.format(name), follow_redirects=True)
        if resp.status_code != 200:
            continue
        for event in resp.json().get("events", []):
            if event.get("eventAction") == "expiration":
                value = datetime.fromisoformat(event["eventDate"].replace("Z", "+00:00"))
                return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return None
    return None


def next_warning(days_left: int, warned: int | None, warn_days: list[int]) -> tuple[bool, int | None]:
    """Return (send now, new warned level). Each level (30, 14, 7) is sent once; renewal resets."""
    hits = [d for d in warn_days if days_left <= d]
    if not hits:
        return False, None
    level = min(hits)
    if warned is None or level < warned:
        return True, level
    return False, warned


def describe(kind: str, expires: datetime, now: datetime) -> str:
    when = f"{expires:%Y-%m-%d}"
    if expires < now:
        return f"{kind} expired {(now - expires).days} days ago ({when})"
    return f"{kind} expires in {(expires - now).days} days ({when})"
