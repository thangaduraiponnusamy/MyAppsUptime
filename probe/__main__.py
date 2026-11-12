"""Start with: SERVER_URL=https://uptime.example.com PROBE_TOKEN=... python -m probe"""
import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx

try:
    from app.checks import run_check
except ImportError:  # started from the repository folder
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
    from app.checks import run_check
from app.timeutil import APP_VERSION

log = logging.getLogger("probe")

TICK_SEC = 2
SYNC_SEC = 10
MAX_RESULTS = 500
MAX_AGE_SEC = 120  # older results are dropped when the server was unreachable


class Probe:
    def __init__(self, server: str, token: str) -> None:
        self.server = server.rstrip("/")
        self.token = token
        self.name = ""
        self.jobs: dict[int, dict] = {}
        self.due: dict[int, float] = {}
        self.running: set[int] = set()
        self.tasks: set[asyncio.Task] = set()
        self.results: list[tuple[float, dict]] = []
        self.last_sync = -SYNC_SEC

    async def sync(self, client: httpx.AsyncClient) -> None:
        now = time.monotonic()
        self.results = [r for r in self.results if now - r[0] < MAX_AGE_SEC][-MAX_RESULTS:]
        batch = self.results[:]
        try:
            resp = await client.post(f"{self.server}/api/probe/sync",
                                     json={"version": APP_VERSION, "results": [r[1] for r in batch]})
        except httpx.HTTPError as exc:
            log.warning("Server not reachable: %s", exc)
            return
        if resp.status_code == 401:
            log.error("The server refused the token. Check PROBE_TOKEN.")
            return
        if resp.status_code >= 400:
            log.warning("Sync failed: HTTP %s %s", resp.status_code, resp.text[:200])
            return
        self.results = self.results[len(batch):]
        data = resp.json()
        jobs = {j["id"]: j for j in data["monitors"]}
        if data["name"] != self.name:
            self.name = data["name"]
            log.info("Connected to %s as '%s'", self.server, self.name)
        if jobs.keys() != self.jobs.keys():
            log.info("%d monitor(s) to check", len(jobs))
        for mid, job in jobs.items():
            if self.jobs.get(mid) != job:
                self.due[mid] = 0  # new or changed: check now
        for mid in data.get("run_now", []):
            self.due[mid] = 0
        self.jobs = jobs

    async def check(self, job: dict) -> None:
        try:
            r = await run_check(SimpleNamespace(**job))
            self.results.append((time.monotonic(), {
                "monitor_id": job["id"], "status": r.status, "latency_ms": r.latency_ms, "message": r.message[:300],
                "ssl_expires_at": r.ssl_expires_at.isoformat() if r.ssl_expires_at else None,
            }))
        except Exception:
            log.exception("Check of '%s' failed", job["name"])
        finally:
            self.running.discard(job["id"])

    def start_due_checks(self) -> None:
        now = time.monotonic()
        for mid, job in self.jobs.items():
            if mid in self.running or self.due.get(mid, 0) > now:
                continue
            self.due[mid] = now + job["interval_sec"]
            self.running.add(mid)
            task = asyncio.create_task(self.check(job))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def run(self) -> None:
        headers = {"Authorization": f"Bearer {self.token}", "User-Agent": f"MyAppsUptime-Probe/{APP_VERSION}"}
        async with httpx.AsyncClient(headers=headers, timeout=20) as client:
            while True:
                since = time.monotonic() - self.last_sync
                # Send new results quickly, otherwise check in every SYNC_SEC
                if since >= SYNC_SEC or (self.results and since >= TICK_SEC):
                    self.last_sync = time.monotonic()
                    await self.sync(client)
                self.start_due_checks()
                await asyncio.sleep(TICK_SEC)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # no line per request
    server, token = os.getenv("SERVER_URL", ""), os.getenv("PROBE_TOKEN", "")
    if not server.startswith(("http://", "https://")) or not token:
        sys.exit("Set SERVER_URL (for example https://uptime.example.com) and PROBE_TOKEN")
    log.info("MyAppsUptime Probe %s", APP_VERSION)
    try:
        asyncio.run(Probe(server, token).run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
