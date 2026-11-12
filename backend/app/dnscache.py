"""Optional cache for host name lookups made by checks and alerts (DNS monitors are not cached)."""
import socket
import time

TTL_SEC = 60
MAX_ENTRIES = 5000

_original = socket.getaddrinfo
_cache: dict[tuple, tuple[float, list]] = {}


def _cached_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    key = (host, port, family, type, proto, flags)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    result = _original(host, port, family, type, proto, flags)  # failures are not cached
    if len(_cache) >= MAX_ENTRIES:
        _cache.clear()
    _cache[key] = (now + TTL_SEC, result)
    return result


def enable(on: bool) -> None:
    _cache.clear()
    socket.getaddrinfo = _cached_getaddrinfo if on else _original


def enabled() -> bool:
    return socket.getaddrinfo is _cached_getaddrinfo
