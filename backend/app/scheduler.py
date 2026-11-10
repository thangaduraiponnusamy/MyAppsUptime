import asyncio
import logging
import os
import statistics
from datetime import timedelta

from sqlalchemy import delete, select

from . import notifier
from .checks import CheckResult, run_check
from .database import SessionLocal
from .models import Heartbeat, Incident, MaintenanceWindow, Monitor, NotificationChannel, aware, utcnow

log = logging.getLogger(__name__)

TICK_SEC = 5
RETENTION_DAYS = int(os.getenv("HEARTBEAT_RETENTION_DAYS", "90"))


def in_maintenance(db, monitor_id: int, now) -> bool:
    windows = db.scalars(
        select(MaintenanceWindow).where(MaintenanceWindow.starts_at <= now, MaintenanceWindow.ends_at >= now)
    ).all()
    return any(not w.monitor_ids or monitor_id in w.monitor_ids for w in windows)


def latency_anomaly(db, monitor_id: int, latency: float) -> float | None:
    """Return the baseline mean when latency is far outside the recent normal range."""
    values = db.scalars(
        select(Heartbeat.latency_ms)
        .where(Heartbeat.monitor_id == monitor_id, Heartbeat.status == "up", Heartbeat.latency_ms.is_not(None))
        .order_by(Heartbeat.created_at.desc())
        .limit(50)
    ).all()
    if len(values) < 20:
        return None
    mean, std = statistics.fmean(values), statistics.pstdev(values)
    if latency > mean + 3 * std and latency > mean * 1.5 and latency - mean > 50:
        return mean
    return None


async def record_result(monitor_id: int, result: CheckResult) -> None:
    now = utcnow()
    with SessionLocal() as db:
        m = db.get(Monitor, monitor_id)
        if m is None or not m.active:
            return
        previous = m.status

        if m.anomaly_detection and result.status == "up" and result.latency_ms is not None:
            baseline = latency_anomaly(db, m.id, result.latency_ms)
            if baseline is not None:
                result.status = "degraded"
                result.message += f" (latency anomaly, baseline {baseline:.0f} ms)"

        status = "maintenance" if in_maintenance(db, m.id, now) else result.status

        parent_down = False
        if status == "down" and m.parent_id:
            parent = db.get(Monitor, m.parent_id)
            parent_down = parent is not None and parent.status == "down"
            if parent_down:
                result.message += f" (parent '{parent.name}' is down)"

        db.add(Heartbeat(monitor_id=m.id, status=status, latency_ms=result.latency_ms,
                         message=result.message[:300], created_at=now))
        m.status = status
        m.last_checked = now
        m.last_latency_ms = result.latency_ms
        m.last_message = result.message[:300]
        if result.ssl_expires_at:
            m.ssl_expires_at = result.ssl_expires_at

        open_incident = db.scalar(
            select(Incident).where(Incident.monitor_id == m.id, Incident.resolved_at.is_(None))
        )
        # A child that is down because its parent is down is covered by the parent's incident
        if status == "down" and open_incident is None and not parent_down:
            db.add(Incident(monitor_id=m.id, cause=result.message[:300], started_at=now))
        elif status in ("up", "degraded") and open_incident is not None:
            open_incident.resolved_at = now

        changed = status != previous and status != "maintenance" and not (previous == "pending" and status == "up")
        last_sent = aware(m.last_notified_at)
        repeat = (
            status == "down" and previous == "down" and m.notify_repeat_min is not None
            and (last_sent is None or now - last_sent >= timedelta(minutes=m.notify_repeat_min))
        )
        notify = (changed or repeat) and not parent_down
        if notify:
            m.last_notified_at = now
        db.commit()

        channels = []
        if notify:
            active = db.scalars(select(NotificationChannel).where(NotificationChannel.active.is_(True))).all()
            channels = [c for c in active if notifier.channel_matches(c, m.tags)]
        name = m.name

    message = ("Still down: " if repeat and not changed else "") + result.message
    await notifier.send(channels, name, status, message)


class Scheduler:
    def __init__(self) -> None:
        self._running: set[int] = set()
        self._tasks: set[asyncio.Task] = set()
        self._task: asyncio.Task | None = None
        self._last_prune = utcnow() - timedelta(days=1)

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                self._dispatch()
                self._prune()
            except Exception:
                log.exception("Scheduler tick failed")
            await asyncio.sleep(TICK_SEC)

    def _dispatch(self) -> None:
        now = utcnow()
        with SessionLocal() as db:
            monitors = db.scalars(select(Monitor).where(Monitor.active.is_(True))).all()
        for m in monitors:
            last = aware(m.last_checked)
            if m.id in self._running or (last and (now - last).total_seconds() < m.interval_sec):
                continue
            self.trigger(m)

    def trigger(self, m: Monitor) -> bool:
        if m.id in self._running:
            return False
        self._running.add(m.id)
        task = asyncio.create_task(self._check(m))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def _check(self, m: Monitor) -> None:
        try:
            result = await run_check(m)
            # Push monitors record their UP beats when the push arrives
            if not (m.type == "push" and result.status == "up"):
                await record_result(m.id, result)
        except Exception:
            log.exception("Check failed for monitor %s", m.id)
        finally:
            self._running.discard(m.id)

    def _prune(self) -> None:
        now = utcnow()
        if now - self._last_prune < timedelta(hours=1):
            return
        self._last_prune = now
        cutoff = now - timedelta(days=RETENTION_DAYS)
        with SessionLocal() as db:
            db.execute(delete(Heartbeat).where(Heartbeat.created_at < cutoff))
            db.execute(delete(MaintenanceWindow).where(MaintenanceWindow.ends_at < cutoff))
            db.commit()


scheduler = Scheduler()
