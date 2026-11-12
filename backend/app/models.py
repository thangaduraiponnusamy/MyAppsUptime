from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .crypto import EncryptedText
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
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id", ondelete="SET NULL"), index=True)
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
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_by: Mapped[str | None] = mapped_column(String(120))
    note: Mapped[str | None] = mapped_column(String(1000))

    monitor: Mapped[Monitor] = relationship(back_populates="incidents")


class NotificationChannel(Base):
    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(20))  # webhook | slack | teams | telegram | discord | googlechat | ntfy | email
    url: Mapped[str] = mapped_column(EncryptedText, default="")  # webhook URLs contain secrets
    recipients: Mapped[list] = mapped_column(JSON, default=list)  # email addresses for email channels
    tags: Mapped[list] = mapped_column(JSON, default=list)  # empty = all monitors
    project_ids: Mapped[list] = mapped_column(JSON, default=list)  # empty = all projects
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


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str | None] = mapped_column(String(500))
    member_ids: Mapped[list] = mapped_column(JSON, default=list)  # user ids: see the project and get its alerts
    email_members: Mapped[bool] = mapped_column(default=True)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(60), unique=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    email: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(10), default="member")  # superadmin | admin | member
    password_hash: Mapped[str] = mapped_column(String(200))
    must_change_password: Mapped[bool] = mapped_column(default=False)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10), default="session")  # session | reset
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)


class Tenant(Base):
    """Tenant registry; only used in the default (admin) database."""
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(30), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)