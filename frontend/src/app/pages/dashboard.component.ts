import { DatePipe, DecimalPipe, NgTemplateOutlet, formatDate } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { EMPTY, catchError, combineLatest, switchMap, timer } from 'rxjs';
import { ApiService } from '../core/api.service';
import { DashboardData, Monitor, MonitorStatus } from '../core/models';
import { MonitorStore } from '../core/monitor-store.service';
import { HeartbeatBarComponent } from '../shared/heartbeat-bar.component';
import { IconComponent } from '../shared/icon.component';
import { StatusMapComponent } from '../shared/status-map.component';
import { daysUntil, short } from '../shared/time';

interface Group { root: Monitor; children: Monitor[]; issues: number; }
type Filter = MonitorStatus | 'all' | 'blocked';
interface ActivityEvent { key: string; monitorId: number; name: string; kind: 'up' | 'down'; at: string; detail: string; }

const RANK: Record<string, number> = { down: 0, degraded: 1, maintenance: 2, pending: 3, up: 4, paused: 5 };
const PAGE = 25;

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [DatePipe, DecimalPipe, NgTemplateOutlet, RouterLink, HeartbeatBarComponent, IconComponent, StatusMapComponent],
  template: `
    <div class="page-head">
      <div>
        <h1>Dashboard</h1>
        <div class="sub">{{ greeting }} &middot; {{ todayText }}</div>
      </div>
      <span class="spacer"></span>
      <div class="range-picker">
        <div class="segmented">
          @for (r of ranges; track r.hours) {
            <button class="chip" [class.active]="!custom() && hours() === r.hours" (click)="pickHours(r.hours)">{{ r.label }}</button>
          }
          <button class="chip" [class.active]="!!custom()" (click)="customOpen.set(!customOpen())">
            <app-icon name="calendar" [size]="14" /> {{ custom() ? rangeLabel() : 'Custom' }}
          </button>
        </div>
        @if (customOpen()) {
          <div class="range-pop card">
            <div class="range-fields">
              <label>From <input type="date" [value]="draftFrom()" [max]="draftTo()" (input)="draftFrom.set($any($event.target).value)" /></label>
              <label>To <input type="date" [value]="draftTo()" [min]="draftFrom()" [max]="todayIso" (input)="draftTo.set($any($event.target).value)" /></label>
            </div>
            @if (rangeError(); as err) { <div class="range-error">{{ err }}</div> }
            <div class="range-actions">
              <button class="btn sm" (click)="customOpen.set(false)">Cancel</button>
              <button class="btn sm primary" [disabled]="!!rangeError()" (click)="applyCustom()">Apply</button>
            </div>
          </div>
        }
      </div>
    </div>

    <section class="card panel status-board">
      <div class="card-head">
        <div class="grow"><h2>Service status</h2><div class="sub">{{ store.monitors().length }} monitors &middot; click a tile to filter the list</div></div>
        @if (status() !== 'all') { <button class="btn sm" (click)="status.set('all')">Show all</button> }
      </div>
      <div class="sb-grid">
        @for (t of statusTiles(); track t.key) {
          <button class="sb-tile" [class]="'sb-tile ' + t.key" [class.active]="status() === t.key" [class.zero]="!t.count"
                  (click)="filterBy(t.key)">
            <span class="sb-ico"><app-icon [name]="t.icon" [size]="22" /></span>
            <span class="sb-body">
              <span class="sb-count">{{ t.count }}</span>
              <span class="sb-label">{{ t.label }}</span>
              <span class="sb-sub">{{ t.sub }}</span>
            </span>
          </button>
        }
      </div>
    </section>

    @if (store.summary(); as s) {
      <div class="stat-grid three">
        <div class="stat-tile" [class.warn]="uptime() !== null && uptime()! < 99.9">
          <div class="st-label"><span class="st-ico emerald"><app-icon name="activity" [size]="15" /></span>Uptime</div>
          <div class="st-value">{{ uptime() === null ? '--' : (uptime() | number: '1.2-2') }}<small>%</small></div>
          <div class="st-sub">{{ rangeText() }} &middot; target 99.9%</div>
        </div>
        <div class="stat-tile">
          <div class="st-label"><span class="st-ico indigo"><app-icon name="bolt" [size]="15" /></span>Avg response</div>
          <div class="st-value">{{ avgLatency() === null ? '--' : (avgLatency() | number: '1.0-0') }}<small>ms</small></div>
          <div class="st-sub">
            @if (latencyRange(); as lr) { {{ lr.min }}&ndash;{{ lr.max }} ms range } @else { {{ rangeText() }} }
          </div>
        </div>
        <a class="stat-tile link" routerLink="/incidents" [class.bad]="s.open_incidents > 0">
          <div class="st-label"><span class="st-ico rose"><app-icon name="alert" [size]="15" /></span>Open incidents
            <app-icon class="st-go" name="arrow" [size]="14" /></div>
          <div class="st-value">{{ s.open_incidents }}</div>
          <div class="st-sub">{{ s.unacknowledged }} not acknowledged &middot; {{ data()?.incident_count ?? 0 }} in {{ rangeText() }}</div>
        </a>
      </div>
    }

    <div class="dash-row">
      <section class="card panel">
        <div class="card-head">
          <span class="head-ico tone-indigo"><app-icon name="history" [size]="16" /></span>
          <div class="grow"><h2>Recent activity</h2><div class="sub">Which service went down and came back &middot; {{ rangeText() }}</div></div>
        </div>
        @if (events().length) {
          <div class="evt-list">
            <div class="evt evt-head"><span>Service</span><span>Status</span><span>Date &amp; time</span><span class="hide-sm">Duration</span></div>
            @for (e of events(); track e.key) {
              <a class="evt" [routerLink]="['/monitors', e.monitorId]">
                <span class="evt-name">
                  <span [class]="'evt-ico ' + e.kind"><app-icon [name]="e.kind === 'up' ? 'trend-up' : 'trend-down'" [size]="14" /></span>
                  <b>{{ e.name }}</b>
                </span>
                <span><span [class]="'pill ' + e.kind">{{ e.kind === 'up' ? 'Up' : 'Down' }}</span></span>
                <span class="num muted-strong">{{ e.at | date: 'MMM d, HH:mm:ss' }}</span>
                <span class="hide-sm evt-dur">{{ e.detail }}</span>
              </a>
            }
          </div>
        } @else {
          <div class="empty-state tint-up">
            <span class="empty-ico"><app-icon name="check" [size]="20" /></span>
            <div><b>No status changes</b><div class="muted">Every service stayed up {{ rangeText() }}.</div></div>
          </div>
        }
      </section>
      <section class="card panel">
        <div class="card-head">
          <div class="grow"><h2>Needs attention</h2><div class="sub">Root causes only</div></div>
          @if (attention().length) { <span class="count-badge amber">{{ attention().length }}</span> }
        </div>
        <div class="attn-list">
          @for (m of attention(); track m.id) {
            <a class="list-item" [routerLink]="['/monitors', m.id]">
              <span [class]="'dot ' + m.status"></span>
              <div class="grow">
                <div class="title">{{ m.name }}
                  @if (blockedChildren(m); as n) { <span class="dep-chip" title="Dependent monitors affected"><app-icon name="network" [size]="11" /> {{ n }}</span> }
                </div>
                <div class="meta">{{ m.last_message || m.target }}</div>
              </div>
            </a>
          } @empty {
            <div class="empty-state tint-up">
              <span class="empty-ico"><app-icon name="check" [size]="20" /></span>
              <div><b>All systems healthy</b><div class="muted">Nothing needs your attention.</div></div>
            </div>
          }
        </div>
      </section>
    </div>

    <section class="card panel">
      <div class="card-head">
        <span class="head-ico tone-ocean"><app-icon name="map" [size]="16" /></span>
        <div class="grow"><h2>Status map</h2><div class="sub">How services depend on each other &middot; click a node to open it</div></div>
        <div class="map-legend hide-sm">
          <span><i class="up"></i>Up</span><span><i class="down"></i>Down</span><span><i class="blocked"></i>Blocked</span>
          <span><i class="degraded"></i>Degraded</span><span><i class="paused"></i>Paused</span>
        </div>
      </div>
      @if (store.monitors().length) {
        <app-status-map [monitors]="store.monitors()" title="All monitors" />
      } @else {
        <div class="empty-state">No monitors yet. <a routerLink="/monitors/new" class="kpi-link">Create one</a></div>
      }
    </section>

    <section class="card panel">
      <div class="toolbar">
        <h2>Monitors <span class="count-badge">{{ visibleCount() }}</span></h2>
        <input class="search" placeholder="Search name or target..." [value]="query()"
               (input)="query.set($any($event.target).value); limit.set(page)" />
        @if (tags().length) {
          <select class="tag-select" [value]="tag() ?? ''" (change)="tag.set($any($event.target).value || null); limit.set(page)">
            <option value="">All tags</option>
            @for (t of tags(); track t) { <option [value]="t">#{{ t }}</option> }
          </select>
        }
        <span class="spacer"></span>
        @if (status() !== 'all') {
          <button class="chip active" (click)="status.set('all')" title="Clear status filter">
            {{ statusLabel() }} <app-icon name="x" [size]="12" />
          </button>
        }
      </div>

      <div class="mlist">
        <div class="mrow mhead">
          <span></span><span>Monitor</span><span>Status</span><span class="hide-sm">For</span><span class="hide-sm">Uptime 24h</span>
          <span class="hide-sm">Response</span><span class="hide-md">Last 30 checks</span>
        </div>
        @for (g of shownGroups(); track g.root.id) {
          <div class="mgroup" [class.has-children]="g.children.length">
            <ng-container *ngTemplateOutlet="row; context: { $implicit: g.root, group: g }" />
            @if (g.children.length && isOpen(g)) {
              @for (c of g.children; track c.id) {
                <ng-container *ngTemplateOutlet="row; context: { $implicit: c, group: null }" />
              }
            }
          </div>
        } @empty {
          <div class="empty-state">No monitors match. <a routerLink="/monitors/new" class="kpi-link">Create one</a></div>
        }
      </div>
      @if (groups().length > limit()) {
        <button class="btn show-more" (click)="limit.set(limit() + page)">
          Show more ({{ groups().length - limit() }} remaining)
        </button>
      }
    </section>

    <ng-template #row let-m let-g="group">
      <a class="mrow" [class.child]="!g" [class.blocked]="isBlocked(m)" [routerLink]="['/monitors', m.id]">
        <span class="caret-cell">
          @if (g?.children.length) {
            <button class="caret" [class.open]="isOpen(g)" (click)="toggle(g, $event)"
                    [title]="isOpen(g) ? 'Hide dependents' : 'Show dependents'"><app-icon name="arrow" [size]="14" /></button>
          } @else if (!g) { <app-icon class="child-ico" name="child" [size]="14" /> }
        </span>
        <span class="m-name">
          <span [class]="'dot ' + (isBlocked(m) ? 'paused' : m.status)"></span>
          <span class="m-text">
            <b>{{ m.name }}
              @if (g?.children.length) { <span class="dep-chip" title="Dependent monitors"><app-icon name="network" [size]="11" /> {{ g.children.length }}</span> }
            </b>
            <small><span class="m-type">{{ m.type }}</span>&middot; {{ m.type === 'push' ? 'Push heartbeat' : m.target }}</small>
          </span>
        </span>
        <span>
          @if (isBlocked(m)) { <span class="pill paused" title="Down because its parent is down">blocked</span> }
          @else { <span [class]="'pill ' + m.status">{{ m.status }}</span> }
        </span>
        <span class="hide-sm num muted-strong" [title]="m.status_since ? 'Since ' + (m.status_since | date: 'MMM d, HH:mm') : ''">
          {{ m.status_since ? shortSince(m.status_since) : '--' }}</span>
        <span class="hide-sm num">{{ m.uptime_24h === null ? '--' : (m.uptime_24h | number: '1.0-2') + '%' }}</span>
        <span class="hide-sm num">{{ m.last_latency_ms === null ? '--' : (m.last_latency_ms | number: '1.0-0') + ' ms' }}</span>
        <span class="hide-md"><app-heartbeat-bar [statuses]="m.recent" [max]="30" size="sm" /></span>
      </a>
    </ng-template>
  `,
})
export class DashboardComponent {
  store = inject(MonitorStore);
  private api = inject(ApiService);

  readonly page = PAGE;
  readonly today = new Date();
  readonly todayText = formatDate(this.today, 'EEEE, MMM d', 'en-US');
  readonly todayIso = formatDate(this.today, 'yyyy-MM-dd', 'en-US');
  readonly greeting = this.today.getHours() < 12 ? 'Good morning' : this.today.getHours() < 18 ? 'Good afternoon' : 'Good evening';
  readonly ranges = [{ hours: 24, label: '24h' }, { hours: 168, label: '7d' }, { hours: 720, label: '30d' }];

  hours = signal(24);
  custom = signal<{ from: string; to: string } | null>(null);
  customOpen = signal(false);
  draftFrom = signal(formatDate(Date.now() - 6 * 86_400_000, 'yyyy-MM-dd', 'en-US'));
  draftTo = signal(this.todayIso);
  data = signal<DashboardData | null>(null);
  query = signal('');
  tag = signal<string | null>(null);
  status = signal<Filter>('all');
  limit = signal(PAGE);
  private openState = signal(new Map<number, boolean>());

  rangeLabel = computed(() => {
    const c = this.custom();
    if (!c) return this.ranges.find((r) => r.hours === this.hours())?.label ?? '';
    const f = (d: string) => formatDate(d, 'MMM d', 'en-US');
    return c.from === c.to ? f(c.from) : `${f(c.from)} – ${f(c.to)}`;
  });
  rangeText = computed(() => (this.custom() ? this.rangeLabel() : `last ${this.rangeLabel()}`));
  uptime = computed(() => this.data()?.uptime ?? null);
  avgLatency = computed(() => this.data()?.avg_latency_ms ?? null);
  rangeError = computed(() => {
    const from = this.draftFrom(), to = this.draftTo();
    if (!from || !to) return 'Pick both dates';
    if (from > to) return 'From must be before To';
    if ((Date.parse(to) - Date.parse(from)) / 86_400_000 >= 90) return 'Range can be at most 90 days';
    return null;
  });
  private rangeParams = computed<{ hours?: number; start?: string; end?: string }>(() => {
    const c = this.custom();
    if (!c) return { hours: this.hours() };
    return { start: new Date(`${c.from}T00:00:00`).toISOString(), end: new Date(`${c.to}T23:59:59`).toISOString() };
  });

  private latencyValues = computed(() =>
    (this.data()?.series ?? []).map((p) => p.avg_latency_ms).filter((v): v is number => v !== null));
  latencyRange = computed(() => {
    const v = this.latencyValues();
    return v.length < 2 ? null : { min: Math.round(Math.min(...v)), max: Math.round(Math.max(...v)) };
  });

  statusTiles = computed(() => {
    const all = this.store.monitors();
    const total = all.length || 1;
    const blocked = all.filter((m) => this.isBlocked(m)).length;
    const n = (s: MonitorStatus) => all.filter((m) => m.status === s).length;
    const up = n('up');
    return [
      { key: 'up', label: 'Up', icon: 'check-circle', count: up, sub: `${Math.round((up / total) * 100)}% of monitors` },
      { key: 'down', label: 'Down', icon: 'x-circle', count: n('down') - blocked, sub: 'Root cause failures' },
      { key: 'blocked', label: 'Blocked', icon: 'link-off', count: blocked, sub: 'Parent is down' },
      { key: 'degraded', label: 'Degraded', icon: 'alert', count: n('degraded'), sub: 'Slow or partial' },
      { key: 'maintenance', label: 'Maintenance', icon: 'wrench', count: n('maintenance'), sub: 'Planned work' },
      { key: 'paused', label: 'Paused', icon: 'pause-circle', count: n('paused'), sub: 'Not checked' },
    ] as { key: Filter; label: string; icon: string; count: number; sub: string }[];
  });
  statusLabel = computed(() => this.statusTiles().find((t) => t.key === this.status())?.label ?? this.status());

  // Each incident = a "down" event, plus an "up" event when it was resolved
  events = computed<ActivityEvent[]>(() => {
    const incidents = this.data()?.incidents ?? [];
    const list: ActivityEvent[] = [];
    for (const i of incidents) {
      const name = i.monitor_name ?? `#${i.monitor_id}`;
      list.push({
        key: `d${i.id}`, monitorId: i.monitor_id, name, kind: 'down', at: i.started_at,
        detail: i.resolved_at ? `Down for ${short(i.started_at, i.resolved_at)}` : `Still down · ${short(i.started_at)}`,
      });
      if (i.resolved_at) {
        const next = incidents
          .filter((o) => o.monitor_id === i.monitor_id && o.started_at > i.resolved_at!)
          .sort((a, b) => a.started_at.localeCompare(b.started_at))[0];
        list.push({
          key: `u${i.id}`, monitorId: i.monitor_id, name, kind: 'up', at: i.resolved_at,
          detail: next ? `Up for ${short(i.resolved_at, next.started_at)}` : `Up for ${short(i.resolved_at)} · running`,
        });
      }
    }
    return list.sort((a, b) => b.at.localeCompare(a.at)).slice(0, 8);
  });

  private byId = computed(() => new Map(this.store.monitors().map((m) => [m.id, m])));
  private children = computed(() => {
    const map = new Map<number, Monitor[]>();
    for (const m of this.store.monitors()) {
      if (m.parent_id !== null) map.set(m.parent_id, [...(map.get(m.parent_id) ?? []), m]);
    }
    return map;
  });

  attention = computed(() =>
    this.store
      .monitors()
      .filter((m) => m.active && !this.isBlocked(m) && (m.status === 'down' || m.status === 'degraded' || this.sslSoon(m)))
      .sort((a, b) => (RANK[a.status] ?? 9) - (RANK[b.status] ?? 9))
      .slice(0, 6),
  );

  tags = computed(() => [...new Set(this.store.monitors().flatMap((m) => m.tags))].sort());

  private matches = computed(() => {
    const q = this.query().toLowerCase();
    const t = this.tag();
    const s = this.status();
    const statusOk = (m: Monitor) =>
      s === 'all' ||
      (s === 'blocked' ? this.isBlocked(m) : s === 'down' ? m.status === 'down' && !this.isBlocked(m) : m.status === s);
    return this.store.monitors().filter(
      (m) =>
        (!q || m.name.toLowerCase().includes(q) || m.target.toLowerCase().includes(q)) &&
        (!t || m.tags.includes(t)) &&
        statusOk(m),
    );
  });
  visibleCount = computed(() => this.matches().length);

  // Children are nested under their top-most visible ancestor
  groups = computed<Group[]>(() => {
    const list = this.matches();
    const shown = new Map(list.map((m) => [m.id, m]));
    const rootOf = (m: Monitor) => {
      let r = m;
      const seen = new Set<number>();
      while (r.parent_id !== null && shown.has(r.parent_id) && !seen.has(r.id)) {
        seen.add(r.id);
        r = shown.get(r.parent_id)!;
      }
      return r;
    };
    const map = new Map<number, Group>();
    for (const m of list) {
      const root = rootOf(m);
      const g = map.get(root.id) ?? { root, children: [], issues: 0 };
      if (m !== root) {
        g.children.push(m);
        if (m.status === 'down' || m.status === 'degraded') g.issues++;
      }
      map.set(root.id, g);
    }
    const worst = (g: Group) => Math.min(RANK[g.root.status], ...g.children.map((c) => RANK[c.status]));
    for (const g of map.values()) g.children.sort((a, b) => RANK[a.status] - RANK[b.status] || a.name.localeCompare(b.name));
    return [...map.values()].sort((a, b) => worst(a) - worst(b) || a.root.name.localeCompare(b.root.name));
  });
  shownGroups = computed(() => this.groups().slice(0, this.limit()));

  constructor() {
    combineLatest([toObservable(this.rangeParams), timer(0, 30_000)])
      .pipe(
        switchMap(([range]) => this.api.dashboard(range).pipe(catchError(() => EMPTY))),
        takeUntilDestroyed(),
      )
      .subscribe((d) => this.data.set(d));
  }

  parentOf(m: Monitor): Monitor | null {
    return m.parent_id === null ? null : this.byId().get(m.parent_id) ?? null;
  }

  isBlocked(m: Monitor): boolean {
    return m.status === 'down' && this.parentOf(m)?.status === 'down';
  }

  blockedChildren(m: Monitor): number {
    return (this.children().get(m.id) ?? []).filter((c) => this.isBlocked(c)).length;
  }

  // Groups start open when a dependent has a problem, closed otherwise
  isOpen(g: Group): boolean {
    return this.openState().get(g.root.id) ?? g.issues > 0;
  }

  toggle(g: Group, event: Event): void {
    event.preventDefault();
    event.stopPropagation();
    const next = new Map(this.openState());
    next.set(g.root.id, !this.isOpen(g));
    this.openState.set(next);
  }

  filterBy(key: Filter): void {
    this.status.set(this.status() === key ? 'all' : key);
    this.limit.set(PAGE);
  }

  shortSince(iso: string): string {
    return short(iso);
  }

  pickHours(hours: number): void {
    this.custom.set(null);
    this.customOpen.set(false);
    this.hours.set(hours);
  }

  applyCustom(): void {
    if (this.rangeError()) return;
    this.custom.set({ from: this.draftFrom(), to: this.draftTo() });
    this.customOpen.set(false);
  }

  private sslSoon(m: Monitor): boolean {
    const days = daysUntil(m.ssl_expires_at);
    return days !== null && days <= m.ssl_expiry_warn_days;
  }
}
