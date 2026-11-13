import asyncio
import logging
import statistics
from datetime import timedelta

import httpx

from sqlalchemy import delete, select

from . import backup, expiry, notifier, tenancy
from .checks import CheckResult, run_check
from .config import get_setting, retention_days
from .models import (Heartbeat, Incident, IncidentAck, LoginEvent, MaintenanceWindow, Monitor, NotificationChannel,
                     Probe, Project, User, aware, utcnow)

log = logging.getLogger(__name__)

TICK_SEC = 5
JOBS_EVERY = timedelta(minutes=10)
EXPIRY_EVERY = timedelta(hours=12)
PROBE_OFFLINE = timedelta(seconds=90)  # probes sync every 10 s


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


def tenant_config(db, tenant: str) -> tuple[dict, dict]:
    """General + SMTP settings; tenants without their own fall back to the admin tenant."""
    general, smtp = get_setting(db, "general"), get_setting(db, "smtp")
    if tenant != tenancy.DEFAULT and not (general["app_url"] and general["timezone"] and notifier.smtp_ready(smtp)):
        with tenancy.session_for(tenancy.DEFAULT) as admin:
            base, base_smtp = get_setting(admin, "general"), get_setting(admin, "smtp")
        general = {**general, "app_url": general["app_url"] or base["app_url"],
                   "timezone": general["timezone"] or base["timezone"]}
        if not notifier.smtp_ready(smtp):
            smtp = base_smtp
    return general, smtp


def channel_for(channel: NotificationChannel, m: Monitor, project: Project | None) -> bool:
    if project and project.channel_ids is not None:
        # The team picked its channels: the channel's team filter is ignored, tags still apply
        return channel.id in project.channel_ids and notifier.channel_matches(channel, m, any_project=True)
    return notifier.channel_matches(channel, m)


def build_alert(db, tenant: str, m: Monitor, status: str, message: str, headline: str = ""):
    """Channels, alert, project e-mails and SMTP settings for one monitor alert."""
    active = db.scalars(select(NotificationChannel).where(NotificationChannel.active.is_(True))).all()
    project = db.get(Project, m.project_id) if m.project_id else None
    channels = [c for c in active if channel_for(c, m, project)]
    emails: list[str] = []
    if project and project.email_members and project.member_ids:
        emails = [u.email for u in db.scalars(select(User).where(
            User.id.in_(project.member_ids), User.active.is_(True))).all() if u.email]
    general, smtp = tenant_config(db, tenant)
    app_url = notifier.server_url(general)
    alert = notifier.Alert(
        monitor=m.name, status=status, message=message, headline=headline,
        project=project.name if project else None, target=m.target or None,
        link=f"{app_url}/monitors/{m.id}" if app_url else None,
        **notifier.alert_context(general),
    )
    return channels, alert, emails, smtp


async def record_result(tenant: str, monitor_id: int, result: CheckResult) -> None:
    now = utcnow()
    with tenancy.session_for(tenant) as db:
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

        # Only DOWN after N failed checks in a row; until then the monitor shows "pending"
        if status == "down":
            m.fail_count = (m.fail_count or 0) + 1
            needed = m.down_after or int(get_setting(db, "general")["down_after_checks"])
            if previous != "down" and m.fail_count < needed:
                status = "pending"
                result.message = f"Check failed ({m.fail_count} of {needed}): {result.message}"
        else:
            m.fail_count = 0

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

        changed = (status != previous and status not in ("maintenance", "pending")
                   and not (previous == "pending" and status == "up"))
        last_sent = aware(m.last_notified_at)
        repeat = (
            status == "down" and previous == "down" and m.notify_repeat_min is not None
            and (last_sent is None or now - last_sent >= timedelta(minutes=m.notify_repeat_min))
        )
        notify = (changed or repeat) and not parent_down
        if notify:
            m.last_notified_at = now
        db.commit()

        if not notify:
            return
        message = ("Still down: " if repeat and not changed else "") + result.message
        channels, alert, emails, smtp = build_alert(db, tenant, m, status, message)

    await notifier.send(channels, alert, emails, smtp)


def probe_alert(db, tenant: str, probe: Probe, status: str, message: str):
    """One alert for a private location, sent to the channels of its monitors."""
    monitors = db.scalars(select(Monitor).where(Monitor.probe_id == probe.id, Monitor.active.is_(True))).all()
    active = db.scalars(select(NotificationChannel).where(NotificationChannel.active.is_(True))).all()
    projects = {p.id: p for p in db.scalars(select(Project)).all()}
    channels = [c for c in active if any(channel_for(c, m, projects.get(m.project_id)) for m in monitors)]
    general, smtp = tenant_config(db, tenant)
    app_url = notifier.server_url(general)
    alert = notifier.Alert(
        monitor=f"Private location {probe.name}", status=status, message=message,
        link=f"{app_url}/settings/locations" if app_url else None, **notifier.alert_context(general),
    )
    return channels, alert, [], smtp


async def check_probes(tenant: str) -> None:
    """Alert once when a private location stops reporting; its monitors show pending until it is back."""
    now = utcnow()
    alerts = []
    with tenancy.session_for(tenant) as db:
        for p in db.scalars(select(Probe).where(Probe.offline_notified.is_(False),
                                                Probe.last_seen_at.is_not(None))).all():
            if now - aware(p.last_seen_at) < PROBE_OFFLINE:
                continue
            p.offline_notified = True
            text = f"No contact since {int((now - aware(p.last_seen_at)).total_seconds())} s"
            for m in db.scalars(select(Monitor).where(Monitor.probe_id == p.id, Monitor.active.is_(True))).all():
                m.status, m.fail_count = "pending", 0
                m.last_message = f"Private location '{p.name}' is offline"
            alerts.append(probe_alert(db, tenant, p, "down", text))
        db.commit()
    for channels, alert, emails, smtp in alerts:
        await notifier.send(channels, alert, emails, smtp)


async def check_expiry(tenant: str, client: httpx.AsyncClient, domains: dict) -> None:
    """Warn 30/14/7 days (Settings > General) before certificates and domains expire."""
    now = utcnow()
    with tenancy.session_for(tenant) as db:
        general = get_setting(db, "general")
        monitors = [m for m in db.scalars(select(Monitor).where(Monitor.active.is_(True))).all()
                    if expiry.host_port(m) and m.probe_id is None and not (
                        m.expiry_checked_at and now - aware(m.expiry_checked_at) < EXPIRY_EVERY)]
    warn_days = list(general["expiry_warn_days"])
    for m in monitors:
        host, port = expiry.host_port(m)
        cert = domain = None
        try:
            cert = await expiry.cert_expiry(host, port, m.timeout_sec)
        except Exception as exc:
            log.info("Certificate check failed for %s: %s", host, type(exc).__name__)
        if general["domain_expiry"] and not expiry.is_ip(host):
            if host not in domains:
                try:
                    domains[host] = await expiry.domain_expiry(host, client)
                except Exception as exc:
                    log.info("Domain lookup failed for %s: %s", host, type(exc).__name__)
                    domains[host] = None
            domain = domains[host]
        alerts = []
        with tenancy.session_for(tenant) as db:
            row = db.get(Monitor, m.id)
            if row is None:
                continue
            row.expiry_checked_at = now
            for kind, expires, field in (("SSL certificate", cert, "ssl_warned_days"),
                                          ("Domain", domain, "domain_warned_days")):
                if expires is None:
                    continue
                if kind == "Domain":
                    row.domain_expires_at = expires
                else:
                    row.ssl_expires_at = expires
                send, level = expiry.next_warning((expires - now).days, getattr(row, field), warn_days)
                setattr(row, field, level)
                if send:
                    text = expiry.describe(kind, expires, now)
                    alerts.append(build_alert(db, tenant, row, "warning", text, headline=text.split(" (")[0]))
            db.commit()
        for channels, alert, emails, smtp in alerts:
            await notifier.send(channels, alert, emails, smtp)


class Scheduler:
    def __init__(self) -> None:
        self._running: set[tuple[str, int]] = set()
        self._tasks: set[asyncio.Task] = set()
        self._task: asyncio.Task | None = None
        self._last_prune = utcnow() - timedelta(days=1)
        self._last_jobs = utcnow() - timedelta(days=1)
        self._jobs: asyncio.Task | None = None
        self._probes: asyncio.Task | None = None

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
                self._start_jobs()
                self._watch_probes()
            except Exception:
                log.exception("Scheduler tick failed")
            await asyncio.sleep(TICK_SEC)

    def _watch_probes(self) -> None:
        if self._probes and not self._probes.done():
            return
        self._probes = asyncio.create_task(self.run_probe_checks())

    async def run_probe_checks(self) -> None:
        for tenant in tenancy.slugs():
            try:
                await check_probes(tenant)
            except Exception:
                log.exception("Probe check failed for tenant %s", tenant)

    def _start_jobs(self) -> None:
        """Backup and expiry checks run in the background, at most one at a time."""
        now = utcnow()
        if (self._jobs and not self._jobs.done()) or now - self._last_jobs < JOBS_EVERY:
            return
        self._last_jobs = now
        self._jobs = asyncio.create_task(self.run_jobs())

    async def run_jobs(self) -> None:
        try:
            with tenancy.session_for(tenancy.DEFAULT) as db:
                server = get_setting(db, "server")
            if server["backup_enabled"] and backup.supported() and backup.due():
                await asyncio.to_thread(backup.run)
                await asyncio.to_thread(backup.prune, int(server["backup_keep_days"]))
        except Exception:
            log.exception("Backup failed")
        domains: dict = {}
        async with httpx.AsyncClient(timeout=15, headers={"Accept": "application/rdap+json"}) as client:
            for tenant in tenancy.slugs():
                try:
                    await check_expiry(tenant, client, domains)
                except Exception:
                    log.exception("Expiry check failed for tenant %s", tenant)

    def _dispatch(self) -> None:
        now = utcnow()
        for tenant in tenancy.slugs():
            with tenancy.session_for(tenant) as db:
                monitors = db.scalars(select(Monitor).where(Monitor.active.is_(True), Monitor.probe_id.is_(None))).all()
            for m in monitors:
                last = aware(m.last_checked)
                if (tenant, m.id) in self._running or (last and (now - last).total_seconds() < m.interval_sec):
                    continue
                self.trigger(tenant, m)

    def trigger(self, tenant: str, m: Monitor) -> bool:
        key = (tenant, m.id)
        if key in self._running:
            return False
        self._running.add(key)
        task = asyncio.create_task(self._check(tenant, m))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return True

    async def _check(self, tenant: str, m: Monitor) -> None:
        try:
            result = await run_check(m)
            # Push monitors record their UP beats when the push arrives
            if not (m.type == "push" and result.status == "up"):
                await record_result(tenant, m.id, result)
        except Exception:
            log.exception("Check failed for monitor %s/%s", tenant, m.id)
        finally:
            self._running.discard((tenant, m.id))

    def _prune(self) -> None:
        now = utcnow()
        if now - self._last_prune < timedelta(hours=1):
            return
        self._last_prune = now
        for tenant in tenancy.slugs():
            with tenancy.session_for(tenant) as db:
                cutoff = now - timedelta(days=retention_days(get_setting(db, "general")))
                db.execute(delete(Heartbeat).where(Heartbeat.created_at < cutoff))
                db.execute(delete(Incident).where(Incident.resolved_at.is_not(None), Incident.resolved_at < cutoff))
                # SQLite does not enforce ON DELETE CASCADE here, so drop notes of deleted incidents
                db.execute(delete(IncidentAck).where(IncidentAck.incident_id.not_in(select(Incident.id))))
                db.execute(delete(MaintenanceWindow).where(MaintenanceWindow.ends_at < cutoff))
                db.execute(delete(LoginEvent).where(LoginEvent.created_at < cutoff))
                db.commit()


scheduler = Scheduler()
