import { DatePipe, DecimalPipe, formatDate } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { EMPTY, catchError, combineLatest, switchMap, timer } from 'rxjs';
import { ApiService } from '../core/api.service';
import { DashboardData, Monitor, MonitorStatus } from '../core/models';
import { MonitorStore } from '../core/monitor-store.service';
import { AreaChartComponent } from '../shared/area-chart.component';
import { DonutComponent, DonutSlice } from '../shared/donut.component';
import { HeartbeatBarComponent } from '../shared/heartbeat-bar.component';
import { IconComponent } from '../shared/icon.component';
import { SparklineComponent } from '../shared/sparkline.component';
import { daysUntil, duration } from '../shared/time';

type View = 'grid' | 'list';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [DatePipe, DecimalPipe, RouterLink, AreaChartComponent, DonutComponent, HeartbeatBarComponent,
            IconComponent, SparklineComponent],
  template: `
    <div class="page-head">
      <div>
        <h1>Dashboard</h1>
        <div class="sub">{{ greeting }} &middot; {{ today | date: 'EEEE, MMM d' }}</div>
      </div>
      <span class="spacer"></span>
      <div class="segmented">
        @for (r of ranges; track r.hours) {
          <button class="chip" [class.active]="hours() === r.hours" (click)="hours.set(r.hours)">{{ r.label }}</button>
        }
      </div>
    </div>

    @if (store.summary(); as s) {
      <div class="kpi-grid">
        <div class="kpi-card tone tone-emerald">
          <div class="kpi-head"><span class="ico"><app-icon name="activity" [size]="16" /></span>Fleet uptime<span class="delta">24h</span></div>
          <div class="kpi-value">{{ s.avg_uptime_24h === null ? '--' : (s.avg_uptime_24h | number: '1.2-2') }}<small>%</small></div>
          <div class="kpi-sub">{{ s.avg_uptime_24h === null ? 'Waiting for first checks' : s.avg_uptime_24h >= 99.9 ? 'Healthy, above 99.9% SLA' : 'Below 99.9% SLA target' }}</div>
          <app-sparkline class="kpi-spark" [values]="uptimeSeries()" />
        </div>
        <div class="kpi-card tone tone-indigo">
          <div class="kpi-head"><span class="ico"><app-icon name="bolt" [size]="16" /></span>Avg response
            @if (latencyTrend(); as t) {
              <span class="delta" [class.bad]="t > 0">{{ t > 0 ? '▲' : '▼' }} {{ t > 0 ? t : -t }}%</span>
            }
          </div>
          <div class="kpi-value">{{ s.avg_latency_ms === null ? '--' : (s.avg_latency_ms | number: '1.0-0') }}<small>ms</small></div>
          <div class="kpi-sub">Latest check across active monitors</div>
          <app-sparkline class="kpi-spark" [values]="latencySeries()" />
        </div>
        <div [class]="'kpi-card tone ' + (s.open_incidents > 0 ? 'tone-rose' : 'tone-amber')">
          <div class="kpi-head"><span class="ico">
            <app-icon [name]="s.open_incidents ? 'alert' : 'check'" [size]="16" /></span>Open incidents</div>
          <div class="kpi-value">{{ s.open_incidents }}</div>
          <div class="kpi-sub">
            @if (s.open_incidents) { <span class="tag-soft down">{{ s.unacknowledged }} not acknowledged</span> }
            @else { <span class="tag-soft up">All clear</span> }
            <a routerLink="/incidents" class="kpi-link">View <app-icon name="arrow" [size]="13" /></a>
          </div>
        </div>
        <div class="kpi-card tone tone-ocean">
          <div class="kpi-head"><span class="ico"><app-icon name="server" [size]="16" /></span>Monitors</div>
          <div class="kpi-value">{{ s.total }}<small>{{ s.paused ? s.paused + ' paused' : 'active' }}</small></div>
          <div class="stack">
            <i class="up" [style.flex]="s.up"></i><i class="degraded" [style.flex]="s.degraded"></i>
            <i class="down" [style.flex]="s.down"></i><i class="maintenance" [style.flex]="s.maintenance"></i>
            <i class="paused" [style.flex]="s.paused"></i>
          </div>
          <div class="kpi-sub mini-legend">
            <span><i class="up"></i>{{ s.up }} up</span><span><i class="degraded"></i>{{ s.degraded }}</span><span><i class="down"></i>{{ s.down }}</span>
          </div>
        </div>
      </div>

      <div class="dash-row">
        <section class="card panel">
          <div class="card-head">
            <span class="head-ico tone-indigo"><app-icon name="activity" [size]="16" /></span>
            <div class="grow"><h2>Response time</h2><div class="sub">Fleet average &middot; last {{ rangeLabel() }}</div></div>
            @if (latencyStats(); as ls) {
              <div class="mini-stats">
                <span><small>Min</small>{{ ls.min }} ms</span><span><small>Avg</small>{{ ls.avg }} ms</span><span><small>Max</small>{{ ls.max }} ms</span>
              </div>
            }
          </div>
          @if (latencyStats()) {
            <app-area-chart [values]="latencySeries()" [axis]="axis()" />
          } @else {
            <div class="empty-state tint-indigo">
              <span class="empty-ico"><app-icon name="activity" [size]="20" /></span>
              <div><b>Collecting data</b><div class="muted">The chart fills in after a few hours of checks.</div></div>
            </div>
          }
        </section>
        <section class="card panel">
          <div class="card-head">
            <span class="head-ico tone-ocean"><app-icon name="server" [size]="16" /></span>
            <div class="grow"><h2>Status</h2><div class="sub">{{ s.total }} monitors &middot; current state</div></div>
            @if (s.down + s.degraded === 0) { <span class="tag-soft up">All operational</span> }
            @else { <span class="tag-soft down">{{ s.down + s.degraded }} need attention</span> }
          </div>
          <app-donut [slices]="slices()" [centerValue]="s.total ? ((s.up / s.total) * 100 | number: '1.0-0') + '%' : '--'" caption="operational" />
        </section>
      </div>
    }

    <div class="dash-row even">
      <section class="card panel">
        <div class="card-head">
          <span class="head-ico tone-amber"><app-icon name="bolt" [size]="16" /></span>
          <div class="grow"><h2>Needs attention</h2><div class="sub">Down, degraded or SSL expiring soon</div></div>
          @if (attention().length) { <span class="count-badge amber">{{ attention().length }}</span> }
        </div>
        @for (m of attention(); track m.id) {
          <a class="list-item" [routerLink]="['/monitors', m.id]">
            <span class="avatar sm" [style.--h]="hue(m.name)">{{ m.name.charAt(0) }}</span>
            <div class="grow">
              <div class="title">{{ m.name }}</div>
              <div class="meta">{{ m.last_message || m.target }}</div>
            </div>
            <span [class]="'pill ' + m.status">{{ m.status }}</span>
          </a>
        } @empty {
          <div class="empty-state tint-up">
            <span class="empty-ico"><app-icon name="check" [size]="20" /></span>
            <div><b>All systems healthy</b><div class="muted">Nothing needs your attention right now.</div></div>
          </div>
        }
      </section>
      <section class="card panel">
        <div class="card-head">
          <span class="head-ico tone-rose"><app-icon name="alert" [size]="16" /></span>
          <div class="grow"><h2>Recent incidents</h2><div class="sub">Latest outages across all monitors</div></div>
          <a routerLink="/incidents" class="kpi-link">View all <app-icon name="arrow" [size]="14" /></a>
        </div>
        @for (i of data()?.incidents ?? []; track i.id) {
          <a class="list-item" [routerLink]="['/monitors', i.monitor_id]">
            <span class="rail" [class.open]="!i.resolved_at"></span>
            <div class="grow">
              <div class="title">{{ i.monitor_name }}</div>
              <div class="meta">{{ i.started_at | date: 'MMM d, HH:mm' }} &middot; {{ duration(i.started_at, i.resolved_at) }}</div>
            </div>
            @if (i.resolved_at) { <span class="pill up">resolved</span> }
            @else if (i.acknowledged_at) { <span class="pill degraded">acked</span> }
            @else { <span class="pill down">open</span> }
          </a>
        } @empty {
          <div class="empty-state tint-up">
            <span class="empty-ico"><app-icon name="check" [size]="20" /></span>
            <div><b>No incidents yet</b><div class="muted">Outages will be listed here with their duration.</div></div>
          </div>
        }
      </section>
    </div>

    <section class="card panel">
      <div class="toolbar">
        <span class="head-ico tone-emerald"><app-icon name="grid" [size]="16" /></span>
        <h2>Monitors <span class="count-badge">{{ filtered().length }}</span></h2>
        <input class="search" placeholder="Search name or target..." [value]="query()"
               (input)="query.set($any($event.target).value)" />
        <span class="spacer"></span>
        <div class="view-toggle">
          <button [class.active]="view() === 'grid'" (click)="setView('grid')" title="Grid view"><app-icon name="grid" [size]="16" /></button>
          <button [class.active]="view() === 'list'" (click)="setView('list')" title="List view"><app-icon name="list" [size]="16" /></button>
        </div>
      </div>
      <div class="toolbar">
        <div class="segmented scroll-x">
          @for (f of filters; track f.value) {
            <button class="chip" [class.active]="status() === f.value" (click)="status.set(f.value)">{{ f.label }}</button>
          }
        </div>
        @if (tags().length) {
          <div class="chips scroll-x">
            @for (t of tags(); track t) {
              <button class="chip" [class.active]="tag() === t" (click)="tag.set(tag() === t ? null : t)">#{{ t }}</button>
            }
          </div>
        }
      </div>

      @if (view() === 'grid') {
        <div class="mon-grid">
          @for (m of filtered(); track m.id) {
            <a [class]="'mon-card ' + m.status" [routerLink]="['/monitors', m.id]">
              <div class="mc-top">
                <span class="avatar" [style.--h]="hue(m.name)">{{ m.name.charAt(0) }}</span>
                <div class="grow">
                  <div class="title">{{ m.name }}</div>
                  <div class="target">{{ m.type === 'push' ? 'Push heartbeat' : m.target }}</div>
                </div>
                <span [class]="'pill ' + m.status">{{ m.status }}</span>
              </div>
              <div class="mc-stats">
                <div><span>Uptime 24h</span><b>{{ m.uptime_24h === null ? '--' : (m.uptime_24h | number: '1.0-2') + '%' }}</b></div>
                <div><span>Response</span><b>{{ m.last_latency_ms === null ? '--' : (m.last_latency_ms | number: '1.0-0') }}<small> ms</small></b></div>
                <div class="mc-spark"><app-sparkline [values]="m.recent_latency" /></div>
              </div>
              <app-heartbeat-bar [statuses]="m.recent" [max]="30" size="sm" />
              <div class="mc-foot">
                <span class="type-badge">{{ m.type }}</span>
                @for (t of m.tags.slice(0, 2); track t) { <span class="tag">#{{ t }}</span> }
                <span class="spacer"></span>
                <span>every {{ m.interval_sec }}s</span>
              </div>
            </a>
          } @empty {
            <div class="empty-state">No monitors match. <a routerLink="/monitors/new" class="kpi-link">Create one</a></div>
          }
        </div>
      } @else {
        <div class="table-wrap">
          <table class="mon-table">
            <thead><tr><th>Monitor</th><th class="hide-sm">Type</th><th>Uptime 24h</th><th class="hide-sm">Response</th><th class="hide-md">Last 30 checks</th></tr></thead>
            <tbody>
              @for (m of filtered(); track m.id) {
                <tr [routerLink]="['/monitors', m.id]">
                  <td>
                    <div class="row" style="flex-wrap: nowrap; gap: 10px">
                      <span class="avatar sm" [style.--h]="hue(m.name)">{{ m.name.charAt(0) }}</span>
                      <div style="min-width: 0"><div class="title">{{ m.name }}</div>
                        <div class="meta ellipsis">{{ m.type === 'push' ? 'Push heartbeat' : m.target }}</div></div>
                    </div>
                  </td>
                  <td class="hide-sm"><span class="type-badge">{{ m.type }}</span></td>
                  <td><b>{{ m.uptime_24h === null ? '--' : (m.uptime_24h | number: '1.0-2') + '%' }}</b></td>
                  <td class="hide-sm">{{ m.last_latency_ms === null ? '--' : (m.last_latency_ms | number: '1.0-0') + ' ms' }}</td>
                  <td class="hide-md" style="width: 34%"><app-heartbeat-bar [statuses]="m.recent" [max]="30" size="sm" /></td>
                </tr>
              } @empty {
                <tr><td colspan="5" class="muted">No monitors match.</td></tr>
              }
            </tbody>
          </table>
        </div>
      }
    </section>
  `,
})
export class DashboardComponent {
  store = inject(MonitorStore);
  private api = inject(ApiService);

  readonly today = new Date();
  readonly greeting = this.today.getHours() < 12 ? 'Good morning' : this.today.getHours() < 18 ? 'Good afternoon' : 'Good evening';
  readonly ranges = [{ hours: 24, label: '24h' }, { hours: 168, label: '7d' }, { hours: 720, label: '30d' }];
  readonly filters: { value: MonitorStatus | 'all'; label: string }[] = [
    { value: 'all', label: 'All' }, { value: 'up', label: 'Up' }, { value: 'down', label: 'Down' },
    { value: 'degraded', label: 'Degraded' }, { value: 'maintenance', label: 'Maintenance' }, { value: 'paused', label: 'Paused' },
  ];
  duration = duration;

  hours = signal(24);
  data = signal<DashboardData | null>(null);
  query = signal('');
  tag = signal<string | null>(null);
  status = signal<MonitorStatus | 'all'>('all');
  view = signal<View>((localStorage.getItem('dashView') as View) ?? 'grid');

  rangeLabel = computed(() => this.ranges.find((r) => r.hours === this.hours())?.label ?? '');
  latencySeries = computed(() => (this.data()?.series ?? []).map((p) => p.avg_latency_ms));
  uptimeSeries = computed(() => (this.data()?.series ?? []).map((p) => p.uptime));

  latencyStats = computed(() => {
    const vals = this.latencySeries().filter((v): v is number => v !== null);
    if (vals.length < 2) return null;
    return {
      min: Math.round(Math.min(...vals)),
      avg: Math.round(vals.reduce((a, b) => a + b, 0) / vals.length),
      max: Math.round(Math.max(...vals)),
    };
  });

  // Percent change between the oldest and newest quarter of the range
  latencyTrend = computed(() => {
    const vals = this.latencySeries().filter((v): v is number => v !== null);
    if (vals.length < 4) return null;
    const q = Math.max(1, Math.floor(vals.length / 4));
    const avg = (a: number[]) => a.reduce((x, y) => x + y, 0) / a.length;
    const first = avg(vals.slice(0, q));
    const change = Math.round(((avg(vals.slice(-q)) - first) / first) * 100);
    return change === 0 ? null : change;
  });
  axis = computed(() => {
    const fmt = this.hours() <= 48 ? 'HH:mm' : 'MMM d';
    return (this.data()?.series ?? []).map((p) => formatDate(p.at, fmt, 'en-US'));
  });

  slices = computed<DonutSlice[]>(() => {
    const s = this.store.summary();
    if (!s) return [];
    const pending = s.total - s.up - s.down - s.degraded - s.maintenance - s.paused;
    return [
      { label: 'Up', value: s.up, color: 'var(--up)' },
      { label: 'Degraded', value: s.degraded, color: 'var(--degraded)' },
      { label: 'Down', value: s.down, color: 'var(--down)' },
      { label: 'Maintenance', value: s.maintenance, color: 'var(--maintenance)' },
      { label: 'Paused', value: s.paused, color: 'var(--paused)' },
      { label: 'Pending', value: Math.max(0, pending), color: 'var(--pending)' },
    ];
  });

  attention = computed(() => {
    const rank: Record<string, number> = { down: 0, degraded: 1 };
    return this.store
      .monitors()
      .filter((m) => m.active && (m.status === 'down' || m.status === 'degraded' || this.sslSoon(m)))
      .sort((a, b) => (rank[a.status] ?? 2) - (rank[b.status] ?? 2))
      .slice(0, 6);
  });

  tags = computed(() => [...new Set(this.store.monitors().flatMap((m) => m.tags))].sort());

  filtered = computed(() => {
    const q = this.query().toLowerCase();
    const t = this.tag();
    const s = this.status();
    const rank: Record<string, number> = { down: 0, degraded: 1, maintenance: 2, pending: 3, up: 4, paused: 5 };
    return this.store
      .monitors()
      .filter(
        (m) =>
          (!q || m.name.toLowerCase().includes(q) || m.target.toLowerCase().includes(q)) &&
          (!t || m.tags.includes(t)) &&
          (s === 'all' || m.status === s),
      )
      .sort((a, b) => rank[a.status] - rank[b.status] || a.name.localeCompare(b.name));
  });

  constructor() {
    combineLatest([toObservable(this.hours), timer(0, 30_000)])
      .pipe(
        switchMap(([hours]) => this.api.dashboard(hours).pipe(catchError(() => EMPTY))),
        takeUntilDestroyed(),
      )
      .subscribe((d) => this.data.set(d));
  }

  setView(view: View): void {
    this.view.set(view);
    localStorage.setItem('dashView', view);
  }

  // Stable colour per monitor name for its avatar
  hue(name: string): number {
    let h = 0;
    for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) % 360;
    return h;
  }

  private sslSoon(m: Monitor): boolean {
    const days = daysUntil(m.ssl_expires_at);
    return days !== null && days <= m.ssl_expiry_warn_days;
  }
}
