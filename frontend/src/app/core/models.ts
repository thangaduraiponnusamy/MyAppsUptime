export type MonitorStatus = 'up' | 'down' | 'degraded' | 'maintenance' | 'paused' | 'pending';
export type MonitorType = 'http' | 'keyword' | 'json' | 'tcp' | 'ping' | 'dns' | 'ssl' | 'push';
export type ChannelType = 'webhook' | 'slack' | 'teams' | 'email';

export const MONITOR_TYPES: { value: MonitorType; label: string }[] = [
  { value: 'http', label: 'HTTP(s)' },
  { value: 'keyword', label: 'HTTP(s) - Keyword' },
  { value: 'json', label: 'HTTP(s) - JSON assertion' },
  { value: 'tcp', label: 'TCP Port' },
  { value: 'ping', label: 'Ping (ICMP)' },
  { value: 'dns', label: 'DNS' },
  { value: 'ssl', label: 'SSL Certificate' },
  { value: 'push', label: 'Push / Cron heartbeat' },
];

export interface MonitorInput {
  name: string;
  type: MonitorType;
  target: string;
  port: number | null;
  keyword: string | null;
  json_path: string | null;
  json_expected: string | null;
  method: 'GET' | 'HEAD' | 'POST';
  expected_status: string;
  interval_sec: number;
  timeout_sec: number;
  retries: number;
  latency_threshold_ms: number | null;
  ssl_expiry_warn_days: number;
  sla_target: number;
  anomaly_detection: boolean;
  notify_repeat_min: number | null;
  parent_id: number | null;
  project_id: number | null;
  tags: string[];
}

export interface Monitor extends MonitorInput {
  id: number;
  active: boolean;
  status: MonitorStatus;
  push_token: string | null;
  last_checked: string | null;
  last_push_at: string | null;
  last_latency_ms: number | null;
  last_message: string | null;
  ssl_expires_at: string | null;
  created_at: string;
  uptime_24h: number | null;
  status_since: string | null;
  recent: MonitorStatus[];
  recent_latency: (number | null)[];
}

export interface Heartbeat {
  status: MonitorStatus;
  latency_ms: number | null;
  message: string | null;
  created_at: string;
}

export interface Stats {
  uptime_24h: number | null;
  uptime_7d: number | null;
  uptime_30d: number | null;
  avg_latency_ms: number | null;
  p95_latency_ms: number | null;
  incidents_30d: number;
  mttr_min: number | null;
  sla_target: number;
  error_budget_min: number;
  downtime_30d_min: number;
  budget_remaining_pct: number;
}

export interface Incident {
  id: number;
  monitor_id: number;
  monitor_name: string | null;
  cause: string | null;
  started_at: string;
  resolved_at: string | null;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
  note: string | null;
}

export interface Channel {
  id?: number;
  name: string;
  type: ChannelType;
  url: string;
  recipients: string[];
  tags: string[];
  project_ids: number[];
  active: boolean;
}

export type Role = 'superadmin' | 'admin' | 'member';

export interface Me {
  id: number;
  username: string;
  name: string;
  email: string | null;
  role: Role;
  must_change_password: boolean;
  refresh_sec: number;
  tenant: string;
  tenant_name: string;
  active_tenant: string;
  active_tenant_name: string;
}

export interface Tenant {
  slug: string;
  name: string;
  active: boolean;
  is_default: boolean;
  created_at: string;
  admins: string[];
  users: number;
  teams: number;
  monitors: number;
  down: number;
  db_file: string;
  db_size_kb: number;
}

export interface TenantCreate {
  slug: string;
  name: string;
  admin_username: string;
  admin_email: string | null;
  admin_password: string;
}

export interface User {
  id?: number;
  username: string;
  name: string;
  email: string | null;
  role: Role;
  active: boolean;
  password?: string | null;
  must_change_password?: boolean;
  created_at?: string;
}

export interface Project {
  id?: number;
  name: string;
  description: string | null;
  member_ids: number[];
  email_members: boolean;
  monitor_count?: number;
  down_count?: number;
}

export interface GeneralSettings {
  app_url: string;
  timezone: string;
  refresh_sec: number;
  retention_value: number;
  retention_unit: 'days' | 'months' | 'years';
}

export interface SmtpSettings {
  host: string;
  port: number;
  security: 'starttls' | 'ssl' | 'none';
  username: string;
  password?: string | null;
  from_addr: string;
  has_password?: boolean;
}

export interface Maintenance {
  id?: number;
  title: string;
  monitor_ids: number[];
  starts_at: string;
  ends_at: string;
}

export interface StatusPage {
  id?: number;
  slug: string;
  title: string;
  description: string | null;
  monitor_ids: number[];
}

export interface PublicStatus {
  title: string;
  description: string | null;
  overall: MonitorStatus;
  monitors: { id: number; name: string; status: MonitorStatus; uptime_30d: number | null; bars: MonitorStatus[] }[];
  incidents: { monitor_name: string; started_at: string; resolved_at: string | null }[];
}

export interface Summary {
  total: number;
  up: number;
  down: number;
  degraded: number;
  paused: number;
  maintenance: number;
  open_incidents: number;
  unacknowledged: number;
  ssl_expiring_soon: number;
  avg_uptime_24h: number | null;
  avg_latency_ms: number | null;
}

export interface DashboardData {
  series: { at: string; avg_latency_ms: number | null; uptime: number | null; checks: number }[];
  incidents: Incident[];
  uptime: number | null;
  avg_latency_ms: number | null;
  checks: number;
  incident_count: number;
}

export interface ExportData {
  app: string;
  version: number;
  monitors: (MonitorInput & { id: number })[];
  channels: Channel[];
  projects?: (Project & { id: number })[];
}
