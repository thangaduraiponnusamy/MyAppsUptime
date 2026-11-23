import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import {
  AdminDefaults, AlertEvent, AuditEvent, AuditFilter, Channel, DashboardData, DatabaseInfo, ExportData, FailedLogin, GeneralSettings, Heartbeat, Incident, LockedAccount, LoginEvent, Maintenance, Monitor, MonitorInput,
  Probe, Project, PublicStatus, ServerSettings, SessionInfo, SmtpSettings, Stats, StatusPage, Summary, Tenant, TenantCreate, User,
} from './models';

type Range = { hours?: number; start?: string; end?: string; project_id?: number[] };

@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);
  private base = '/api';

  summary() { return this.http.get<Summary>(`${this.base}/summary`); }
  dashboard(range: Range) {
    const params = Object.fromEntries(Object.entries(range).filter(([, v]) => v !== undefined)) as Record<string, string | number | number[]>;
    return this.http.get<DashboardData>(`${this.base}/dashboard`, { params });
  }

  monitors() { return this.http.get<Monitor[]>(`${this.base}/monitors`); }
  monitor(id: number) { return this.http.get<Monitor>(`${this.base}/monitors/${id}`); }
  createMonitor(body: MonitorInput) { return this.http.post<Monitor>(`${this.base}/monitors`, body); }
  updateMonitor(id: number, body: MonitorInput) { return this.http.put<Monitor>(`${this.base}/monitors/${id}`, body); }
  deleteMonitor(id: number) { return this.http.delete<void>(`${this.base}/monitors/${id}`); }
  pause(id: number) { return this.http.post<Monitor>(`${this.base}/monitors/${id}/pause`, {}); }
  resume(id: number) { return this.http.post<Monitor>(`${this.base}/monitors/${id}/resume`, {}); }
  checkNow(id: number) { return this.http.post<{ queued: boolean }>(`${this.base}/monitors/${id}/check`, {}); }
  snooze(id: number, minutes: number) { return this.http.post<Monitor>(`${this.base}/monitors/${id}/snooze`, { minutes }); }
  heartbeats(id: number, range: Range) {
    const params = Object.fromEntries(Object.entries(range).filter(([, v]) => v !== undefined)) as Record<string, string | number>;
    return this.http.get<Heartbeat[]>(`${this.base}/monitors/${id}/heartbeats`, { params });
  }
  stats(id: number) { return this.http.get<Stats>(`${this.base}/monitors/${id}/stats`); }

  incidents(openOnly = false, limit: number | null = 100, range: Range = {}) {
    let params = new HttpParams().set('open_only', openOnly);
    if (limit) params = params.set('limit', limit);
    for (const [k, v] of Object.entries(range)) {
      if (Array.isArray(v)) v.forEach((x) => (params = params.append(k, x)));
      else if (v !== undefined) params = params.set(k, v);
    }
    return this.http.get<Incident[]>(`${this.base}/incidents`, { params });
  }
  acknowledge(id: number, note: string | null, publicNote: string | null) {
    return this.http.post<Incident>(`${this.base}/incidents/${id}/ack`, { note, public_note: publicNote });
  }
  alerts(range: Range) {
    const params = Object.fromEntries(Object.entries(range).filter(([, v]) => v !== undefined)) as Record<string, string | number>;
    return this.http.get<AlertEvent[]>(`${this.base}/alerts`, { params });
  }

  channels() { return this.http.get<Channel[]>(`${this.base}/notifications`); }
  createChannel(body: Channel) { return this.http.post<Channel>(`${this.base}/notifications`, body); }
  updateChannel(id: number, body: Channel) { return this.http.put<Channel>(`${this.base}/notifications/${id}`, body); }
  deleteChannel(id: number) { return this.http.delete<void>(`${this.base}/notifications/${id}`); }
  testChannel(id: number) { return this.http.post<{ sent: boolean }>(`${this.base}/notifications/${id}/test`, {}); }
  pushKey() { return this.http.get<{ public_key: string }>(`${this.base}/push/key`); }
  pushSubscribe(body: PushSubscriptionJSON) { return this.http.post<void>(`${this.base}/push/subscribe`, body); }
  pushUnsubscribe(endpoint: string) { return this.http.delete<void>(`${this.base}/push/subscribe`, { body: { endpoint } }); }
  pushTest() { return this.http.post<{ sent: number; errors: string[] }>(`${this.base}/push/test`, {}); }

  projects() { return this.http.get<Project[]>(`${this.base}/projects`); }
  createProject(body: Project) { return this.http.post<Project>(`${this.base}/projects`, body); }
  updateProject(id: number, body: Project) { return this.http.put<Project>(`${this.base}/projects/${id}`, body); }
  deleteProject(id: number) { return this.http.delete<void>(`${this.base}/projects/${id}`); }

  probes() { return this.http.get<Probe[]>(`${this.base}/probes`); }
  createProbe(name: string) { return this.http.post<Probe>(`${this.base}/probes`, { name }); }
  renameProbe(id: number, name: string) { return this.http.put<Probe>(`${this.base}/probes/${id}`, { name }); }
  probeToken(id: number) { return this.http.post<Probe>(`${this.base}/probes/${id}/token`, {}); }
  deleteProbe(id: number) { return this.http.delete<void>(`${this.base}/probes/${id}`); }
  bulk(ids: number[], action: 'pause' | 'resume' | 'delete' | 'project', projectId: number | null = null) {
    return this.http.post<{ updated: number }>(`${this.base}/monitors/bulk`, { ids, action, project_id: projectId });
  }

  users() { return this.http.get<User[]>(`${this.base}/users`); }
  createUser(body: User) { return this.http.post<User>(`${this.base}/users`, body); }
  updateUser(id: number, body: User) { return this.http.put<User>(`${this.base}/users/${id}`, body); }
  deleteUser(id: number) { return this.http.delete<void>(`${this.base}/users/${id}`); }
  logins(limit: number) { return this.http.get<LoginEvent[]>(`${this.base}/logins`, { params: { limit } }); }
  audit(filter: AuditFilter, limit: number, offset: number) {
    return this.http.get<AuditEvent[]>(`${this.base}/audit`, { params: { ...cleanFilter(filter), limit, offset } });
  }
  auditCsv(filter: AuditFilter) {
    return this.http.get(`${this.base}/audit/export`, { params: cleanFilter(filter), responseType: 'blob' });
  }
  sessions() { return this.http.get<SessionInfo[]>(`${this.base}/auth/sessions`); }
  revokeSession(id: string) { return this.http.delete<void>(`${this.base}/auth/sessions/${id}`); }
  revokeOtherSessions() { return this.http.post<{ revoked: number }>(`${this.base}/auth/sessions/others`, {}); }
  failedLogins(range: Range) {
    const params = Object.fromEntries(Object.entries(range).filter(([, v]) => v !== undefined)) as Record<string, string | number>;
    return this.http.get<{ events: FailedLogin[]; locked: LockedAccount[] }>(`${this.base}/security/failed-logins`, { params });
  }
  unlockAccount(tenant: string, username: string) {
    return this.http.post<void>(`${this.base}/security/accounts/unlock`, { tenant, username });
  }

  tenants() { return this.http.get<Tenant[]>(`${this.base}/tenants`); }
  createTenant(body: TenantCreate) { return this.http.post<Tenant>(`${this.base}/tenants`, body); }
  updateTenant(slug: string, body: { name: string; active: boolean }) {
    return this.http.put<Tenant>(`${this.base}/tenants/${slug}`, body);
  }
  resetTenantAdmin(slug: string, password: string) {
    return this.http.post<{ username: string }>(`${this.base}/tenants/${slug}/reset-admin`, { password });
  }
  deleteTenant(slug: string) { return this.http.delete<void>(`${this.base}/tenants/${slug}`); }

  general() { return this.http.get<GeneralSettings>(`${this.base}/settings/general`); }
  saveGeneral(body: GeneralSettings) { return this.http.put<GeneralSettings>(`${this.base}/settings/general`, body); }
  adminDefaults() { return this.http.get<AdminDefaults>(`${this.base}/settings/defaults`); }
  smtp() { return this.http.get<SmtpSettings>(`${this.base}/settings/smtp`); }
  saveSmtp(body: SmtpSettings) { return this.http.put<SmtpSettings>(`${this.base}/settings/smtp`, body); }
  testSmtp(to: string) { return this.http.post<{ sent: boolean }>(`${this.base}/settings/smtp/test`, { to }); }
  database() { return this.http.get<DatabaseInfo>(`${this.base}/settings/database`); }
  shrinkDatabase() { return this.http.post<DatabaseInfo>(`${this.base}/settings/database/shrink`, {}); }
  clearStats() { return this.http.post<DatabaseInfo>(`${this.base}/settings/database/clear-stats`, {}); }
  server() { return this.http.get<ServerSettings>(`${this.base}/settings/server`); }
  saveServer(body: ServerSettings) { return this.http.put<ServerSettings>(`${this.base}/settings/server`, body); }
  backupNow() { return this.http.post<ServerSettings>(`${this.base}/settings/server/backup`, {}); }

  maintenance() { return this.http.get<Maintenance[]>(`${this.base}/maintenance`); }
  createMaintenance(body: Maintenance) { return this.http.post<Maintenance>(`${this.base}/maintenance`, body); }
  deleteMaintenance(id: number) { return this.http.delete<void>(`${this.base}/maintenance/${id}`); }

  statusPages() { return this.http.get<StatusPage[]>(`${this.base}/status-pages`); }
  createStatusPage(body: StatusPage) { return this.http.post<StatusPage>(`${this.base}/status-pages`, body); }
  updateStatusPage(id: number, body: StatusPage) { return this.http.put<StatusPage>(`${this.base}/status-pages/${id}`, body); }
  deleteStatusPage(id: number) { return this.http.delete<void>(`${this.base}/status-pages/${id}`); }

  exportConfig() { return this.http.get<ExportData>(`${this.base}/export`); }
  importConfig(body: ExportData) {
    return this.http.post<{ monitors: number; channels: number; projects: number }>(`${this.base}/import`, body);
  }

  publicStatus(slug: string) { return this.http.get<PublicStatus>(`${this.base}/public/status/${encodeURIComponent(slug)}`); }
}

function cleanFilter(filter: AuditFilter): Record<string, string> {
  return Object.fromEntries(Object.entries(filter).filter(([, v]) => v && String(v).trim())) as Record<string, string>;
}

export function errorText(err: { error?: { detail?: unknown }; message?: string }): string {
  const detail = err.error?.detail;
  if (Array.isArray(detail)) {
    return detail.map((d: { msg?: string; loc?: unknown[] }) => {
      const msg = (d.msg ?? 'Invalid value').replace(/^Value error, /, '');
      const field = d.loc?.length && d.loc[0] === 'body' && d.loc.length > 1 ? String(d.loc[d.loc.length - 1]).replace(/_/g, ' ') : '';
      return field ? `${field[0].toUpperCase()}${field.slice(1)}: ${msg}` : msg;
    }).join('. ');
  }
  return typeof detail === 'string' ? detail : err.message ?? 'Request failed';
}
