import html
import math
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import notifier, probes, tenancy
from .auth import Principal, get_db, require_admin, require_user, visible_monitor_ids
from .checks import CheckResult
from .models import (Heartbeat, Incident, MaintenanceWindow, Monitor, NotificationChannel, Probe, Project, StatusPage,
                     aware, utcnow)
from .scheduler import record_result, scheduler, tenant_config
from .schemas import (AckIn, ChannelIn, ChannelOut, DashboardOut, DashboardPoint, ExportData, HeartbeatOut,
                      IncidentOut, MaintenanceIn, MaintenanceOut, MonitorCreate, MonitorExport, MonitorOut,
                      ProjectExport, PublicIncident, PublicMonitor, PublicStatusOut,
                      StatsOut, StatusPageIn, StatusPageOut, SummaryOut)

MONTH_MIN = 30 * 24 * 60

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])
public = APIRouter(prefix="/api/public")


# ---------- helpers ----------

def _get(db: Session, model, obj_id: int):
    obj = db.get(model, obj_id)
    if obj is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__name__} {obj_id} not found")
    return obj


def _uptime_from_counts(counts: dict[str, int]) -> float | None:
    good = counts.get("up", 0) + counts.get("degraded", 0)
    total = good + counts.get("down", 0)
    return round(good / total * 100, 2) if total else None


def _status_counts(db: Session, since, monitor_ids: list[int] | None = None) -> dict[int, dict[str, int]]:
    query = (
        select(Heartbeat.monitor_id, Heartbeat.status, func.count())
        .where(Heartbeat.created_at >= since)
        .group_by(Heartbeat.monitor_id, Heartbeat.status)
    )
    if monitor_ids is not None:
        query = query.where(Heartbeat.monitor_id.in_(monitor_ids))
    grouped: dict[int, dict[str, int]] = {}
    for monitor_id, hb_status, count in db.execute(query):
        grouped.setdefault(monitor_id, {})[hb_status] = count
    return grouped


def _uptime_by_monitor(db: Session, since, monitor_ids: list[int] | None = None) -> dict[int, float | None]:
    return {mid: _uptime_from_counts(c) for mid, c in _status_counts(db, since, monitor_ids).items()}


def _recent(db: Session, monitor_id: int, limit: int) -> list[tuple[str, float | None]]:
    rows = db.execute(
        select(Heartbeat.status, Heartbeat.latency_ms).where(Heartbeat.monitor_id == monitor_id)
        .order_by(Heartbeat.created_at.desc()).limit(limit)
    ).all()
    return [(r[0], r[1]) for r in reversed(rows)]


STATE_GROUPS = ({"up", "degraded"}, {"down"}, {"maintenance"})


def _status_since(db: Session, m: Monitor) -> datetime | None:
    """When the monitor entered its current state (first heartbeat after the last different one)."""
    group = next((g for g in STATE_GROUPS if m.status in g), None)
    if not m.active or group is None:
        return None
    changed = db.scalar(select(func.max(Heartbeat.created_at)).where(
        Heartbeat.monitor_id == m.id, Heartbeat.status.not_in(group)))
    query = select(func.min(Heartbeat.created_at)).where(Heartbeat.monitor_id == m.id)
    if changed is not None:
        query = query.where(Heartbeat.created_at > changed)
    return aware(db.scalar(query))


def _monitor_out(db: Session, m: Monitor, uptime: float | None) -> MonitorOut:
    out = MonitorOut.model_validate(m)
    out.uptime_24h = uptime
    out.status_since = _status_since(db, m)
    recent = _recent(db, m.id, 30)
    out.recent = [s for s, _ in recent]
    out.recent_latency = [lat for _, lat in recent]
    if m.probe_id is not None:
        out.probe_name = db.scalar(select(Probe.name).where(Probe.id == m.probe_id))
    return out


def _check_parent(db: Session, parent_id: int | None, self_id: int | None) -> None:
    if parent_id is None:
        return
    if parent_id == self_id or db.get(Monitor, parent_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid parent monitor")


def _check_project(db: Session, project_id: int | None) -> None:
    if project_id is not None and db.get(Project, project_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid project")


def _scope(db: Session, user: Principal, project_id: int | None = None) -> list[int] | None:
    """Monitor ids the user may see (None = all), optionally narrowed to one project (0 = no project)."""
    ids = visible_monitor_ids(db, user)
    if project_id is None:
        return None if ids is None else sorted(ids)
    column = Monitor.project_id.is_(None) if project_id == 0 else Monitor.project_id == project_id
    in_project = set(db.scalars(select(Monitor.id).where(column)).all())
    return sorted(in_project if ids is None else ids & in_project)


def _get_monitor(db: Session, user: Principal, monitor_id: int) -> Monitor:
    ids = visible_monitor_ids(db, user)
    if ids is not None and monitor_id not in ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Monitor {monitor_id} not found")
    return _get(db, Monitor, monitor_id)


def _monitor_is_public(db: Session, monitor_id: int) -> bool:
    return any(monitor_id in (p.monitor_ids or []) for p in db.scalars(select(StatusPage)).all())


# ---------- summary & monitors ----------

@router.get("/summary", response_model=SummaryOut)
def summary(project_id: int | None = None, db: Session = Depends(get_db), user: Principal = Depends(require_user)):
    ids = _scope(db, user, project_id)
    query = select(Monitor)
    open_query = select(Incident).where(Incident.resolved_at.is_(None))
    if ids is not None:
        query = query.where(Monitor.id.in_(ids))
        open_query = open_query.where(Incident.monitor_id.in_(ids))
    monitors = db.scalars(query).all()
    soon = utcnow() + timedelta(days=14)
    active = [m for m in monitors if m.active]
    uptimes = [u for u in _uptime_by_monitor(db, utcnow() - timedelta(hours=24), ids).values() if u is not None]
    latencies = [m.last_latency_ms for m in active if m.last_latency_ms is not None]
    open_incidents = db.scalars(open_query).all()
    return SummaryOut(
        total=len(monitors),
        up=sum(1 for m in active if m.status == "up"),
        down=sum(1 for m in active if m.status == "down"),
        degraded=sum(1 for m in active if m.status == "degraded"),
        maintenance=sum(1 for m in active if m.status == "maintenance"),
        paused=len(monitors) - len(active),
        open_incidents=len(open_incidents),
        unacknowledged=sum(1 for i in open_incidents if i.acknowledged_at is None),
        ssl_expiring_soon=sum(1 for m in monitors if m.ssl_expires_at and aware(m.ssl_expires_at) <= soon),
        avg_uptime_24h=round(sum(uptimes) / len(uptimes), 2) if uptimes else None,
        avg_latency_ms=round(sum(latencies) / len(latencies), 1) if latencies else None,
    )


@router.get("/monitors", response_model=list[MonitorOut])
def list_monitors(tag: str | None = None, project_id: int | None = None, db: Session = Depends(get_db),
                  user: Principal = Depends(require_user)):
    ids = _scope(db, user, project_id)
    query = select(Monitor).order_by(Monitor.name)
    if ids is not None:
        query = query.where(Monitor.id.in_(ids))
    monitors = db.scalars(query).all()
    if tag:
        monitors = [m for m in monitors if tag in (m.tags or [])]
    uptimes = _uptime_by_monitor(db, utcnow() - timedelta(hours=24), ids)
    return [_monitor_out(db, m, uptimes.get(m.id)) for m in monitors]


class BulkIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=1000)
    action: Literal["pause", "resume", "delete", "project"]
    project_id: int | None = None


@router.post("/monitors/bulk")
def bulk_monitors(body: BulkIn, db: Session = Depends(get_db)):
    if body.action == "project":
        _check_project(db, body.project_id)
    monitors = db.scalars(select(Monitor).where(Monitor.id.in_(body.ids))).all()
    for m in monitors:
        if body.action == "pause":
            m.active, m.status = False, "paused"
        elif body.action == "resume" and not m.active:
            m.active, m.status, m.last_checked = True, "pending", None
        elif body.action == "project":
            m.project_id = body.project_id
        elif body.action == "delete":
            db.delete(m)
    db.commit()
    return {"updated": len(monitors)}


@router.post("/monitors", response_model=MonitorOut, status_code=status.HTTP_201_CREATED)
def create_monitor(body: MonitorCreate, db: Session = Depends(get_db)):
    _check_parent(db, body.parent_id, None)
    _check_project(db, body.project_id)
    probes.check_probe(db, body.probe_id)
    m = Monitor(**body.model_dump())
    if m.type == "push":
        m.push_token = secrets.token_urlsafe(16)
    db.add(m)
    db.commit()
    return _monitor_out(db, m, None)


@router.get("/monitors/{monitor_id}", response_model=MonitorOut)
def get_monitor(monitor_id: int, db: Session = Depends(get_db), user: Principal = Depends(require_user)):
    m = _get_monitor(db, user, monitor_id)
    return _monitor_out(db, m, _uptime_by_monitor(db, utcnow() - timedelta(hours=24), [m.id]).get(m.id))


@router.put("/monitors/{monitor_id}", response_model=MonitorOut)
def update_monitor(monitor_id: int, body: MonitorCreate, db: Session = Depends(get_db)):
    m = _get(db, Monitor, monitor_id)
    _check_parent(db, body.parent_id, m.id)
    _check_project(db, body.project_id)
    probes.check_probe(db, body.probe_id)
    for key, value in body.model_dump().items():
        setattr(m, key, value)
    if m.type == "push" and not m.push_token:
        m.push_token = secrets.token_urlsafe(16)
    m.last_checked = None  # re-check right away with the new settings
    db.commit()
    return _monitor_out(db, m, None)


@router.delete("/monitors/{monitor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_monitor(monitor_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, Monitor, monitor_id))
    db.commit()


@router.post("/monitors/{monitor_id}/pause", response_model=MonitorOut)
def pause_monitor(monitor_id: int, db: Session = Depends(get_db)):
    m = _get(db, Monitor, monitor_id)
    m.active, m.status = False, "paused"
    db.commit()
    return _monitor_out(db, m, None)


@router.post("/monitors/{monitor_id}/resume", response_model=MonitorOut)
def resume_monitor(monitor_id: int, db: Session = Depends(get_db)):
    m = _get(db, Monitor, monitor_id)
    m.active, m.status, m.last_checked = True, "pending", None
    db.commit()
    return _monitor_out(db, m, None)


@router.post("/monitors/{monitor_id}/check", status_code=status.HTTP_202_ACCEPTED)
async def check_now(monitor_id: int, db: Session = Depends(get_db), user: Principal = Depends(require_user)):
    m = _get_monitor(db, user, monitor_id)
    if not m.active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Monitor is paused")
    if m.probe_id is not None:
        probes.run_now(db, m)
        return {"queued": True}
    return {"queued": scheduler.trigger(tenancy.tenant_of(db), m)}


@router.get("/monitors/{monitor_id}/heartbeats", response_model=list[HeartbeatOut])
def heartbeats(monitor_id: int, hours: int = Query(24, ge=1, le=24 * 90), start: datetime | None = None,
               end: datetime | None = None, db: Session = Depends(get_db), user: Principal = Depends(require_user)):
    _get_monitor(db, user, monitor_id)
    since, until = _window(hours, start, end)
    return db.scalars(
        select(Heartbeat)
        .where(Heartbeat.monitor_id == monitor_id, Heartbeat.created_at >= since, Heartbeat.created_at <= until)
        .order_by(Heartbeat.created_at)
    ).all()


@router.get("/monitors/{monitor_id}/stats", response_model=StatsOut)
def stats(monitor_id: int, db: Session = Depends(get_db), user: Principal = Depends(require_user)):
    m = _get_monitor(db, user, monitor_id)
    now = utcnow()
    latencies = sorted(db.scalars(
        select(Heartbeat.latency_ms).where(
            Heartbeat.monitor_id == monitor_id,
            Heartbeat.created_at >= now - timedelta(hours=24),
            Heartbeat.latency_ms.is_not(None),
        )
    ).all())
    uptime = lambda delta: _uptime_by_monitor(db, now - delta, [monitor_id]).get(monitor_id)  # noqa: E731

    incidents = db.scalars(select(Incident).where(
        Incident.monitor_id == monitor_id, Incident.started_at >= now - timedelta(days=30))).all()
    resolved = [(aware(i.resolved_at) - aware(i.started_at)).total_seconds() / 60 for i in incidents if i.resolved_at]

    down_beats = _status_counts(db, now - timedelta(days=30), [monitor_id]).get(monitor_id, {}).get("down", 0)
    downtime = round(down_beats * m.interval_sec / 60, 1)
    budget = round(MONTH_MIN * (1 - m.sla_target / 100), 1)
    remaining = max(0.0, 100 - downtime / budget * 100) if budget else (100.0 if downtime == 0 else 0.0)

    return StatsOut(
        uptime_24h=uptime(timedelta(hours=24)),
        uptime_7d=uptime(timedelta(days=7)),
        uptime_30d=uptime(timedelta(days=30)),
        avg_latency_ms=round(sum(latencies) / len(latencies), 1) if latencies else None,
        p95_latency_ms=latencies[max(0, math.ceil(len(latencies) * 0.95) - 1)] if latencies else None,
        incidents_30d=len(incidents),
        mttr_min=round(sum(resolved) / len(resolved), 1) if resolved else None,
        sla_target=m.sla_target,
        error_budget_min=budget,
        downtime_30d_min=downtime,
        budget_remaining_pct=round(remaining, 1),
    )


def _window(hours: int, start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    now = utcnow()
    if not (start or end):
        return now - timedelta(hours=hours), now
    if not (start and end):
        raise HTTPException(400, "Both start and end are required")
    since = aware(start).astimezone(timezone.utc)
    until = min(aware(end).astimezone(timezone.utc), now)
    if until <= since:
        raise HTTPException(400, "End must be after start")
    if until - since > timedelta(days=90):
        raise HTTPException(400, "Range can be at most 90 days")
    return since, until


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    hours: int = Query(24, ge=1, le=24 * 90),
    start: datetime | None = None,
    end: datetime | None = None,
    project_id: int | None = None,
    db: Session = Depends(get_db),
    user: Principal = Depends(require_user),
):
    since, until = _window(hours, start, end)
    ids = _scope(db, user, project_id)

    span_sec = (until - since).total_seconds()
    bucket_sec = next(size for limit, size in (
        (6 * 3600, 300), (48 * 3600, 900), (8 * 86400, 3600), (31 * 86400, 6 * 3600), (math.inf, 86400),
    ) if span_sec <= limit)
    count = max(1, math.ceil(span_sec / bucket_sec))
    latencies: list[list[float]] = [[] for _ in range(count)]
    good, bad = [0] * count, [0] * count

    beats = (select(Heartbeat.created_at, Heartbeat.status, Heartbeat.latency_ms)
             .where(Heartbeat.created_at >= since, Heartbeat.created_at <= until))
    if ids is not None:
        beats = beats.where(Heartbeat.monitor_id.in_(ids))
    rows = db.execute(beats)
    for created_at, hb_status, latency in rows:
        idx = min(count - 1, max(0, int((aware(created_at) - since).total_seconds() // bucket_sec)))
        if latency is not None:
            latencies[idx].append(latency)
        if hb_status in ("up", "degraded"):
            good[idx] += 1
        elif hb_status == "down":
            bad[idx] += 1

    series = [
        DashboardPoint(
            at=since + timedelta(seconds=i * bucket_sec),
            avg_latency_ms=round(sum(latencies[i]) / len(latencies[i]), 1) if latencies[i] else None,
            uptime=round(good[i] / (good[i] + bad[i]) * 100, 2) if good[i] + bad[i] else None,
            checks=good[i] + bad[i],
        )
        for i in range(count)
    ]
    recent = []
    in_range = (Incident.started_at <= until) & (Incident.resolved_at.is_(None) | (Incident.resolved_at >= since))
    if ids is not None:
        in_range = in_range & Incident.monitor_id.in_(ids)
    for incident, name in db.execute(
        select(Incident, Monitor.name).join(Monitor).where(in_range).order_by(Incident.started_at.desc()).limit(20)
    ):
        out = IncidentOut.model_validate(incident)
        out.monitor_name = name
        recent.append(out)
    total_good, total_bad = sum(good), sum(bad)
    all_latency = [v for bucket in latencies for v in bucket]
    return DashboardOut(
        series=series,
        incidents=recent,
        uptime=round(total_good / (total_good + total_bad) * 100, 2) if total_good + total_bad else None,
        avg_latency_ms=round(sum(all_latency) / len(all_latency), 1) if all_latency else None,
        checks=total_good + total_bad,
        incident_count=db.scalar(select(func.count()).select_from(Incident).where(in_range)) or 0,
    )


# ---------- incidents ----------

@router.get("/incidents", response_model=list[IncidentOut])
def incidents(
    open_only: bool = False,
    limit: int | None = Query(None, ge=1),
    hours: int | None = Query(None, ge=1, le=24 * 3650),
    start: datetime | None = None,
    end: datetime | None = None,
    project_id: int | None = None,
    db: Session = Depends(get_db),
    user: Principal = Depends(require_user),
):
    query = select(Incident, Monitor.name).join(Monitor).order_by(Incident.started_at.desc())
    if limit:
        query = query.limit(limit)
    ids = _scope(db, user, project_id)
    if ids is not None:
        query = query.where(Incident.monitor_id.in_(ids))
    if hours or start or end:
        since, until = _window(hours or 24, start, end)
        query = query.where(Incident.started_at <= until, Incident.resolved_at.is_(None) | (Incident.resolved_at >= since))
    if open_only:
        query = query.where(Incident.resolved_at.is_(None))
    result = []
    for incident, name in db.execute(query):
        out = IncidentOut.model_validate(incident)
        out.monitor_name = name
        result.append(out)
    return result


@router.post("/incidents/{incident_id}/ack", response_model=IncidentOut)
def acknowledge(incident_id: int, body: AckIn, db: Session = Depends(get_db), user: Principal = Depends(require_user)):
    incident = _get(db, Incident, incident_id)
    _get_monitor(db, user, incident.monitor_id)
    incident.acknowledged_at = incident.acknowledged_at or utcnow()
    incident.acknowledged_by = body.by
    incident.note = body.note
    incident.public_note = body.public_note
    db.commit()
    out = IncidentOut.model_validate(incident)
    out.monitor_name = incident.monitor.name
    return out


# ---------- notification channels ----------

def _check_projects(db: Session, project_ids: list[int]) -> None:
    for project_id in project_ids:
        _check_project(db, project_id)


@router.get("/notifications", response_model=list[ChannelOut], dependencies=[Depends(require_admin)])
def list_channels(db: Session = Depends(get_db)):
    return db.scalars(select(NotificationChannel)).all()


@router.post("/notifications", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
def create_channel(body: ChannelIn, db: Session = Depends(get_db)):
    _check_projects(db, body.project_ids)
    channel = NotificationChannel(**body.model_dump())
    db.add(channel)
    db.commit()
    return channel


@router.put("/notifications/{channel_id}", response_model=ChannelOut)
def update_channel(channel_id: int, body: ChannelIn, db: Session = Depends(get_db)):
    channel = _get(db, NotificationChannel, channel_id)
    _check_projects(db, body.project_ids)
    for key, value in body.model_dump().items():
        setattr(channel, key, value)
    db.commit()
    return channel


@router.delete("/notifications/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_channel(channel_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, NotificationChannel, channel_id))
    db.commit()


@router.post("/notifications/{channel_id}/test")
async def test_channel(channel_id: int, db: Session = Depends(get_db)):
    channel = _get(db, NotificationChannel, channel_id)
    general, smtp = tenant_config(db, tenancy.tenant_of(db))
    alert = notifier.Alert(monitor="Test monitor", status="up", message="This is a test alert from MyAppsUptime",
                           project="Test", target="https://example.com", **notifier.alert_context(general))
    errors = await notifier.send([channel], alert, smtp=smtp)
    if errors:
        # 400, not 502: proxies like Cloudflare replace 502 bodies with their own page
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "; ".join(errors))
    return {"sent": True}


# ---------- maintenance ----------

@router.get("/maintenance", response_model=list[MaintenanceOut])
def list_maintenance(db: Session = Depends(get_db)):
    return db.scalars(select(MaintenanceWindow).order_by(MaintenanceWindow.starts_at.desc())).all()


@router.post("/maintenance", response_model=MaintenanceOut, status_code=status.HTTP_201_CREATED)
def create_maintenance(body: MaintenanceIn, db: Session = Depends(get_db)):
    window = MaintenanceWindow(**body.model_dump())
    db.add(window)
    db.commit()
    return window


@router.delete("/maintenance/{window_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_maintenance(window_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, MaintenanceWindow, window_id))
    db.commit()


# ---------- status pages ----------

@router.get("/status-pages", response_model=list[StatusPageOut])
def list_status_pages(db: Session = Depends(get_db)):
    return db.scalars(select(StatusPage)).all()


@router.post("/status-pages", response_model=StatusPageOut, status_code=status.HTTP_201_CREATED)
def create_status_page(body: StatusPageIn, db: Session = Depends(get_db)):
    _check_page_unique(db, body, None)
    page = StatusPage(**body.model_dump())
    db.add(page)
    db.commit()
    return page


@router.put("/status-pages/{page_id}", response_model=StatusPageOut)
def update_status_page(page_id: int, body: StatusPageIn, db: Session = Depends(get_db)):
    page = _get(db, StatusPage, page_id)
    _check_page_unique(db, body, page_id)
    for key, value in body.model_dump().items():
        setattr(page, key, value)
    db.commit()
    return page


def _check_page_unique(db: Session, body: StatusPageIn, page_id: int | None) -> None:
    """Slugs (/status/<slug>) and custom domains are unique across tenants."""
    here = tenancy.tenant_of(db)
    checks = [(StatusPage.slug == body.slug, "Slug already exists")]
    if body.domain:
        checks.append((StatusPage.domain == body.domain, "Domain is already used by another status page"))
    for slug in tenancy.slugs(active_only=False):
        with tenancy.session_for(slug) as other:
            for where, message in checks:
                query = select(StatusPage.id).where(where)
                if slug == here and page_id is not None:
                    query = query.where(StatusPage.id != page_id)
                if other.scalar(query) is not None:
                    raise HTTPException(status.HTTP_409_CONFLICT, message)


def page_for_host(host: str) -> str | None:
    """Slug of the status page whose custom domain is this Host header."""
    host = host.split(":")[0].strip().lower().rstrip(".")
    if not host or "." not in host:
        return None
    for tenant in tenancy.slugs():
        with tenancy.session_for(tenant) as db:
            slug = db.scalar(select(StatusPage.slug).where(StatusPage.domain == host))
            if slug:
                return slug
    return None


@router.delete("/status-pages/{page_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_status_page(page_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, StatusPage, page_id))
    db.commit()


# ---------- metrics, export, import ----------

def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(db: Session = Depends(get_db), user: Principal = Depends(require_user)):
    ids = _scope(db, user)
    query = select(Monitor) if ids is None else select(Monitor).where(Monitor.id.in_(ids))
    monitors = db.scalars(query).all()
    uptimes = _uptime_by_monitor(db, utcnow() - timedelta(hours=24), ids)
    lines = [
        "# TYPE myappsuptime_monitor_up gauge",
        "# TYPE myappsuptime_monitor_latency_ms gauge",
        "# TYPE myappsuptime_monitor_uptime_24h gauge",
        "# TYPE myappsuptime_ssl_days_left gauge",
    ]
    for m in monitors:
        labels = f'id="{m.id}",name="{_label(m.name)}",type="{m.type}"'
        lines.append(f"myappsuptime_monitor_up{{{labels}}} {1 if m.status in ('up', 'degraded') else 0}")
        if m.last_latency_ms is not None:
            lines.append(f"myappsuptime_monitor_latency_ms{{{labels}}} {m.last_latency_ms}")
        if uptimes.get(m.id) is not None:
            lines.append(f"myappsuptime_monitor_uptime_24h{{{labels}}} {uptimes[m.id]}")
        if m.ssl_expires_at:
            lines.append(f"myappsuptime_ssl_days_left{{{labels}}} {(aware(m.ssl_expires_at) - utcnow()).days}")
    return "\n".join(lines) + "\n"


@router.get("/export", response_model=ExportData, dependencies=[Depends(require_admin)])
def export_config(db: Session = Depends(get_db)):
    monitors = db.scalars(select(Monitor)).all()
    channels = db.scalars(select(NotificationChannel)).all()
    projects = db.scalars(select(Project)).all()
    return ExportData(
        monitors=[MonitorExport.model_validate(m, from_attributes=True) for m in monitors],
        channels=[ChannelIn.model_validate(c, from_attributes=True) for c in channels],
        # Members are user accounts of this server, so they are not exported
        projects=[ProjectExport(id=p.id, name=p.name, description=p.description, email_members=p.email_members)
                  for p in projects],
    )


@router.post("/import")
def import_config(body: ExportData, db: Session = Depends(get_db)):
    project_map: dict[int, int] = {}
    for item in body.projects:
        project = db.scalar(select(Project).where(Project.name == item.name))
        if project is None:
            project = Project(name=item.name, description=item.description, email_members=item.email_members)
            db.add(project)
            db.flush()
        project_map[item.id] = project.id
    id_map: dict[int, Monitor] = {}
    for item in body.monitors:
        data = item.model_dump(exclude={"id", "parent_id", "project_id", "probe_id"})
        m = Monitor(**data, project_id=project_map.get(item.project_id),
                    push_token=secrets.token_urlsafe(16) if item.type == "push" else None)
        db.add(m)
        id_map[item.id] = m
    db.flush()
    for item in body.monitors:
        if item.parent_id in id_map:
            id_map[item.id].parent_id = id_map[item.parent_id].id
    for channel in body.channels:
        data = channel.model_dump()
        data["project_ids"] = [project_map[p] for p in channel.project_ids if p in project_map]
        db.add(NotificationChannel(**data))
    db.commit()
    return {"monitors": len(body.monitors), "channels": len(body.channels), "projects": len(body.projects)}


# ---------- public endpoints (no token) ----------

@public.api_route("/push/{token}", methods=["GET", "POST"])
async def push(
    token: str,
    status_: Literal["up", "down"] = Query("up", alias="status"),
    msg: str = Query("OK", max_length=300),
    ping: float | None = Query(None, ge=0),
):
    for slug in tenancy.slugs():
        with tenancy.session_for(slug) as db:
            m = db.scalar(select(Monitor).where(Monitor.push_token == token, Monitor.type == "push"))
            if m is None:
                continue
            if not m.active:
                return {"ok": False, "reason": "paused"}
            m.last_push_at = utcnow()
            db.commit()
        await record_result(slug, m.id, CheckResult(status_, ping, msg))
        return {"ok": True}
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown push token")


@public.get("/status/{slug}", response_model=PublicStatusOut)
def public_status(slug: str):
    for tenant in tenancy.slugs():
        with tenancy.session_for(tenant) as db:
            page = db.scalar(select(StatusPage).where(StatusPage.slug == slug))
            if page is not None:
                return _public_status(db, page)
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Status page not found")


def _public_status(db: Session, page: StatusPage) -> PublicStatusOut:
    monitors = db.scalars(select(Monitor).where(Monitor.id.in_(page.monitor_ids))).all()
    uptimes = _uptime_by_monitor(db, utcnow() - timedelta(days=30), [m.id for m in monitors])
    items = []
    for m in monitors:
        beats = list(reversed(db.execute(
            select(Heartbeat.status, Heartbeat.created_at).where(Heartbeat.monitor_id == m.id)
            .order_by(Heartbeat.created_at.desc()).limit(60)).all()))
        items.append(PublicMonitor(id=m.id, name=m.name, status=m.status, uptime_30d=uptimes.get(m.id),
                                   bars=[b[0] for b in beats], times=[b[1] for b in beats]))
    statuses = {i.status for i in items}
    overall = "down" if "down" in statuses else "degraded" if statuses & {"degraded", "maintenance"} else "up"

    # Internal causes and notes stay private; only the public note is shown
    incidents = [
        PublicIncident(monitor_name=name, started_at=i.started_at, resolved_at=i.resolved_at, note=i.public_note)
        for i, name in db.execute(
            select(Incident, Monitor.name).join(Monitor)
            .where(Incident.monitor_id.in_(page.monitor_ids), Incident.started_at >= utcnow() - timedelta(days=14))
            .order_by(Incident.started_at.desc()).limit(10)
        )
    ]
    return PublicStatusOut(title=page.title, description=page.description, logo=page.logo, overall=overall,
                           monitors=items, incidents=incidents)


BADGE_COLORS = {"up": "#16a34a", "down": "#dc2626", "degraded": "#d97706", "maintenance": "#2563eb"}


def _badge_svg(label: str, value: str, color: str) -> str:
    lw, vw = 6 * len(label) + 12, 6 * len(value) + 12
    label, value = html.escape(label), html.escape(value)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{lw + vw}" height="20" role="img" '
        f'aria-label="{label}: {value}"><rect width="{lw}" height="20" rx="3" fill="#475569"/>'
        f'<rect x="{lw}" width="{vw}" height="20" rx="3" fill="{color}"/>'
        f'<g fill="#fff" font-family="Verdana,Geneva,sans-serif" font-size="11" text-anchor="middle">'
        f'<text x="{lw / 2}" y="14">{label}</text><text x="{lw + vw / 2}" y="14">{value}</text></g></svg>'
    )


@public.get("/badge/{monitor_id}/{kind}.svg")
def badge(monitor_id: int, kind: Literal["status", "uptime", "latency"],
          t: str = Query(tenancy.DEFAULT, max_length=30)):
    if not tenancy.is_active(t):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Badge not found")
    with tenancy.session_for(t) as db:
        return _badge(db, monitor_id, kind)


def _badge(db: Session, monitor_id: int, kind: str) -> Response:
    m = db.get(Monitor, monitor_id)
    # Only monitors shown on a public status page expose badges
    if m is None or not _monitor_is_public(db, monitor_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Badge not found")
    if kind == "status":
        svg = _badge_svg(m.name[:30], m.status, BADGE_COLORS.get(m.status, "#64748b"))
    elif kind == "uptime":
        value = _uptime_by_monitor(db, utcnow() - timedelta(days=30), [m.id]).get(m.id)
        svg = _badge_svg("uptime 30d", "--" if value is None else f"{value}%", "#7c3aed")
    else:
        value = m.last_latency_ms
        svg = _badge_svg("latency", "--" if value is None else f"{round(value)} ms", "#0891b2")
    return Response(svg, media_type="image/svg+xml", headers={"Cache-Control": "no-cache"})
