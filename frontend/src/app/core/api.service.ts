import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import {
  Channel, DashboardData, ExportData, Heartbeat, Incident, Maintenance, Monitor, MonitorInput, PublicStatus, Stats, StatusPage,
  Summary,
} from './models';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);
  private base = '/api';

  summary() { return this.http.get<Summary>(`${this.base}/summary`); }
  dashboard(range: { hours?: number; start?: string; end?: string }) {
    const params = Object.fromEntries(Object.entries(range).filter(([, v]) => v !== undefined)) as Record<string, string | number>;
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
  heartbeats(id: number, hours: number) {
    return this.http.get<Heartbeat[]>(`${this.base}/monitors/${id}/heartbeats`, { params: { hours } });
  }
  stats(id: number) { return this.http.get<Stats>(`${this.base}/monitors/${id}/stats`); }

  incidents(openOnly = false, limit = 100) {
    return this.http.get<Incident[]>(`${this.base}/incidents`, {
      params: new HttpParams().set('open_only', openOnly).set('limit', limit),
    });
  }
  acknowledge(id: number, by: string, note: string | null) {
    return this.http.post<Incident>(`${this.base}/incidents/${id}/ack`, { by, note });
  }

  channels() { return this.http.get<Channel[]>(`${this.base}/notifications`); }
  createChannel(body: Channel) { return this.http.post<Channel>(`${this.base}/notifications`, body); }
  deleteChannel(id: number) { return this.http.delete<void>(`${this.base}/notifications/${id}`); }
  testChannel(id: number) { return this.http.post<{ sent: boolean }>(`${this.base}/notifications/${id}/test`, {}); }

  maintenance() { return this.http.get<Maintenance[]>(`${this.base}/maintenance`); }
  createMaintenance(body: Maintenance) { return this.http.post<Maintenance>(`${this.base}/maintenance`, body); }
  deleteMaintenance(id: number) { return this.http.delete<void>(`${this.base}/maintenance/${id}`); }

  statusPages() { return this.http.get<StatusPage[]>(`${this.base}/status-pages`); }
  createStatusPage(body: StatusPage) { return this.http.post<StatusPage>(`${this.base}/status-pages`, body); }
  deleteStatusPage(id: number) { return this.http.delete<void>(`${this.base}/status-pages/${id}`); }

  exportConfig() { return this.http.get<ExportData>(`${this.base}/export`); }
  importConfig(body: ExportData) { return this.http.post<{ monitors: number; channels: number }>(`${this.base}/import`, body); }

  publicStatus(slug: string) { return this.http.get<PublicStatus>(`${this.base}/public/status/${encodeURIComponent(slug)}`); }
}

export function errorText(err: { error?: { detail?: unknown }; message?: string }): string {
  const detail = err.error?.detail;
  if (Array.isArray(detail)) return detail.map((d: { msg?: string }) => d.msg).join(', ');
  return typeof detail === 'string' ? detail : err.message ?? 'Request failed';
}
