import asyncio
import ipaddress
import logging
import statistics
from datetime import timedelta

import httpx

from sqlalchemy import delete, select

from . import actions, backup, expiry, notifier, otel, tenancy
from .checks import CheckResult, TargetPolicy, run_check
from .config import get_setting, retention_days
from .models import (AlertEvent, AuditEvent, Heartbeat, Incident, IncidentAck, LoginEvent, MaintenanceWindow, Monitor,
                     NotificationChannel, Probe, Project, User, aware, utcnow)
from .schemas import ALERT_EMAIL

log = logging.getLogger(__name__)

TICK_SEC = 5
JOBS_EVERY = timedelta(minutes=10)
EXPIRY_EVERY = timedelta(hours=12)
PROBE_OFFLINE = timedelta(seconds=90)  # probes sync every 10 s


def target_policy() -> TargetPolicy:
    """Which targets checks run by this server may reach (Server settings of the admin tenant)."""
    with tenancy.session_for(tenancy.DEFAULT) as db:
        server = get_setting(db, "server")
    return TargetPolicy(bool(server["allow_private_targets"]),
                        tuple(ipaddress.ip_network(c, strict=False) for c in server["allowed_cidrs"]))


def in_maintenance(db, monitor_id: int, now) -> bool:
    windows = db.scalars(
        select(MaintenanceWindow).where(MaintenanceWindow.starts_at <= now, MaintenanceWindow.ends_at >= now)
    ).all()
    return any(not w.monitor_ids or monitor_id in w.monitor_ids for w in windows)


ANOMALY_CHECKS = 3  # slow checks in a row before a monitor is degraded


def latency_anomaly(db, monitor_id: int, latency: float) -> float | None:
    """Return the baseline mean when the last few checks are all far outside the normal range."""
    values = db.scalars(
        select(Heartbeat.latency_ms)
        .where(Heartbeat.monitor_id == monitor_id, Heartbeat.status.in_(("up", "degraded")),
               Heartbeat.latency_ms.is_not(None))
        .order_by(Heartbeat.created_at.desc())
        .limit(50 + ANOMALY_CHECKS - 1)
    ).all()
    recent, history = [latency, *values[:ANOMALY_CHECKS - 1]], values[ANOMALY_CHECKS - 1:]
    if len(history) < 20:
        return None
    mean, std = statistics.fmean(history), statistics.pstdev(history)
    if all(v > mean + 3 * std and v > mean * 1.5 and v - mean > 50 for v in recent):
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


def build_alert(db, tenant: str, m: Monitor, status: str, message: str, headline: str = "",
                incident_id: int | None = None):
    """Channels, alert, project e-mails and SMTP settings for one monitor alert."""
    active = db.scalars(select(NotificationChannel).where(NotificationChannel.active.is_(True))).all()
    project = db.get(Project, m.project_id) if m.project_id else None
    channels = [c for c in active if channel_for(c, m, project)]
    emails: list[str] = []
    if project:
        # Project members, plus users given this one service
        users = [u for u in db.scalars(select(User).where(User.active.is_(True))).all()
                 if u.id in (project.member_ids or []) or m.id in (u.monitor_ids or [])]

        def wants(u: User, cid: int) -> bool:
            return u.alert_channels is None or cid in u.alert_channels

        # A shared channel is skipped only when every one of these users turned it off
        if users:
            channels = [c for c in channels if any(wants(u, c.id) for u in users)]
        if project.email_members:
            emails = [u.email for u in users if u.email and wants(u, ALERT_EMAIL)]
    general, smtp = tenant_config(db, tenant)
    app_url = notifier.server_url(general)
    alert = notifier.Alert(
        monitor=m.name, status=status, message=message, headline=headline,
        project=project.name if project else None, target=m.target or None,
        link=f"{app_url}/monitors/{m.id}" if app_url else None,
        **notifier.alert_context(general),
    )
    if app_url and incident_id and status == "down":
        alert.actions = actions.links(app_url, tenant, incident_id, m.id)
    return channels, alert, emails, smtp


def is_snoozed(m: Monitor) -> bool:
    until = aware(m.snoozed_until)
    return until is not None and until > utcnow()


async def deliver(tenant: str, monitor_id: int | None, channels, alert, emails, smtp) -> None:
    """Send an alert and keep a record of it for the Alerts page."""
    if monitor_id is not None:
        with tenancy.session_for(tenant) as db:
            m = db.get(Monitor, monitor_id)
            if m is not None and is_snoozed(m):
                log.info("Alert for %s skipped: snoozed until %s", m.name, m.snoozed_until)
                return
    sent_to = [c.name for c in channels] + (["Team e-mail"] if emails else [])
    errors = await notifier.send(channels, alert, emails, smtp)
    if monitor_id is not None:
        from . import push
        try:
            pushed, push_errors = await push.notify_monitor(tenant, monitor_id, alert)
        except Exception:  # push must never stop the other alerts
            log.exception("Push delivery failed")
            pushed, push_errors = 0, ["Push: failed"]
        if pushed:
            sent_to.append("Push")
        errors = list(errors or []) + push_errors
    with tenancy.session_for(tenant) as db:
        db.add(AlertEvent(monitor_id=monitor_id, name=alert.monitor[:160], status=alert.status,
                          message=(alert.message or "")[:300], sent_to=sent_to,
                          errors="; ".join(errors or [])[:500]))
        db.commit()


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

        # Phase timings are kept on heartbeats only when the check failed or was slow
        slow = result.latency_ms is not None and result.latency_ms > m.timeout_sec * 500
        db.add(Heartbeat(monitor_id=m.id, status=status, latency_ms=result.latency_ms,
                         message=result.message[:300], created_at=now,
                         timings=result.timings if (result.status != "up" or slow) else None))
        m.status = status
        m.last_checked = now
        m.last_latency_ms = result.latency_ms
        m.last_message = result.message[:300]
        m.last_timings = result.timings
        if result.findings is not None:
            m.security_findings = result.findings
        if result.ssl_expires_at:
            m.ssl_expires_at = result.ssl_expires_at

        open_incident = db.scalar(
            select(Incident).where(Incident.monitor_id == m.id, Incident.resolved_at.is_(None))
        )
        # A child that is down because its parent is down is covered by the parent's incident
        if status == "down" and open_incident is None and not parent_down:
            open_incident = Incident(monitor_id=m.id, cause=result.message[:300], started_at=now)
            db.add(open_incident)
        elif status in ("up", "degraded") and open_incident is not None:
            open_incident.resolved_at = now

        changed = (status != previous and status not in ("maintenance", "pending")
                   and not (previous == "pending" and status == "up"))
        last_sent = aware(m.last_notified_at)
        repeat = (
            status == "down" and previous == "down" and m.notify_repeat_min is not None
            and (last_sent is None or now - last_sent >= timedelta(minutes=m.notify_repeat_min))
            and not (open_incident is not None and open_incident.acknowledged_at)
        )
        notify = (changed or repeat) and not parent_down
        if notify:
            m.last_notified_at = now
        db.commit()
        if result.status in ("down", "degraded"):
            otel.export_failed_check(m.name, m.target, result.status, result.message, result.timings)

        if not notify:
            return
        message = ("Still down: " if repeat and not changed else "") + result.message
        incident_id = open_incident.id if open_incident is not None and not open_incident.resolved_at else None
        channels, alert, emails, smtp = build_alert(db, tenant, m, status, message, incident_id=incident_id)

    await deliver(tenant, monitor_id, channels, alert, emails, smtp)


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
        await deliver(tenant, None, channels, alert, emails, smtp)


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
            await deliver(tenant, m.id, channels, alert, emails, smtp)


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
            result = await run_check(m, target_policy())
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
                audit_cutoff = now - timedelta(days=int(get_setting(db, "general")["audit_keep_days"]))
                db.execute(delete(Heartbeat).where(Heartbeat.created_at < cutoff))
                db.execute(delete(Incident).where(Incident.resolved_at.is_not(None), Incident.resolved_at < cutoff))
                # SQLite does not enforce ON DELETE CASCADE here, so drop notes of deleted incidents
                db.execute(delete(IncidentAck).where(IncidentAck.incident_id.not_in(select(Incident.id))))
                db.execute(delete(MaintenanceWindow).where(MaintenanceWindow.ends_at < cutoff))
                db.execute(delete(LoginEvent).where(LoginEvent.created_at < cutoff))
                db.execute(delete(AlertEvent).where(AlertEvent.created_at < cutoff))
                # The audit log has its own retention
                db.execute(delete(AuditEvent).where(AuditEvent.created_at < audit_cutoff))
                db.commit()


scheduler = Scheduler()
