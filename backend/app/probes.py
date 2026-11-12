"""Private locations: probes on other networks fetch their monitors and send results back."""
import hashlib
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import notifier, tenancy
from .auth import bearer, get_db, require_admin, token_tenant
from .checks import CheckResult
from .models import Monitor, Probe, aware, utcnow
from .scheduler import PROBE_OFFLINE, probe_alert, record_result
from .schemas import ProbeCreated, ProbeIn, ProbeOut, ProbeSyncIn

admin = APIRouter(prefix="/api/probes", dependencies=[Depends(require_admin)])
probe = APIRouter(prefix="/api/probe")

JOB_FIELDS = ("id", "name", "type", "target", "port", "keyword", "json_path", "json_expected", "method",
              "expected_status", "interval_sec", "timeout_sec", "retries", "latency_threshold_ms",
              "ssl_expiry_warn_days")

# "Check now" requests waiting for the next probe sync: (tenant, probe id) -> monitor ids
_run_now: dict[tuple[str, int], set[int]] = {}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_token(db: Session) -> str:
    # The tenant prefix tells which database holds the probe
    return f"{tenancy.tenant_of(db)}.p{secrets.token_urlsafe(32)}"


def is_online(p: Probe) -> bool:
    return p.last_seen_at is not None and utcnow() - aware(p.last_seen_at) < PROBE_OFFLINE


def _out(db: Session, p: Probe) -> ProbeOut:
    count = db.scalar(select(func.count()).select_from(Monitor).where(Monitor.probe_id == p.id)) or 0
    return ProbeOut(id=p.id, name=p.name, online=is_online(p), version=p.version, last_seen_at=p.last_seen_at,
                    created_at=p.created_at, monitors=count)


def _get(db: Session, probe_id: int) -> Probe:
    p = db.get(Probe, probe_id)
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Private location not found")
    return p


def check_probe(db: Session, probe_id: int | None) -> None:
    if probe_id is not None and db.get(Probe, probe_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid private location")


def run_now(db: Session, m: Monitor) -> None:
    _run_now.setdefault((tenancy.tenant_of(db), m.probe_id), set()).add(m.id)


# ---------- admin ----------

@admin.get("", response_model=list[ProbeOut])
def list_probes(db: Session = Depends(get_db)):
    return [_out(db, p) for p in db.scalars(select(Probe).order_by(Probe.name)).all()]


@admin.post("", response_model=ProbeCreated, status_code=status.HTTP_201_CREATED)
def create_probe(body: ProbeIn, db: Session = Depends(get_db)):
    token = _new_token(db)
    p = Probe(name=body.name, token_hash=_hash(token))
    db.add(p)
    try:
        db.commit()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "A private location with this name already exists")
    return ProbeCreated(**_out(db, p).model_dump(), token=token)


@admin.put("/{probe_id}", response_model=ProbeOut)
def rename_probe(probe_id: int, body: ProbeIn, db: Session = Depends(get_db)):
    p = _get(db, probe_id)
    p.name = body.name
    try:
        db.commit()
    except IntegrityError:
        raise HTTPException(status.HTTP_409_CONFLICT, "A private location with this name already exists")
    return _out(db, p)


@admin.post("/{probe_id}/token", response_model=ProbeCreated)
def new_token(probe_id: int, db: Session = Depends(get_db)):
    p = _get(db, probe_id)
    token = _new_token(db)
    p.token_hash = _hash(token)
    db.commit()
    return ProbeCreated(**_out(db, p).model_dump(), token=token)


@admin.delete("/{probe_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_probe(probe_id: int, db: Session = Depends(get_db)):
    p = _get(db, probe_id)
    # Its monitors move back to this server
    for m in db.scalars(select(Monitor).where(Monitor.probe_id == p.id)).all():
        m.probe_id, m.last_checked = None, None
    db.delete(p)
    db.commit()


# ---------- probe (probe token) ----------

@probe.post("/sync")
async def sync(body: ProbeSyncIn, authorization: str | None = Header(None)):
    """Store the probe's results and return the monitors it must check."""
    token = bearer(authorization)
    tenant = token_tenant(token) if token else ""
    if not token or not tenancy.is_active(tenant):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid probe token")
    back_online = None
    with tenancy.session_for(tenant) as db:
        p = db.scalar(select(Probe).where(Probe.token_hash == _hash(token)))
        if p is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid probe token")
        if p.offline_notified:
            back_online = probe_alert(db, tenant, p, "up", "Private location is reporting again")
        p.last_seen_at, p.version, p.offline_notified = utcnow(), body.version or None, False
        monitors = db.scalars(select(Monitor).where(Monitor.probe_id == p.id, Monitor.active.is_(True))).all()
        jobs = [{f: getattr(m, f) for f in JOB_FIELDS} for m in monitors]
        name, probe_id = p.name, p.id
        db.commit()

    allowed = {j["id"] for j in jobs}
    for r in body.results:
        if r.monitor_id in allowed:
            await record_result(tenant, r.monitor_id,
                                CheckResult(r.status, r.latency_ms, r.message, r.ssl_expires_at))
    if back_online:
        await notifier.send(*back_online)
    return {"name": name, "monitors": jobs, "run_now": sorted(_run_now.pop((tenant, probe_id), set()) & allowed)}
