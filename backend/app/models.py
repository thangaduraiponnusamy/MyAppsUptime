from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    # SQLite drops tzinfo on read
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


class Monitor(Base):
    __tablename__ = "monitors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(20))  # http | keyword | json | tcp | ping | dns | ssl | push
    target: Mapped[str] = mapped_column(String(500), default="")
    port: Mapped[int | None]
    keyword: Mapped[str | None] = mapped_column(String(200))
    json_path: Mapped[str | None] = mapped_column(String(200))
    json_expected: Mapped[str | None] = mapped_column(String(200))
    method: Mapped[str] = mapped_column(String(10), default="GET")
    expected_status: Mapped[str] = mapped_column(String(50), default="200-399")
    interval_sec: Mapped[int] = mapped_column(default=60)
    timeout_sec: Mapped[int] = mapped_column(default=10)
    retries: Mapped[int] = mapped_column(default=0)
    latency_threshold_ms: Mapped[int | None]
    ssl_expiry_warn_days: Mapped[int] = mapped_column(default=14)
    sla_target: Mapped[float] = mapped_column(default=99.9)
    anomaly_detection: Mapped[bool] = mapped_column(default=True)
    notify_repeat_min: Mapped[int | None]
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("monitors.id", ondelete="SET NULL"))
    tags: Mapped[list] = mapped_column(JSON, default=list)
    push_token: Mapped[str | None] = mapped_column(String(64), unique=True)

    active: Mapped[bool] = mapped_column(default=True)
    status: Mapped[str] = mapped_column(String(12), default="pending")
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_push_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_latency_ms: Mapped[float | None]
    last_message: Mapped[str | None] = mapped_column(String(300))
    ssl_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    heartbeats: Mapped[list["Heartbeat"]] = relationship(back_populates="monitor", cascade="all, delete-orphan")
    incidents: Mapped[list["Incident"]] = relationship(back_populates="monitor", cascade="all, delete-orphan")


class Heartbeat(Base):
    __tablename__ = "heartbeats"

    id: Mapped[int] = mapped_column(primary_key=True)
    monitor_id: Mapped[int] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(12))
    latency_ms: Mapped[float | None]
    message: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    monitor: Mapped[Monitor] = relationship(back_populates="heartbeats")


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    monitor_id: Mapped[int] = mapped_column(ForeignKey("monitors.id", ondelete="CASCADE"), index=True)
    cause: Mapped[str | None] = mapped_column(String(300))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(String(1000))

    monitor: Mapped[Monitor] = relationship(back_populates="incidents")


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(20))  # webhook | slack | teams
    url: Mapped[str] = mapped_column(String(500))
    tags: Mapped[list] = mapped_column(JSON, default=list)  # empty = all monitors
    active: Mapped[bool] = mapped_column(default=True)


class MaintenanceWindow(Base):
    __tablename__ = "maintenance_windows"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    monitor_ids: Mapped[list] = mapped_column(JSON, default=list)  # empty = all monitors
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class StatusPage(Base):
    __tablename__ = "status_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(500))
    monitor_ids: Mapped[list] = mapped_column(JSON, default=list)
