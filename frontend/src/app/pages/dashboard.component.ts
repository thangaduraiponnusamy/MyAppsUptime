import { DatePipe, DecimalPipe, NgTemplateOutlet, formatDate } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { EMPTY, catchError, combineLatest, switchMap, timer } from 'rxjs';
import { ApiService } from '../core/api.service';
import { AuthService } from '../core/auth.service';
import { DashboardData, Monitor, MonitorStatus } from '../core/models';
import { MonitorStore } from '../core/monitor-store.service';
import { toEvents } from '../shared/activity';
import { HeartbeatBarComponent } from '../shared/heartbeat-bar.component';
import { IconComponent } from '../shared/icon.component';
import { ProjectPickComponent } from '../shared/project-pick.component';
import { DAY, DayRange, RANGES, RangePickerComponent, rangeLabel, rangeQuery } from '../shared/range-picker.component';
import { StatusMapComponent } from '../shared/status-map.component';
import { PrefsService } from '../core/prefs.service';
import { daysUntil, short } from '../shared/time';

interface Group { root: Monitor; children: Monitor[]; issues: number; }
export type Filter = MonitorStatus | 'all' | 'blocked';
const FILTERS: Filter[] = ['up', 'down', 'blocked', 'degraded', 'maintenance', 'paused'];

const RANK: Record<string, number> = { down: 0, degraded: 1, maintenance: 2, pending: 3, up: 4, paused: 5 };
const PAGE = 25;

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [DatePipe, DecimalPipe, NgTemplateOutlet, RouterLink, HeartbeatBarComponent, IconComponent, RangePickerComponent, StatusMapComponent, ProjectPickComponent],
  template: `
    <div class="page-head">
      <div>
        <h1>Dashboard</h1>
        <div class="sub">{{ greeting }} &middot; {{ todayText }}</div>
      </div>
      <span class="spacer"></span>
      <app-project-pick [project]="project()" (pick)="pickProject($event === null ? '' : '' + $event)" />
    </div>

    <section class="card panel status-board">
      <div class="sb-layout">
        <div class="sb-side">
          <div class="card-head">
            <span class="head-ico tone-ocean"><app-icon name="server" [size]="16" /></span>
            <div class="grow"><h2>Status</h2><div class="sub">{{ scoped().length }} monitors &middot; current state</div></div>
            @if (attention().length) {
              <span class="sb-badge bad">{{ attention().length }} need attention</span>
            } @else {
              <span class="sb-badge ok">All healthy</span>
            }
          </div>
          <div class="sb-body">
            <button class="sb-donut" (click)="filterBy('all')" title="Show all monitors">
              <svg viewBox="0 0 120 120">
                <defs>
                  <linearGradient id="sbg-up" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#4ade80" /><stop offset="1" stop-color="#15803d" /></linearGradient>
                  <linearGradient id="sbg-down" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fb8a7e" /><stop offset="1" stop-color="#dc2626" /></linearGradient>
                  <linearGradient id="sbg-blocked" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fdba74" /><stop offset="1" stop-color="#ea580c" /></linearGradient>
                  <linearGradient id="sbg-degraded" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#fcd34d" /><stop offset="1" stop-color="#d97706" /></linearGradient>
                  <linearGradient id="sbg-maintenance" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#93c5fd" /><stop offset="1" stop-color="#2563eb" /></linearGradient>
                  <linearGradient id="sbg-paused" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#cbd5e1" /><stop offset="1" stop-color="#64748b" /></linearGradient>
                </defs>
                <circle cx="60" cy="60" r="50" class="sb-track" />
                @for (a of arcs(); track a.key) {
                  <circle cx="60" cy="60" r="50" [class]="'sb-arc ' + a.key" [class.solo]="arcs().length === 1"
                          [attr.stroke-dasharray]="a.len + ' ' + (circ - a.len)" [attr.stroke-dashoffset]="-a.start" />
                }
              </svg>
              <span class="sb-center">
                <b>{{ upPct() }}<i>%</i></b>
                <small>Operational</small>
                <em>{{ statusTiles()[0].count }} of {{ scoped().length }} up</em>
              </span>
            </button>
            <div class="sb-tiles">
              @for (t of statusTiles(); track t.key) {
                <button [class]="'sb-tile ' + t.key" [class.active]="status() === t.key" [class.zero]="!t.count"
                        (click)="filterBy(t.key)" [title]="'Show ' + t.label.toLowerCase() + ' monitors'">
                  <span class="sb-name"><i></i>{{ t.label }}</span>
                  <span class="sb-num"><b>{{ t.count }}</b><small>{{ t.pct }}%</small></span>
                </button>
              }
            </div>
          </div>
        </div>
        <div class="sb-map">
          <div class="card-head">
            <span class="head-ico tone-indigo"><app-icon name="map" [size]="16" /></span>
            <div class="grow"><h2>Status map</h2><div class="sub">How monitors depend on each other</div></div>
            @if (status() !== 'all') {
              <button class="chip active" (click)="filterBy('all')" title="Clear status filter">{{ statusLabel() }} <app-icon name="x" [size]="12" /></button>
            }
          </div>
          @if (scoped().length) {
            <app-status-map [monitors]="scoped()" [filter]="status()" [title]="projectTitle()" />
          } @else {
            <div class="empty-state">No monitors yet. @if (auth.isAdmin()) { <a routerLink="/monitors/new" class="kpi-link">Create one</a> }</div>
          }
        </div>
      </div>
    </section>

    @if (attention().length) {
      <section class="card panel">
        <div class="card-head">
          <span class="head-ico tone-rose"><app-icon name="alert" [size]="16" /></span>
          <div class="grow"><h2>Needs attention</h2><div class="sub">Root causes only &middot; right now</div></div>
          <span class="count-badge amber">{{ attention().length }}</span>
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
          }
        </div>
      </section>
    }

    <div class="section-head">
      <div><h2>History</h2><div class="sub">Uptime, response and activity for {{ rangeText() }}</div></div>
      <span class="spacer"></span>
      <app-range-picker [hours]="hours()" [custom]="custom()" (pick)="pickHours($event)" (range)="applyCustom($event)" />
    </div>

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
        <a class="stat-tile link" routerLink="/incidents" [class.bad]="openNow() > 0">
          <div class="st-label"><span class="st-ico rose"><app-icon name="alert" [size]="15" /></span>Incidents
            <app-icon class="st-go" name="arrow" [size]="14" /></div>
          <div class="st-value">{{ data()?.incident_count ?? 0 }}</div>
          <div class="st-sub">{{ rangeText() }} &middot; {{ openNow() }} open now</div>
        </a>
      </div>
    }

    <section class="card panel">
        <a class="card-head head-link" routerLink="/activity" queryParamsHandling="preserve" title="Open all activity">
          <span class="head-ico tone-indigo"><app-icon name="history" [size]="16" /></span>
          <div class="grow"><h2>Recent activity</h2><div class="sub">Last {{ prefs.limits().recent }} changes &middot; {{ rangeText() }}</div></div>
          <span class="view-all">View all <app-icon name="arrow" [size]="14" /></span>
        </a>
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
          <button class="chip active" (click)="filterBy('all')" title="Clear status filter">
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
          <div class="empty-state">No monitors match. @if (auth.isAdmin()) { <a routerLink="/monitors/new" class="kpi-link">Create one</a> }</div>
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
            <small><span class="m-type">{{ m.type }}</span>&middot; {{ m.type === 'push' ? 'Push heartbeat' : m.target }}
              @if (project() === null && store.projectName(m.project_id); as pn) { <span class="m-project">{{ pn }}</span> }</small>
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
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  prefs = inject(PrefsService);
  auth = inject(AuthService);
  readonly page = PAGE;
  readonly today = new Date();
  readonly todayText = formatDate(this.today, 'EEEE, MMM d', 'en-US');
  readonly greeting = this.today.getHours() < 12 ? 'Good morning' : this.today.getHours() < 18 ? 'Good afternoon' : 'Good evening';

  hours = signal(24);
  custom = signal<DayRange | null>(null);
  data = signal<DashboardData | null>(null);
  query = signal('');
  tag = signal<string | null>(null);
  status = signal<Filter>('all');
  /** null = all projects, 0 = monitors without a project */
  project = signal<number | null>(null);
  limit = signal(PAGE);
  private openState = signal(new Map<number, boolean>());

  private rangeLabel = computed(() => rangeLabel(this.hours(), this.custom()));
  rangeText = computed(() => (this.custom() ? this.rangeLabel() : `last ${this.rangeLabel()}`));
  uptime = computed(() => this.data()?.uptime ?? null);
  avgLatency = computed(() => this.data()?.avg_latency_ms ?? null);
  private rangeParams = computed(() => {
    const p = this.project();
    return p === null ? rangeQuery(this.hours(), this.custom()) : { ...rangeQuery(this.hours(), this.custom()), project_id: p };
  });

  scoped = computed(() => {
    const p = this.project();
    const all = this.store.monitors();
    return p === null ? all : all.filter((m) => (m.project_id ?? 0) === p);
  });
  openNow = computed(() =>
    this.project() === null
      ? (this.store.summary()?.open_incidents ?? 0)
      : this.scoped().filter((m) => m.status === 'down').length);
  projectTitle = computed(() => {
    const p = this.project();
    return p === null ? 'All monitors' : p === 0 ? 'No team' : this.store.projectName(p) || 'Team';
  });

  private latencyValues = computed(() =>
    (this.data()?.series ?? []).map((p) => p.avg_latency_ms).filter((v): v is number => v !== null));
  latencyRange = computed(() => {
    const v = this.latencyValues();
    return v.length < 2 ? null : { min: Math.round(Math.min(...v)), max: Math.round(Math.max(...v)) };
  });

  statusTiles = computed(() => {
    const all = this.scoped();
    const total = all.length || 1;
    const blocked = all.filter((m) => this.isBlocked(m)).length;
    const n = (s: MonitorStatus) => all.filter((m) => m.status === s).length;
    return ([
      { key: 'up', label: 'Up', count: n('up') },
      { key: 'down', label: 'Down', count: n('down') - blocked },
      { key: 'degraded', label: 'Degraded', count: n('degraded') },
      { key: 'blocked', label: 'Blocked', count: blocked },
      { key: 'maintenance', label: 'Maintenance', count: n('maintenance') },
      { key: 'paused', label: 'Paused', count: n('paused') },
    ] as { key: Filter; label: string; count: number }[]).map((t) => ({ ...t, pct: Math.round((t.count / total) * 100) }));
  });
  readonly circ = 2 * Math.PI * 50;
  upPct = computed(() => this.statusTiles()[0].pct);
  arcs = computed(() => {
    const parts = this.statusTiles().filter((t) => t.count);
    const total = parts.reduce((s, t) => s + t.count, 0);
    // Round caps eat into the gap, so it must be wider than the stroke
    const gap = parts.length > 1 ? 15 : 0;
    let start = 0;
    return parts.map((t) => {
      const full = (t.count / total) * this.circ;
      const a = { key: t.key, start: start + gap / 2, len: Math.max(full - gap, 0.01) };
      start += full;
      return a;
    });
  });
  statusLabel = computed(() => this.statusTiles().find((t) => t.key === this.status())?.label ?? this.status());

  events = computed(() => toEvents(this.data()?.incidents ?? []).slice(0, this.prefs.limits().recent));

  private byId = computed(() => new Map(this.store.monitors().map((m) => [m.id, m])));
  private children = computed(() => {
    const map = new Map<number, Monitor[]>();
    for (const m of this.store.monitors()) {
      if (m.parent_id !== null) map.set(m.parent_id, [...(map.get(m.parent_id) ?? []), m]);
    }
    return map;
  });

  attention = computed(() =>
    this.scoped()
      .filter((m) => m.active && !this.isBlocked(m) && (m.status === 'down' || m.status === 'degraded' || this.sslSoon(m)))
      .sort((a, b) => (RANK[a.status] ?? 9) - (RANK[b.status] ?? 9))
      .slice(0, 6),
  );

  tags = computed(() => [...new Set(this.scoped().flatMap((m) => m.tags))].sort());

  private matches = computed(() => {
    const q = this.query().toLowerCase();
    const t = this.tag();
    const s = this.status();
    const statusOk = (m: Monitor) =>
      s === 'all' ||
      (s === 'blocked' ? this.isBlocked(m) : s === 'down' ? m.status === 'down' && !this.isBlocked(m) : m.status === s);
    return this.scoped().filter(
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
    // Range and status filter live in the URL, so the logo / Dashboard link (plain "/") resets them
    this.route.queryParamMap.pipe(takeUntilDestroyed()).subscribe((p) => {
      const from = p.get('from'), to = p.get('to');
      const custom = from && to && DAY.test(from) && DAY.test(to) && from <= to ? { from, to } : null;
      this.custom.set(custom);
      this.hours.set(RANGES.find((r) => r.label === p.get('range'))?.hours ?? 24);
      const s = p.get('status') as Filter | null;
      this.status.set(s && FILTERS.includes(s) ? s : 'all');
      const proj = p.get('project');
      this.project.set(proj !== null && /^\d+$/.test(proj) ? Number(proj) : null);
      this.limit.set(PAGE);
      if (!p.keys.length) {
        this.query.set('');
        this.tag.set(null);
        this.openState.set(new Map());
      }
    });
    const tick = toObservable(this.auth.refreshMs).pipe(switchMap((ms) => timer(0, ms)));
    combineLatest([toObservable(this.rangeParams), tick])
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
    this.setParams({ status: key === 'all' || this.status() === key ? null : key });
  }

  pickProject(value: string): void {
    this.setParams({ project: value === '' ? null : value });
  }

  shortSince(iso: string): string {
    return short(iso);
  }

  pickHours(hours: number): void {
    this.setParams({ range: hours === 24 ? null : RANGES.find((r) => r.hours === hours)?.label, from: null, to: null });
  }

  applyCustom(r: DayRange): void {
    this.setParams({ range: null, from: r.from, to: r.to });
  }

  private setParams(params: Record<string, string | null | undefined>): void {
    this.router.navigate([], { relativeTo: this.route, queryParams: params, queryParamsHandling: 'merge', replaceUrl: true });
  }

  private sslSoon(m: Monitor): boolean {
    const days = daysUntil(m.ssl_expires_at);
    return days !== null && days <= m.ssl_expiry_warn_days;
  }
}
