import { DecimalPipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { MonitorStatus } from '../core/models';
import { MonitorStore } from '../core/monitor-store.service';
import { HeartbeatBarComponent } from '../shared/heartbeat-bar.component';
import { IconComponent } from '../shared/icon.component';
import { SparklineComponent } from '../shared/sparkline.component';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [DecimalPipe, RouterLink, HeartbeatBarComponent, IconComponent, SparklineComponent],
  template: `
    <div class="page-head">
      <div>
        <h1>Overview</h1>
        <div class="sub">Live health of {{ store.summary()?.total ?? 0 }} monitors. Refreshes every 10 seconds.</div>
      </div>
      <span class="spacer"></span>
      <input class="search" placeholder="Search name or target..." [value]="query()"
             (input)="query.set($any($event.target).value)" />
    </div>

    @if (store.summary(); as s) {
      <div class="two-col">
        <div class="grid-cards">
          <div class="card kpi"><span class="ico up"><app-icon name="check" /></span>
            <div><div class="val">{{ s.up }}</div><div class="lbl">Up</div></div></div>
          <div class="card kpi"><span class="ico down"><app-icon name="x" /></span>
            <div><div class="val">{{ s.down }}</div><div class="lbl">Down</div></div></div>
          <div class="card kpi"><span class="ico degraded"><app-icon name="bolt" /></span>
            <div><div class="val">{{ s.degraded }}</div><div class="lbl">Degraded</div></div></div>
          <div class="card kpi"><span class="ico maintenance"><app-icon name="wrench" /></span>
            <div><div class="val">{{ s.maintenance }}</div><div class="lbl">Maintenance</div></div></div>
          <div class="card kpi"><span class="ico down"><app-icon name="alert" /></span>
            <div><div class="val">{{ s.unacknowledged }}</div><div class="lbl">Unacked incidents</div></div></div>
          <div class="card kpi"><span class="ico degraded"><app-icon name="lock" /></span>
            <div><div class="val">{{ s.ssl_expiring_soon }}</div><div class="lbl">SSL &lt; 14 days</div></div></div>
        </div>
        <div class="card row" style="justify-content: space-around">
          <div class="ring" [style.--p]="s.avg_uptime_24h ?? 0">
            <div><span><b>{{ s.avg_uptime_24h === null ? '--' : (s.avg_uptime_24h | number: '1.0-2') + '%' }}</b>
              <span class="muted">fleet uptime</span></span></div>
          </div>
          <div>
            <div class="muted">Avg response</div>
            <div style="font-size: 24px; font-weight: 750">{{ s.avg_latency_ms === null ? '--' : (s.avg_latency_ms | number: '1.0-0') + ' ms' }}</div>
            <div class="muted" style="margin-top: 8px">Paused</div>
            <div style="font-size: 24px; font-weight: 750">{{ s.paused }}</div>
          </div>
        </div>
      </div>
    }

    <div class="row">
      <div class="segmented">
        @for (f of filters; track f.value) {
          <button class="chip" [class.active]="status() === f.value" (click)="status.set(f.value)">{{ f.label }}</button>
        }
      </div>
      <span class="spacer"></span>
      <div class="chips">
        @for (t of tags(); track t) {
          <button class="chip" [class.active]="tag() === t" (click)="tag.set(tag() === t ? null : t)">#{{ t }}</button>
        }
      </div>
    </div>

    <div class="mon-grid">
      @for (m of filtered(); track m.id) {
        <a class="mon-card" [class]="'mon-card ' + m.status" [routerLink]="['/monitors', m.id]">
          <div class="row" style="flex-wrap: nowrap">
            <span [class]="'dot ' + m.status"></span>
            <div style="min-width: 0; flex: 1">
              <div class="title">{{ m.name }}</div>
              <div class="target">{{ m.type === 'push' ? 'Push heartbeat' : m.target }}</div>
            </div>
            <span class="type-badge">{{ m.type }}</span>
          </div>
          <div class="metrics">
            <div><b>{{ m.uptime_24h === null ? '--' : (m.uptime_24h | number: '1.0-2') + '%' }}</b><span>uptime 24h</span></div>
            <div><b>{{ m.last_latency_ms === null ? '--' : (m.last_latency_ms | number: '1.0-0') }}</b><span>ms</span></div>
            <div style="flex: 1"><app-sparkline [values]="m.recent_latency" /></div>
          </div>
          <app-heartbeat-bar [statuses]="m.recent" [max]="30" size="sm" />
          @if (m.tags.length) {
            <div>@for (t of m.tags; track t) { <span class="tag">#{{ t }}</span> }</div>
          }
        </a>
      } @empty {
        <div class="card muted">No monitors match. <a routerLink="/monitors/new" style="color: var(--primary)">Create one</a>.</div>
      }
    </div>
  `,
})
export class DashboardComponent {
  store = inject(MonitorStore);
  query = signal('');
  tag = signal<string | null>(null);
  status = signal<MonitorStatus | 'all'>('all');

  readonly filters: { value: MonitorStatus | 'all'; label: string }[] = [
    { value: 'all', label: 'All' },
    { value: 'up', label: 'Up' },
    { value: 'down', label: 'Down' },
    { value: 'degraded', label: 'Degraded' },
    { value: 'maintenance', label: 'Maintenance' },
    { value: 'paused', label: 'Paused' },
  ];

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
}
