import ipaddress
import json
import re
from datetime import datetime, timezone
from typing import Annotated, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

MonitorType = Literal["http", "keyword", "json", "security", "tcp", "ping", "dns", "ssl", "push", "docker"]
Grade = Literal["A", "B", "C", "D"]
Timings = dict[str, float]
ChannelType = Literal["webhook", "slack", "teams", "telegram", "discord", "googlechat", "ntfy", "email", "push"]
TELEGRAM_URL = re.compile(r"^https://api\.telegram\.org/bot\d+:[A-Za-z0-9_-]{30,}/sendMessage\?chat_id=(-?\d+|@[A-Za-z0-9_]{5,32})$")
CHANNEL_URLS = {
    "discord": (re.compile(r"^https://(discord|discordapp)\.com/api/webhooks/\d+/[A-Za-z0-9_-]+(\?[^\s]*)?$"),
                "Discord webhook URL is not valid"),
    "googlechat": (re.compile(r"^https://chat\.googleapis\.com/v1/spaces/[A-Za-z0-9_-]+/messages\?\S+$"),
                   "Google Chat webhook URL is not valid"),
    "ntfy": (re.compile(r"^https?://[^\s/?#]+(/[^\s/?#]+)*/[A-Za-z0-9_-]{1,64}/?$"),
             "ntfy URL must be the server address plus topic, for example https://ntfy.sh/my-alerts"),
}
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
DOMAIN_PATTERN = re.compile(r"^(?=.{4,200}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
LOGO_PATTERN = re.compile(r"^data:image/(png|jpeg|gif|webp|svg\+xml);base64,[A-Za-z0-9+/=]+$")

# SQLite returns naive datetimes; mark them UTC so clients convert correctly
Utc = Annotated[datetime, AfterValidator(lambda d: d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d)]

HOST_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9.\-:]*$"
CONTAINER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"


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
    down_after: int | None = Field(default=None, ge=1, le=20)
    latency_threshold_ms: int | None = Field(default=None, ge=1)
    ssl_expiry_warn_days: int = Field(default=14, ge=1, le=365)
    sla_target: float = Field(default=99.9, ge=50, le=100)
    anomaly_detection: bool = True
    notify_repeat_min: int | None = Field(default=None, ge=5, le=1440)
    parent_id: int | None = None
    project_id: int | None = None
    probe_id: int | None = None
    min_grade: Grade = "B"
    tags: list[str] = Field(default_factory=list, max_length=20)
    badge_enabled: bool | None = None  # empty = leave as it is

    @model_validator(mode="after")
    def check_type_fields(self):
        if self.type in ("http", "keyword", "json", "security"):
            _require_http_url(self.target)
        elif self.type in ("tcp", "ping", "dns"):
            _require_host(self.target)
        elif self.type == "ssl":
            _require_host(urlparse(self.target).hostname or self.target)
        elif self.type == "docker" and not re.fullmatch(CONTAINER_PATTERN, self.target):
            raise ValueError("target must be a container name or ID")
        if self.type == "push" and self.probe_id is not None:
            raise ValueError("push monitors cannot run from a private location")
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
    domain_expires_at: Utc | None = None
    fail_count: int = 0
    probe_name: str | None = None
    created_at: Utc
    uptime_24h: float | None = None
    status_since: Utc | None = None
    recent: list[str] = Field(default_factory=list)
    recent_latency: list[float | None] = Field(default_factory=list)
    last_timings: Timings | None = None
    security_findings: dict | None = None
    badge_enabled: bool = False
    badge_token: str | None = None
    snoozed_until: Utc | None = None


class SnoozeIn(BaseModel):
    minutes: int = Field(ge=0, le=7 * 24 * 60)  # 0 = unsnooze


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=10, max_length=200, pattern=r"^[A-Za-z0-9_=-]+$")
    auth: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_=-]+$")


class PushSubscribeIn(BaseModel):
    endpoint: str = Field(min_length=10, max_length=1000)
    keys: PushKeys | None = None
    device: str = Field(default="", max_length=80)

    @field_validator("endpoint")
    @classmethod
    def https_only(cls, value: str) -> str:
        url = urlparse(value)
        if url.scheme != "https" or not url.hostname:
            raise ValueError("push endpoint must be an https URL")
        return value


class HeartbeatOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    status: str
    latency_ms: float | None
    message: str | None
    timings: Timings | None = None
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


class IncidentAckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    by: str
    note: str | None
    created_at: Utc


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
    public_note: str | None = None
    acks: list[IncidentAckOut] = []


class AlertEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    monitor_id: int | None
    name: str
    status: str
    message: str
    sent_to: list[str] | None
    errors: str
    created_at: Utc


class AckIn(BaseModel):
    by: str | None = Field(default=None, max_length=120)  # ignored: the signed-in user is recorded
    note: str | None = Field(default=None, max_length=1000)
    public_note: str | None = Field(default=None, max_length=1000)


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
        elif self.type == "push":
            self.url, self.recipients = "", []
        elif self.type == "telegram":
            if not TELEGRAM_URL.match(self.url):
                raise ValueError("Telegram bot token or chat ID is not valid")
            self.recipients = []
        elif self.type in CHANNEL_URLS:
            pattern, message = CHANNEL_URLS[self.type]
            if not pattern.match(self.url):
                raise ValueError(message)
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
    domain: str | None = Field(default=None, max_length=200)
    logo: str | None = Field(default=None, max_length=300_000)

    @field_validator("logo")
    @classmethod
    def check_logo(cls, value: str | None) -> str | None:
        if value and not LOGO_PATTERN.match(value):
            raise ValueError("logo must be a PNG, JPEG, GIF, WebP or SVG image")
        return value or None

    @field_validator("domain")
    @classmethod
    def check_domain(cls, value: str | None) -> str | None:
        value = (value or "").strip().lower().rstrip(".")
        if not value:
            return None
        if not DOMAIN_PATTERN.match(value):
            raise ValueError("domain must be a host name like status.example.com")
        return value


class StatusPageOut(StatusPageIn):
    model_config = ConfigDict(from_attributes=True)
    id: int


class PublicMonitor(BaseModel):
    id: int
    name: str
    status: str
    uptime_30d: float | None
    bars: list[str]
    times: list[Utc] = Field(default_factory=list)


class PublicIncident(BaseModel):
    monitor_name: str
    started_at: Utc
    resolved_at: Utc | None
    note: str | None = None


class PublicStatusOut(BaseModel):
    title: str
    description: str | None
    logo: str | None = None
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
    channel_ids: list[int] | None = None


class ProjectOut(ProjectIn):
    model_config = ConfigDict(from_attributes=True)
    id: int
    monitor_count: int = 0
    down_count: int = 0


class ProjectExport(ProjectIn):
    id: int


Role = Literal["superadmin", "admin", "member"]
ALERT_EMAIL = -1  # "Email" in a user's alert channels; other values are channel ids


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
    project_ids: list[int] | None = None  # None = leave as is
    monitor_ids: list[int] | None = None  # extra services outside those projects
    alert_channels: list[int] | None = None


class UserUpdate(UserBase):
    password: str | None = Field(default=None, min_length=8, max_length=200)
    project_ids: list[int] | None = None
    monitor_ids: list[int] | None = None
    alert_channels: list[int] | None = None


class UserOut(UserBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    must_change_password: bool
    last_login_at: Utc | None = None
    created_at: Utc
    project_ids: list[int] = []
    monitor_ids: list[int] = []
    alert_channels: list[int] | None = None  # None = all


class AlertChannelsIn(BaseModel):
    alert_channels: list[int]


class AlertOption(BaseModel):
    id: int
    name: str


class AuditEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    action: str
    kind: str
    target: str
    detail: str
    ip: str
    tenant: str = ""
    user_agent: str = ""
    object_id: str = ""
    changes: str = ""
    created_at: Utc


class SessionOut(BaseModel):
    id: str
    device: str
    ip: str
    created_at: Utc | None
    last_used_at: Utc | None
    current: bool


class LoginEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    ip: str
    device: str = ""
    success: bool
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
    alert_channels: list[int] | None = None  # None = all
    alert_options: list[AlertOption] = []


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
    down_after_checks: int = Field(default=3, ge=1, le=20)
    expiry_warn_days: list[Annotated[int, Field(ge=1, le=365)]] = Field(default_factory=lambda: [30, 14, 7], max_length=5)
    domain_expiry: bool = True
    audit_keep_days: int = Field(default=365, ge=30, le=3650)

    @field_validator("expiry_warn_days")
    @classmethod
    def sort_days(cls, value: list[int]) -> list[int]:
        return sorted(set(value), reverse=True)

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


class ServerSettings(BaseModel):
    backup_enabled: bool = True
    backup_keep_days: int = Field(default=7, ge=1, le=90)
    dns_cache: bool = True
    allow_private_targets: bool = False
    allowed_cidrs: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("allowed_cidrs")
    @classmethod
    def check_cidrs(cls, value: list[str]) -> list[str]:
        out = []
        for item in (v.strip() for v in value):
            if item:
                try:
                    out.append(str(ipaddress.ip_network(item, strict=False)))
                except ValueError:
                    raise ValueError(f"Not a network: {item}") from None
        return out


class BackupInfo(BaseModel):
    name: str
    size_kb: int


class ServerOut(ServerSettings):
    backup_dir: str
    backups: list[BackupInfo]


class DatabaseInfo(BaseModel):
    size_kb: int
    heartbeats: int
    incidents: int


class ExportData(BaseModel):
    app: str = "myappsuptime"
    version: int = 2
    monitors: list[MonitorExport]
    channels: list[ChannelIn]
    projects: list[ProjectExport] = Field(default_factory=list)


class ProbeIn(BaseModel):
    name: str = Field(min_length=1, max_length=60, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _.-]*$")


class ProbeOut(BaseModel):
    id: int
    name: str
    online: bool
    version: str | None
    last_ip: str | None = None
    last_seen_at: Utc | None
    created_at: Utc
    monitors: int


class ProbeCreated(ProbeOut):
    token: str  # shown only once


class ProbeResult(BaseModel):
    monitor_id: int
    status: Literal["up", "down", "degraded"]
    latency_ms: float | None = Field(default=None, ge=0)
    message: str = Field(default="", max_length=300)
    ssl_expires_at: Utc | None = None
    timings: Timings | None = Field(default=None, max_length=10)
    findings: dict | None = None

    @field_validator("findings")
    @classmethod
    def small_findings(cls, value: dict | None) -> dict | None:
        if value is not None and len(json.dumps(value)) > 5000:
            raise ValueError("findings too large")
        return value


class ProbeSyncIn(BaseModel):
    version: str = Field(default="", max_length=20)
    results: list[ProbeResult] = Field(default_factory=list, max_length=500)
