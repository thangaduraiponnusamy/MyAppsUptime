import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { EMPTY, Subject, catchError, combineLatest, filter, first, forkJoin, merge, switchMap, timeout, timer } from 'rxjs';
import { ApiService, errorText } from '../core/api.service';
import { AuthService } from '../core/auth.service';
import { ConfirmService } from '../core/confirm.service';
import { Heartbeat, Monitor, Stats } from '../core/models';
import { MonitorStore } from '../core/monitor-store.service';
import { HeartbeatBarComponent } from '../shared/heartbeat-bar.component';
import { IconComponent } from '../shared/icon.component';
import { LatencyChartComponent } from '../shared/latency-chart.component';
import { daysUntil } from '../shared/time';
import { DayRange, RANGES, RangePickerComponent, rangeQuery } from '../shared/range-picker.component';

const PAGE = 10;

@Component({
  selector: 'app-monitor-detail',
  standalone: true,
  imports: [DatePipe, DecimalPipe, RouterLink, HeartbeatBarComponent, IconComponent, LatencyChartComponent, RangePickerComponent],
  template: `
    @if (monitor(); as m) {
      <div class="page-head">
        <span [class]="'dot ' + m.status"></span>
        <div>
          <h1>{{ m.name }}</h1>
          <div class="sub">
            <span class="type-badge">{{ m.type }}</span>
            {{ m.type === 'push' ? 'Waiting for pushes' : m.target }}{{ m.port ? ':' + m.port : '' }}
            &middot; every {{ m.interval_sec }}s
            @if (m.probe_name) { &middot; from {{ m.probe_name }} }
            @if (parent(); as p) { &middot; depends on <a [routerLink]="['/monitors', p.id]" style="color: var(--primary)">{{ p.name }}</a> }
            @if (store.projectName(m.project_id); as pn) { &middot; <a [routerLink]="['/']" [queryParams]="{ project: m.project_id }" style="color: var(--primary)">{{ pn }}</a> }
          </div>
        </div>
        <span class="spacer"></span>
        <button class="btn" (click)="checkNow(m)" [disabled]="checking() || !m.active || m.type === 'push'">
          <app-icon name="bolt" /> {{ checking() ? 'Checking...' : 'Check now' }}</button>
        @if (auth.isAdmin()) {
          <button class="btn" (click)="togglePause(m)">{{ m.active ? 'Pause' : 'Resume' }}</button>
          <a class="btn" [routerLink]="['/monitors', m.id, 'edit']">Edit</a>
          <button class="btn danger" (click)="remove(m)">Delete</button>
        }
      </div>

      <section class="card">
        <div class="row">
          <span [class]="'pill ' + m.status">{{ m.status }}</span>
          <span class="muted">{{ m.last_message }}</span>
          <span class="spacer"></span>
          <span class="muted">Last check {{ m.last_checked ? (m.last_checked | date: 'HH:mm:ss') : 'never' }}</span>
          @for (t of m.tags; track t) { <span class="tag">#{{ t }}</span> }
        </div>
        @if (notice()) {
          <p class="row muted">@if (noticeStatus(); as s) { <span [class]="'pill ' + s">{{ s }}</span> } {{ notice() }}</p>
        }
        <div style="margin-top: 14px"><app-heartbeat-bar [statuses]="statuses()" [details]="details()" [max]="72" [labels]="true" /></div>
        @if (m.fail_count && m.status !== 'down') {
          <p class="muted">{{ m.fail_count }} failed check(s) in a row - alert after {{ m.down_after ?? 'the default number of' }} checks.</p>
        }
      </section>

      @if (stats(); as s) {
        <div class="two-col">
          <section class="card grid-cards">
            <div class="stat"><div class="label">Response</div><div class="value">{{ fmtMs(m.last_latency_ms) }}</div></div>
            <div class="stat"><div class="label">Avg 24h</div><div class="value">{{ fmtMs(s.avg_latency_ms) }}</div></div>
            <div class="stat"><div class="label">P95 24h</div><div class="value">{{ fmtMs(s.p95_latency_ms) }}</div></div>
            <div class="stat"><div class="label">Uptime 24h</div><div class="value up">{{ fmtPct(s.uptime_24h) }}</div></div>
            <div class="stat"><div class="label">Uptime 7d</div><div class="value up">{{ fmtPct(s.uptime_7d) }}</div></div>
            <div class="stat"><div class="label">Uptime 30d</div><div class="value up">{{ fmtPct(s.uptime_30d) }}</div></div>
            <div class="stat"><div class="label">Incidents 30d</div><div class="value down">{{ s.incidents_30d }}</div></div>
            <div class="stat"><div class="label">MTTR</div><div class="value">{{ s.mttr_min === null ? '--' : (s.mttr_min | number: '1.0-1') + ' min' }}</div></div>
            @if (sslDays() !== null) {
              <div class="stat"><div class="label">SSL expires</div>
                <div class="value" [class.degraded]="sslDays()! <= m.ssl_expiry_warn_days">{{ sslDays() }} d</div></div>
            }
            @if (domainDays() !== null) {
              <div class="stat"><div class="label">Domain expires</div>
                <div class="value" [class.degraded]="domainDays()! <= 30">{{ domainDays() }} d</div></div>
            }
          </section>

          <section class="card">
            <h2>SLA &amp; error budget</h2>
            <p class="muted">Target {{ s.sla_target }}% over 30 days</p>
            <div class="progress" [class.low]="s.budget_remaining_pct < 25">
              <div [style.width.%]="s.budget_remaining_pct"></div>
            </div>
            <div class="row" style="margin-top: 10px">
              <div><b>{{ s.budget_remaining_pct }}%</b> <span class="muted">budget left</span></div>
              <span class="spacer"></span>
              <span class="muted">{{ s.downtime_30d_min }} / {{ s.error_budget_min }} min used</span>
            </div>
          </section>
        </div>
      }

      <section class="card">
        <div class="row">
          <h2>Response time</h2><span class="spacer"></span>
          <app-range-picker [ranges]="ranges" [hours]="hours()" [custom]="custom()" (pick)="pickHours($event)" (range)="custom.set($event); page.set(0)" />
        </div>
        <app-latency-chart [beats]="beats()" />
      </section>

      <div class="two-col">
        <section class="card">
          <h2>Status changes</h2>
          <table>
            <thead><tr><th>Status</th><th>Time</th><th>Message</th></tr></thead>
            <tbody>
              @for (e of pageEvents(); track e.created_at) {
                <tr>
                  <td><span [class]="'pill ' + e.status">{{ e.status }}</span></td>
                  <td>{{ e.created_at | date: 'MMM d, HH:mm:ss' }}</td>
                  <td class="muted">{{ e.message }}</td>
                </tr>
              } @empty {
                <tr><td colspan="3" class="muted">No events in this range.</td></tr>
              }
            </tbody>
          </table>
          @if (pages() > 1) {
            <div class="pager">
              <button class="chip" [disabled]="page() === 0" (click)="page.set(page() - 1)">&lsaquo;</button>
              @for (p of pageList(); track $index) {
                @if (p < 0) { <span class="muted">&hellip;</span> }
                @else { <button class="chip" [class.active]="p === page()" (click)="page.set(p)">{{ p + 1 }}</button> }
              }
              <button class="chip" [disabled]="page() >= pages() - 1" (click)="page.set(page() + 1)">&rsaquo;</button>
            </div>
          }
        </section>

        <section class="card" style="display: flex; flex-direction: column; gap: 14px">
          <h2>Integrations</h2>
          @if (m.push_token) {
            <div>
              <div class="muted">Push URL (call from your cron job or script)</div>
              <div class="code-box mono">{{ pushUrl(m) }}
                <span class="spacer"></span>
                <button class="btn sm" (click)="copy(pushUrl(m))"><app-icon name="copy" [size]="14" /></button>
              </div>
            </div>
          }
          <div>
            <div class="muted">Badges (monitor must be on a status page)</div>
            <div class="code-box mono">{{ origin }}/api/public/badge/{{ m.id }}/uptime.svg{{ auth.user()?.active_tenant === 'admin' ? '' : '?t=' + auth.user()?.active_tenant }}</div>
          </div>
          <div>
            <div class="muted">Prometheus</div>
            <div class="code-box mono">{{ origin }}/api/metrics</div>
          </div>
        </section>
      </div>
    }
  `,
})
export class MonitorDetailComponent {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private api = inject(ApiService);
  store = inject(MonitorStore);
  auth = inject(AuthService);
  private confirm = inject(ConfirmService);

  readonly origin = location.origin;
  monitor = signal<Monitor | null>(null);
  beats = signal<Heartbeat[]>([]);
  stats = signal<Stats | null>(null);
  notice = signal('');
  noticeStatus = signal('');
  checking = signal(false);
  private reload = new Subject<void>();
  hours = signal(24);
  custom = signal<DayRange | null>(null);
  page = signal(0);
  readonly ranges = [{ hours: 1, label: '1h' }, ...RANGES];

  statuses = computed(() => this.beats().map((b) => b.status));
  details = computed(() => this.beats().map((b) => ({ time: b.created_at, message: b.message, latency: b.latency_ms })));
  sslDays = computed(() => daysUntil(this.monitor()?.ssl_expires_at ?? null));
  domainDays = computed(() => daysUntil(this.monitor()?.domain_expires_at ?? null));
  parent = computed(() => this.store.monitors().find((p) => p.id === this.monitor()?.parent_id) ?? null);
  events = computed(() =>
    this.beats()
      .filter((b, i, all) => i === 0 || all[i - 1].status !== b.status)
      .reverse(),
  );
  pages = computed(() => Math.ceil(this.events().length / PAGE));
  pageEvents = computed(() => {
    const p = Math.min(this.page(), Math.max(0, this.pages() - 1));
    return this.events().slice(p * PAGE, (p + 1) * PAGE);
  });
  // 1 2 3 ... 10 style page list; -1 is a gap
  pageList = computed(() => {
    const n = this.pages(), cur = this.page();
    const keep = [...Array(n).keys()].filter((i) => i < 1 || i >= n - 1 || Math.abs(i - cur) <= 1);
    return keep.flatMap((p, i) => (i && p - keep[i - 1] > 1 ? [-1, p] : [p]));
  });

  constructor() {
    const tick = toObservable(this.auth.refreshMs).pipe(switchMap((ms) => merge(timer(0, ms), this.reload)));
    const range = computed(() => rangeQuery(this.hours(), this.custom()));
    combineLatest([this.route.paramMap, toObservable(range), tick])
      .pipe(
        switchMap(([params, q]) => {
          const id = Number(params.get('id'));
          return forkJoin({
            monitor: this.api.monitor(id),
            beats: this.api.heartbeats(id, q),
            stats: this.api.stats(id),
          }).pipe(catchError(() => EMPTY));
        }),
        takeUntilDestroyed(),
      )
      .subscribe((r) => {
        this.monitor.set(r.monitor);
        this.beats.set(r.beats);
        this.stats.set(r.stats);
      });
  }

  pickHours(h: number): void {
    this.custom.set(null);
    this.hours.set(h);
    this.page.set(0);
  }

  pushUrl(m: Monitor): string {
    return `${this.origin}/api/public/push/${m.push_token}?status=up&msg=OK&ping=`;
  }

  copy(text: string): void {
    this.noticeStatus.set('');
    navigator.clipboard.writeText(text).then(() => this.notice.set('Copied to clipboard.'));
  }

  fmtMs(v: number | null | undefined): string {
    return v == null ? '--' : `${Math.round(v)} ms`;
  }

  fmtPct(v: number | null | undefined): string {
    return v == null ? '--' : `${v.toFixed(2)}%`;
  }

  checkNow(m: Monitor): void {
    const before = this.monitor()?.last_checked ?? null;
    this.checking.set(true);
    this.noticeStatus.set('');
    this.notice.set(m.probe_name ? `Checking from ${m.probe_name}...` : 'Checking...');
    this.api.checkNow(m.id)
      .pipe(
        switchMap(() => timer(1000, 1000)),
        switchMap(() => this.api.monitor(m.id)),
        filter((x) => x.last_checked !== before),
        first(),
        timeout(m.probe_name ? 90_000 : 60_000),
      )
      .subscribe({
        next: (x) => {
          this.monitor.set(x);
          this.reload.next();
          this.store.refresh();
          this.checking.set(false);
          this.noticeStatus.set(x.status);
          this.notice.set(`Checked at ${new Date(x.last_checked!).toLocaleTimeString()}${x.last_message ? ' - ' + x.last_message : ''}`);
        },
        error: (e) => {
          this.checking.set(false);
          this.notice.set(e?.name === 'TimeoutError' ? 'Check is still running. The result shows on the next refresh.' : errorText(e));
        },
      });
  }

  togglePause(m: Monitor): void {
    (m.active ? this.api.pause(m.id) : this.api.resume(m.id)).subscribe((updated) => {
      this.monitor.set(updated);
      this.store.refresh();
    });
  }

  async remove(m: Monitor): Promise<void> {
    const kids = this.store.monitors().filter((c) => c.parent_id === m.id).length;
    const ok = await this.confirm.ask({
      title: 'Delete monitor?',
      message: `You are about to delete the monitor "${m.name}".`,
      details: [
        'All its checks, incidents and uptime history are deleted.',
        ...(kids ? [`${kids} monitor(s) that depend on it lose that link.`] : []),
      ],
      typeToConfirm: m.name,
      action: 'Delete monitor',
    });
    if (!ok) return;
    this.api.deleteMonitor(m.id).subscribe(() => {
      this.store.refresh();
      this.router.navigate(['/']);
    });
  }
}
