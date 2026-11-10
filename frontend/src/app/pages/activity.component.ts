import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { RouterLink } from '@angular/router';
import { EMPTY, catchError, switchMap, timer } from 'rxjs';
import { ApiService } from '../core/api.service';
import { Incident } from '../core/models';
import { toEvents } from '../shared/activity';
import { IconComponent } from '../shared/icon.component';

const PAGE = 50;

@Component({
  selector: 'app-activity',
  standalone: true,
  imports: [DatePipe, RouterLink, IconComponent],
  template: `
    <div class="page-head">
      <div>
        <h1>Activity</h1>
        <div class="sub">Every time a service went down or came back up</div>
      </div>
    </div>

    <section class="card panel">
      <div class="toolbar">
        <h2>All activity <span class="count-badge">{{ filtered().length }}</span></h2>
        <input class="search" placeholder="Search service..." [value]="query()"
               (input)="query.set($any($event.target).value); limit.set(page)" />
        <span class="spacer"></span>
        <div class="segmented">
          @for (k of kinds; track k.value) {
            <button class="chip" [class.active]="kind() === k.value" (click)="kind.set(k.value); limit.set(page)">{{ k.label }}</button>
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
          <button class="btn show-more" (click)="limit.set(limit() + page)">Show more ({{ filtered().length - limit() }} remaining)</button>
        }
      } @else {
        <div class="empty-state tint-up">
          <span class="empty-ico"><app-icon name="check" [size]="20" /></span>
          <div><b>No activity</b><div class="muted">No service changed state yet.</div></div>
        </div>
      }
    </section>
  `,
})
export class ActivityComponent {
  private api = inject(ApiService);
  readonly page = PAGE;
  readonly kinds = [
    { value: 'all', label: 'All' },
    { value: 'down', label: 'Down' },
    { value: 'up', label: 'Up' },
  ] as const;

  incidents = signal<Incident[]>([]);
  query = signal('');
  kind = signal<'all' | 'up' | 'down'>('all');
  limit = signal(PAGE);

  private events = computed(() => toEvents(this.incidents()));
  filtered = computed(() => {
    const q = this.query().toLowerCase();
    const k = this.kind();
    return this.events().filter((e) => (k === 'all' || e.kind === k) && (!q || e.name.toLowerCase().includes(q)));
  });
  shown = computed(() => this.filtered().slice(0, this.limit()));

  constructor() {
    timer(0, 30_000)
      .pipe(switchMap(() => this.api.incidents(false, 500).pipe(catchError(() => EMPTY))), takeUntilDestroyed())
      .subscribe((list) => this.incidents.set(list));
  }
}
