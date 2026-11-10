import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed, toObservable } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { EMPTY, catchError, combineLatest, switchMap, timer } from 'rxjs';
import { ApiService } from '../core/api.service';
import { AuthService } from '../core/auth.service';
import { Incident } from '../core/models';
import { PrefsService } from '../core/prefs.service';
import { toEvents } from '../shared/activity';
import { IconComponent } from '../shared/icon.component';
import { ProjectPickComponent, parseProject } from '../shared/project-pick.component';
import { DAY, DayRange, RANGES, RangePickerComponent, rangeLabel, rangeQuery } from '../shared/range-picker.component';
import { MonitorStore } from '../core/monitor-store.service';

@Component({
  selector: 'app-activity',
  standalone: true,
  imports: [DatePipe, RouterLink, IconComponent, RangePickerComponent, ProjectPickComponent],
  template: `
    <div class="page-head">
      <div>
        <h1>Activity</h1>
        <div class="sub">Every time a service went down or came back up &middot; {{ rangeText() }}</div>
      </div>
      <span class="spacer"></span>
      <app-project-pick [project]="project()" (pick)="setParams({ project: $event === null ? null : '' + $event })" />
      <app-range-picker [hours]="hours()" [custom]="custom()" (pick)="pickHours($event)" (range)="applyCustom($event)" />
    </div>

    <section class="card panel">
      <div class="toolbar">
        <h2>All activity <span class="count-badge">{{ filtered().length }}</span></h2>
        <input class="search" placeholder="Search service..." [value]="query()"
               (input)="query.set($any($event.target).value); limit.set(page())" />
        <span class="spacer"></span>
        <div class="segmented">
          @for (k of kinds; track k.value) {
            <button class="chip" [class.active]="kind() === k.value" (click)="kind.set(k.value); limit.set(page())">{{ k.label }}</button>
          }
        </div>
      </div>

      @if (filtered().length) {
        <div class="evt-list full">
          <div class="evt evt-head">
            <span>Service</span><span>Status</span><span>Date &amp; time</span><span class="hide-sm">Duration</span><span class="hide-md">Cause</span>
          </div>
          @for (e of shown(); track e.key) {
            <a class="evt" [routerLink]="['/monitors', e.monitorId]">
              <span class="evt-name">
                <span [class]="'evt-ico ' + e.kind"><app-icon [name]="e.kind === 'up' ? 'trend-up' : 'trend-down'" [size]="14" /></span>
                <b>{{ e.name }}</b>
              </span>
              <span><span [class]="'pill ' + e.kind">{{ e.kind === 'up' ? 'Up' : 'Down' }}</span></span>
              <span class="num muted-strong">{{ e.at | date: 'MMM d, HH:mm:ss' }}</span>
              <span class="hide-sm evt-dur">{{ e.detail }}</span>
              <span class="hide-md evt-dur">{{ e.cause || '--' }}</span>
            </a>
          }
        </div>
        @if (filtered().length > limit()) {
          <button class="btn show-more" (click)="limit.set(limit() + page())">Show more ({{ filtered().length - limit() }} remaining)</button>
        }
      } @else {
        <div class="empty-state tint-up">
          <span class="empty-ico"><app-icon name="check" [size]="20" /></span>
          <div><b>No activity</b><div class="muted">No service changed state {{ rangeText() }}.</div></div>
        </div>
      }
    </section>
  `,
})
export class ActivityComponent {
  private api = inject(ApiService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private prefs = inject(PrefsService);
  private auth = inject(AuthService);
  private store = inject(MonitorStore);
  readonly kinds = [
    { value: 'all', label: 'All' },
    { value: 'down', label: 'Down' },
    { value: 'up', label: 'Up' },
  ] as const;

  page = computed(() => this.prefs.limits().page);
  hours = signal(24);
  custom = signal<DayRange | null>(null);
  incidents = signal<Incident[]>([]);
  query = signal('');
  kind = signal<'all' | 'up' | 'down'>('all');
  project = signal<number | null>(null);
  limit = signal(this.page());

  rangeText = computed(() => {
    const l = rangeLabel(this.hours(), this.custom());
    return this.custom() ? l : `last ${l}`;
  });
  private window = computed(() => {
    const c = this.custom();
    return c
      ? { from: Date.parse(`${c.from}T00:00:00`), to: Date.parse(`${c.to}T23:59:59`) }
      : { from: Date.now() - this.hours() * 3_600_000, to: Infinity };
  });
  // Only events whose time falls inside the picked range
  private events = computed(() => {
    const w = this.window();
    return toEvents(this.incidents()).filter((e) => {
      const t = Date.parse(e.at);
      return t >= w.from && t <= w.to;
    });
  });
  filtered = computed(() => {
    const q = this.query().toLowerCase();
    const k = this.kind();
    const p = this.project();
    const of = this.store.projectOf();
    return this.events().filter(
      (e) => (k === 'all' || e.kind === k) && (!q || e.name.toLowerCase().includes(q)) && (p === null || (of.get(e.monitorId) ?? 0) === p),
    );
  });
  shown = computed(() => this.filtered().slice(0, this.limit()));

  constructor() {
    this.route.queryParamMap.pipe(takeUntilDestroyed()).subscribe((p) => {
      const from = p.get('from'), to = p.get('to');
      this.custom.set(from && to && DAY.test(from) && DAY.test(to) && from <= to ? { from, to } : null);
      this.hours.set(RANGES.find((r) => r.label === p.get('range'))?.hours ?? 24);
      this.project.set(parseProject(p.get('project')));
      this.limit.set(this.page());
    });
    const range = computed(() => rangeQuery(this.hours(), this.custom()));
    const max = computed(() => this.prefs.limits().max);
    const tick = toObservable(this.auth.refreshMs).pipe(switchMap((ms) => timer(0, ms)));
    combineLatest([toObservable(range), toObservable(max), tick])
      .pipe(switchMap(([r, m]) => this.api.incidents(false, m, r).pipe(catchError(() => EMPTY))), takeUntilDestroyed())
      .subscribe((list) => this.incidents.set(list));
  }

  pickHours(hours: number): void {
    this.setParams({ range: hours === 24 ? null : RANGES.find((r) => r.hours === hours)?.label, from: null, to: null });
  }

  applyCustom(r: DayRange): void {
    this.setParams({ range: null, from: r.from, to: r.to });
  }

  setParams(params: Record<string, string | null | undefined>): void {
    this.router.navigate([], { relativeTo: this.route, queryParams: params, queryParamsHandling: 'merge', replaceUrl: true });
  }
}
