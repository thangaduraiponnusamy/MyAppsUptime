import re
from datetime import datetime, timezone
from typing import Annotated, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

MonitorType = Literal["http", "keyword", "json", "tcp", "ping", "dns", "ssl", "push"]
ChannelType = Literal["webhook", "slack", "teams", "telegram", "email"]
TELEGRAM_URL = re.compile(r"^https://api\.telegram\.org/bot\d+:[A-Za-z0-9_-]{30,}/sendMessage\?chat_id=(-?\d+|@[A-Za-z0-9_]{5,32})$")
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

# SQLite returns naive datetimes; mark them UTC so clients convert correctly
Utc = Annotated[datetime, AfterValidator(lambda d: d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d)]

HOST_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9.\-:]*$"


def _require_http_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("must be an http(s) URL")
    return value


def _require_host(value: str) -> str:
    if not re.fullmatch(HOST_PATTERN, value):
        raise ValueError("must be a hostname or IP address")
    return value


class MonitorBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: MonitorType = "http"
    target: str = Field(default="", max_length=500)
    port: int | None = Field(default=None, ge=1, le=65535)
    keyword: str | None = Field(default=None, max_length=200)
    json_path: str | None = Field(default=None, max_length=200)
    json_expected: str | None = Field(default=None, max_length=200)
    method: Literal["GET", "HEAD", "POST"] = "GET"
    expected_status: str = Field(default="200-399", pattern=r"^\d{3}(-\d{3})?(,\s*\d{3}(-\d{3})?)*$")
    interval_sec: int = Field(default=60, ge=20, le=86400)
    timeout_sec: int = Field(default=10, ge=1, le=60)
    retries: int = Field(default=0, ge=0, le=5)
    latency_threshold_ms: int | None = Field(default=None, ge=1)
    ssl_expiry_warn_days: int = Field(default=14, ge=1, le=365)
    sla_target: float = Field(default=99.9, ge=50, le=100)
    anomaly_detection: bool = True
    notify_repeat_min: int | None = Field(default=None, ge=5, le=1440)
    parent_id: int | None = None
    project_id: int | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def check_type_fields(self):
        if self.type in ("http", "keyword", "json"):
            _require_http_url(self.target)
        elif self.type in ("tcp", "ping", "dns"):
            _require_host(self.target)
        elif self.type == "ssl":
            _require_host(urlparse(self.target).hostname or self.target)
        if self.type == "keyword" and not self.keyword:
            raise ValueError("keyword is required for keyword monitors")
        if self.type == "json" and not self.json_path:
            raise ValueError("json_path is required for json monitors")
        if self.type == "tcp" and not self.port:
            raise ValueError("port is required for tcp monitors")
        return self


class MonitorCreate(MonitorBase):
    pass


class MonitorOut(MonitorBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    active: bool
    status: str
    push_token: str | None
    last_checked: Utc | None
    last_push_at: Utc | None
    last_latency_ms: float | None
    last_message: str | None
    ssl_expires_at: Utc | None
    created_at: Utc
    uptime_24h: float | None = None
    status_since: Utc | None = None
    recent: list[str] = Field(default_factory=list)
    recent_latency: list[float | None] = Field(default_factory=list)


class HeartbeatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    latency_ms: float | None
    message: str | None
    created_at: Utc


class StatsOut(BaseModel):
    uptime_24h: float | None
    uptime_7d: float | None
    uptime_30d: float | None
    avg_latency_ms: float | None
    p95_latency_ms: float | None
    incidents_30d: int
    mttr_min: float | None
    sla_target: float
    error_budget_min: float
    downtime_30d_min: float
    budget_remaining_pct: float


class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    monitor_id: int
    monitor_name: str | None = None
    cause: str | None
    started_at: Utc
    resolved_at: Utc | None
    acknowledged_at: Utc | None
    acknowledged_by: str | None
    note: str | None


class AckIn(BaseModel):
    by: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=1000)


class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: ChannelType
    url: str = Field(default="", max_length=500)
    recipients: list[Annotated[str, Field(pattern=EMAIL_PATTERN, max_length=200)]] = Field(default_factory=list, max_length=50)
    tags: list[str] = Field(default_factory=list, max_length=20)
    project_ids: list[int] = Field(default_factory=list)
    active: bool = True

    @model_validator(mode="after")
    def check_target(self):
        if self.type == "email":
            if not self.recipients:
                raise ValueError("add at least one email address")
            self.url = ""
        elif self.type == "telegram":
            if not TELEGRAM_URL.match(self.url):
                raise ValueError("Telegram bot token or chat ID is not valid")
            self.recipients = []
        else:
            _require_http_url(self.url)
            self.recipients = []
        return self


class ChannelOut(ChannelIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class MaintenanceIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    monitor_ids: list[int] = Field(default_factory=list)
    starts_at: datetime
    ends_at: datetime

    @field_validator("starts_at", "ends_at")
    @classmethod
    def to_utc(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def check_range(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be after starts_at")
        return self


class MaintenanceOut(MaintenanceIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class StatusPageIn(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9-]{2,60}$")
    title: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    monitor_ids: list[int] = Field(default_factory=list)


class StatusPageOut(StatusPageIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class PublicMonitor(BaseModel):
    id: int
    name: str
    status: str
    uptime_30d: float | None
    bars: list[str]


class PublicIncident(BaseModel):
    monitor_name: str
    started_at: Utc
    resolved_at: Utc | None


class PublicStatusOut(BaseModel):
    title: str
    description: str | None
    overall: str
    monitors: list[PublicMonitor]
    incidents: list[PublicIncident] = Field(default_factory=list)


class SummaryOut(BaseModel):
    total: int
    up: int
    down: int
    degraded: int
    paused: int
    maintenance: int
    open_incidents: int
    unacknowledged: int
    ssl_expiring_soon: int
    avg_uptime_24h: float | None
    avg_latency_ms: float | None


class MonitorExport(MonitorBase):
    id: int


class DashboardPoint(BaseModel):
    at: Utc
    avg_latency_ms: float | None
    uptime: float | None
    checks: int


class DashboardOut(BaseModel):
    series: list[DashboardPoint]
    incidents: list[IncidentOut]
    uptime: float | None = None
    avg_latency_ms: float | None = None
    checks: int = 0
    incident_count: int = 0


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    member_ids: list[int] = Field(default_factory=list)
    email_members: bool = True


class ProjectOut(ProjectIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    monitor_count: int = 0
    down_count: int = 0


class ProjectExport(ProjectIn):
    id: int


Role = Literal["superadmin", "admin", "member"]


class UserBase(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9._@-]{2,60}$")
    name: str = Field(default="", max_length=120)
    email: str | None = Field(default=None, pattern=EMAIL_PATTERN, max_length=200)
    role: Role = "member"
    active: bool = True

    @field_validator("email", mode="before")
    @classmethod
    def blank_email(cls, value):
        return value or None


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=200)


class UserUpdate(UserBase):
    password: str | None = Field(default=None, min_length=8, max_length=200)


class UserOut(UserBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    must_change_password: bool
    created_at: Utc


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=60)
    password: str = Field(min_length=1, max_length=200)


class PasswordIn(BaseModel):
    current: str = Field(min_length=1, max_length=200)
    new: str = Field(min_length=8, max_length=200)


class ForgotIn(BaseModel):
    username: str = Field(min_length=1, max_length=200)


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    new: str = Field(min_length=8, max_length=200)


class MeOut(BaseModel):
    id: int
    username: str
    name: str = ""
    email: str | None = None
    role: Role
    must_change_password: bool
    refresh_sec: int
    tenant: str = "admin"
    tenant_name: str = ""
    active_tenant: str = "admin"
    active_tenant_name: str = ""


class LoginOut(MeOut):
    token: str


class TenantCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,29}$")
    name: str = Field(min_length=1, max_length=120)
    admin_username: str = Field(pattern=r"^[A-Za-z0-9._@-]{2,60}$")
    admin_email: str | None = Field(default=None, pattern=EMAIL_PATTERN, max_length=200)
    admin_password: str = Field(min_length=8, max_length=200)

    @field_validator("admin_email", mode="before")
    @classmethod
    def blank_email(cls, value):
        return value or None


class TenantUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    active: bool = True


class TenantOut(BaseModel):
    slug: str
    name: str
    active: bool
    is_default: bool
    created_at: Utc
    admins: list[str]
    users: int
    teams: int
    monitors: int
    down: int
    db_file: str
    db_size_kb: int


class GeneralSettings(BaseModel):
    app_url: str = Field(default="", max_length=300)
    timezone: str = Field(default="", max_length=60)  # empty = TZ env or UTC
    refresh_sec: int = Field(default=5, ge=5, le=3600)
    retention_value: int = Field(default=1, ge=1, le=3650)
    retention_unit: Literal["days", "months", "years"] = "years"

    @model_validator(mode="after")
    def check_retention(self):
        days = self.retention_value * {"days": 1, "months": 30, "years": 365}[self.retention_unit]
        if days > 3650:
            raise ValueError("Log retention can be at most 10 years")
        return self

    @field_validator("app_url")
    @classmethod
    def check_url(cls, value: str) -> str:
        return _require_http_url(value).rstrip("/") if value else ""

    @field_validator("timezone")
    @classmethod
    def check_timezone(cls, value: str) -> str:
        if value:
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError(f"Unknown time zone: {value}") from None
        return value


class SmtpSettings(BaseModel):
    host: str = Field(default="", max_length=200)
    port: int = Field(default=587, ge=1, le=65535)
    security: Literal["starttls", "ssl", "none"] = "starttls"
    username: str = Field(default="", max_length=200)
    password: str | None = Field(default=None, max_length=500)  # None keeps the saved password
    from_addr: str = Field(default="", max_length=200)

    @field_validator("host")
    @classmethod
    def check_host(cls, value: str) -> str:
        return _require_host(value) if value else ""

    @field_validator("from_addr")
    @classmethod
    def check_from(cls, value: str) -> str:
        if value and not re.fullmatch(EMAIL_PATTERN, value):
            raise ValueError("must be an email address")
        return value


class SmtpOut(BaseModel):
    host: str
    port: int
    security: str
    username: str
    from_addr: str
    has_password: bool


class SmtpTestIn(BaseModel):
    to: str = Field(pattern=EMAIL_PATTERN, max_length=200)


class ExportData(BaseModel):
    app: str = "myappsuptime"
    version: int = 2
    monitors: list[MonitorExport]
    channels: list[ChannelIn]
    projects: list[ProjectExport] = Field(default_factory=list)
