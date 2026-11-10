import html
import math
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import notifier
from .checks import CheckResult
from .database import get_db
from .models import (Heartbeat, Incident, MaintenanceWindow, Monitor, NotificationChannel, StatusPage,
                     aware, utcnow)
from .scheduler import record_result, scheduler
from .schemas import (AckIn, ChannelIn, ChannelOut, DashboardOut, DashboardPoint, ExportData, HeartbeatOut,
                      IncidentOut, MaintenanceIn, MaintenanceOut, MonitorCreate, MonitorExport, MonitorOut,
                      PublicIncident, PublicMonitor, PublicStatusOut,
                      StatsOut, StatusPageIn, StatusPageOut, SummaryOut)

API_TOKEN = os.getenv("API_TOKEN")
MONTH_MIN = 30 * 24 * 60


def require_token(authorization: str | None = Header(default=None)) -> None:
    if not API_TOKEN:
        return
    if not authorization or not secrets.compare_digest(authorization, f"Bearer {API_TOKEN}"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing token")


router = APIRouter(prefix="/api", dependencies=[Depends(require_token)])
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


def _monitor_out(db: Session, m: Monitor, uptime: float | None) -> MonitorOut:
    out = MonitorOut.model_validate(m)
    out.uptime_24h = uptime
    recent = _recent(db, m.id, 30)
    out.recent = [s for s, _ in recent]
    out.recent_latency = [lat for _, lat in recent]
    return out


def _check_parent(db: Session, parent_id: int | None, self_id: int | None) -> None:
    if parent_id is None:
        return
    if parent_id == self_id or db.get(Monitor, parent_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid parent monitor")


def _monitor_is_public(db: Session, monitor_id: int) -> bool:
    return any(monitor_id in (p.monitor_ids or []) for p in db.scalars(select(StatusPage)).all())


# ---------- summary & monitors ----------

@router.get("/summary", response_model=SummaryOut)
def summary(db: Session = Depends(get_db)):
    monitors = db.scalars(select(Monitor)).all()
    soon = utcnow() + timedelta(days=14)
    active = [m for m in monitors if m.active]
    uptimes = [u for u in _uptime_by_monitor(db, utcnow() - timedelta(hours=24)).values() if u is not None]
    latencies = [m.last_latency_ms for m in active if m.last_latency_ms is not None]
    open_incidents = db.scalars(select(Incident).where(Incident.resolved_at.is_(None))).all()
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
def list_monitors(tag: str | None = None, db: Session = Depends(get_db)):
    monitors = db.scalars(select(Monitor).order_by(Monitor.name)).all()
    if tag:
        monitors = [m for m in monitors if tag in (m.tags or [])]
    uptimes = _uptime_by_monitor(db, utcnow() - timedelta(hours=24))
    return [_monitor_out(db, m, uptimes.get(m.id)) for m in monitors]


@router.post("/monitors", response_model=MonitorOut, status_code=status.HTTP_201_CREATED)
def create_monitor(body: MonitorCreate, db: Session = Depends(get_db)):
    _check_parent(db, body.parent_id, None)
    m = Monitor(**body.model_dump())
    if m.type == "push":
        m.push_token = secrets.token_urlsafe(16)
    db.add(m)
    db.commit()
    return _monitor_out(db, m, None)


@router.get("/monitors/{monitor_id}", response_model=MonitorOut)
def get_monitor(monitor_id: int, db: Session = Depends(get_db)):
    m = _get(db, Monitor, monitor_id)
    return _monitor_out(db, m, _uptime_by_monitor(db, utcnow() - timedelta(hours=24), [m.id]).get(m.id))


@router.put("/monitors/{monitor_id}", response_model=MonitorOut)
def update_monitor(monitor_id: int, body: MonitorCreate, db: Session = Depends(get_db)):
    m = _get(db, Monitor, monitor_id)
    _check_parent(db, body.parent_id, m.id)
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
def check_now(monitor_id: int, db: Session = Depends(get_db)):
    m = _get(db, Monitor, monitor_id)
    if not m.active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Monitor is paused")
    return {"queued": scheduler.trigger(m)}


@router.get("/monitors/{monitor_id}/heartbeats", response_model=list[HeartbeatOut])
def heartbeats(monitor_id: int, hours: int = Query(24, ge=1, le=24 * 90), db: Session = Depends(get_db)):
    _get(db, Monitor, monitor_id)
    return db.scalars(
        select(Heartbeat)
        .where(Heartbeat.monitor_id == monitor_id, Heartbeat.created_at >= utcnow() - timedelta(hours=hours))
        .order_by(Heartbeat.created_at)
    ).all()


@router.get("/monitors/{monitor_id}/stats", response_model=StatsOut)
def stats(monitor_id: int, db: Session = Depends(get_db)):
    m = _get(db, Monitor, monitor_id)
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


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(
    hours: int = Query(24, ge=1, le=24 * 90),
    start: datetime | None = None,
    end: datetime | None = None,
    db: Session = Depends(get_db),
):
    now = utcnow()
    if start or end:
        if not (start and end):
            raise HTTPException(400, "Both start and end are required")
        since = aware(start).astimezone(timezone.utc)
        until = min(aware(end).astimezone(timezone.utc), now)
        if until <= since:
            raise HTTPException(400, "End must be after start")
        if until - since > timedelta(days=90):
            raise HTTPException(400, "Range can be at most 90 days")
    else:
        since, until = now - timedelta(hours=hours), now

    span_sec = (until - since).total_seconds()
    bucket_sec = 3600 if span_sec <= 48 * 3600 else 6 * 3600 if span_sec <= 7 * 86400 else 86400
    count = max(1, math.ceil(span_sec / bucket_sec))
    latencies: list[list[float]] = [[] for _ in range(count)]
    good, bad = [0] * count, [0] * count

    rows = db.execute(
        select(Heartbeat.created_at, Heartbeat.status, Heartbeat.latency_ms)
        .where(Heartbeat.created_at >= since, Heartbeat.created_at <= until)
    )
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
    for incident, name in db.execute(
        select(Incident, Monitor.name).join(Monitor).where(in_range).order_by(Incident.started_at.desc()).limit(6)
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
def incidents(open_only: bool = False, limit: int = Query(100, le=500), db: Session = Depends(get_db)):
    query = select(Incident, Monitor.name).join(Monitor).order_by(Incident.started_at.desc()).limit(limit)
    if open_only:
        query = query.where(Incident.resolved_at.is_(None))
    result = []
    for incident, name in db.execute(query):
        out = IncidentOut.model_validate(incident)
        out.monitor_name = name
        result.append(out)
    return result


@router.post("/incidents/{incident_id}/ack", response_model=IncidentOut)
def acknowledge(incident_id: int, body: AckIn, db: Session = Depends(get_db)):
    incident = _get(db, Incident, incident_id)
    incident.acknowledged_at = incident.acknowledged_at or utcnow()
    incident.acknowledged_by = body.by
    incident.note = body.note
    db.commit()
    out = IncidentOut.model_validate(incident)
    out.monitor_name = incident.monitor.name
    return out


# ---------- notification channels ----------

@router.get("/notifications", response_model=list[ChannelOut])
def list_channels(db: Session = Depends(get_db)):
    return db.scalars(select(NotificationChannel)).all()


@router.post("/notifications", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
def create_channel(body: ChannelIn, db: Session = Depends(get_db)):
    channel = NotificationChannel(**body.model_dump())
    db.add(channel)
    db.commit()
    return channel


@router.delete("/notifications/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_channel(channel_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, NotificationChannel, channel_id))
    db.commit()


@router.post("/notifications/{channel_id}/test")
async def test_channel(channel_id: int, db: Session = Depends(get_db)):
    channel = _get(db, NotificationChannel, channel_id)
    await notifier.send([channel], "Test monitor", "up", "This is a test notification")
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
    if db.scalar(select(StatusPage).where(StatusPage.slug == body.slug)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Slug already exists")
    page = StatusPage(**body.model_dump())
    db.add(page)
    db.commit()
    return page


@router.delete("/status-pages/{page_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_status_page(page_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, StatusPage, page_id))
    db.commit()


# ---------- metrics, export, import ----------

def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(db: Session = Depends(get_db)):
    monitors = db.scalars(select(Monitor)).all()
    uptimes = _uptime_by_monitor(db, utcnow() - timedelta(hours=24))
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


@router.get("/export", response_model=ExportData)
def export_config(db: Session = Depends(get_db)):
    monitors = db.scalars(select(Monitor)).all()
    channels = db.scalars(select(NotificationChannel)).all()
    return ExportData(
        monitors=[MonitorExport.model_validate(m, from_attributes=True) for m in monitors],
        channels=[ChannelIn.model_validate(c, from_attributes=True) for c in channels],
    )


@router.post("/import")
def import_config(body: ExportData, db: Session = Depends(get_db)):
    id_map: dict[int, Monitor] = {}
    for item in body.monitors:
        data = item.model_dump(exclude={"id", "parent_id"})
        m = Monitor(**data, push_token=secrets.token_urlsafe(16) if item.type == "push" else None)
        db.add(m)
        id_map[item.id] = m
    db.flush()
    for item in body.monitors:
        if item.parent_id in id_map:
            id_map[item.id].parent_id = id_map[item.parent_id].id
    for channel in body.channels:
        db.add(NotificationChannel(**channel.model_dump()))
    db.commit()
    return {"monitors": len(body.monitors), "channels": len(body.channels)}


# ---------- public endpoints (no token) ----------

@public.api_route("/push/{token}", methods=["GET", "POST"])
async def push(
    token: str,
    status_: Literal["up", "down"] = Query("up", alias="status"),
    msg: str = Query("OK", max_length=300),
    ping: float | None = Query(None, ge=0),
    db: Session = Depends(get_db),
):
    m = db.scalar(select(Monitor).where(Monitor.push_token == token, Monitor.type == "push"))
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown push token")
    if not m.active:
        return {"ok": False, "reason": "paused"}
    m.last_push_at = utcnow()
    db.commit()
    await record_result(m.id, CheckResult(status_, ping, msg))
    return {"ok": True}


@public.get("/status/{slug}", response_model=PublicStatusOut)
def public_status(slug: str, db: Session = Depends(get_db)):
    page = db.scalar(select(StatusPage).where(StatusPage.slug == slug))
    if page is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Status page not found")

    monitors = db.scalars(select(Monitor).where(Monitor.id.in_(page.monitor_ids))).all()
    uptimes = _uptime_by_monitor(db, utcnow() - timedelta(days=30), [m.id for m in monitors])
    items = [
        PublicMonitor(id=m.id, name=m.name, status=m.status, uptime_30d=uptimes.get(m.id),
                      bars=[s for s, _ in _recent(db, m.id, 60)])
        for m in monitors
    ]
    statuses = {i.status for i in items}
    overall = "down" if "down" in statuses else "degraded" if statuses & {"degraded", "maintenance"} else "up"

    # Internal causes stay private; only name and timing are public
    incidents = [
        PublicIncident(monitor_name=name, started_at=i.started_at, resolved_at=i.resolved_at)
        for i, name in db.execute(
            select(Incident, Monitor.name).join(Monitor)
            .where(Incident.monitor_id.in_(page.monitor_ids), Incident.started_at >= utcnow() - timedelta(days=14))
            .order_by(Incident.started_at.desc()).limit(10)
        )
    ]
    return PublicStatusOut(title=page.title, description=page.description, overall=overall,
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
def badge(monitor_id: int, kind: Literal["status", "uptime", "latency"], db: Session = Depends(get_db)):
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
