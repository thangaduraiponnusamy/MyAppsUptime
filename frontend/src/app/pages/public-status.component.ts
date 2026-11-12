import { DatePipe, DecimalPipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { EMPTY, catchError, map, of, switchMap, tap, timer } from 'rxjs';
import { ApiService } from '../core/api.service';
import { AuthService } from '../core/auth.service';
import { PublicStatus } from '../core/models';
import { BeatDetail, HeartbeatBarComponent } from '../shared/heartbeat-bar.component';
import { IconComponent } from '../shared/icon.component';
import { LogoComponent } from '../shared/logo.component';

const BARS = 50;

const STATE_LABEL: Record<string, string> = {
  up: 'Operational',
  degraded: 'Degraded',
  down: 'Outage',
  maintenance: 'Maintenance',
  pending: 'Checking',
  paused: 'Paused',
};

@Component({
  selector: 'app-public-status',
  standalone: true,
  imports: [DatePipe, DecimalPipe, HeartbeatBarComponent, IconComponent, LogoComponent],
  template: `
    <div class="sp">
      <div class="sp-wrap">
        <div class="sp-top">
          <app-logo [size]="32" [tagline]="false" />
          <span class="sp-updated"><span class="live-dot"></span> Updated {{ updatedAt() | date: 'HH:mm:ss' }}</span>
        </div>

        @if (page(); as p) {
          <div class="sp-title row">
            @if (p.logo) { <img class="sp-logo" [src]="p.logo" alt="" /> }
            <div>
              <h1>{{ p.title }}</h1>
              @if (p.description) { <p>{{ p.description }}</p> }
            </div>
          </div>

          <div [class]="'sp-banner ' + p.overall">
            <span class="badge"><app-icon [name]="p.overall === 'up' ? 'check' : p.overall === 'down' ? 'x' : 'alert'" [size]="18" /></span>
            <b>{{ headline() }}</b>
            <span class="count">{{ operational() }} / {{ p.monitors.length }} operational</span>
          </div>

          <section class="sp-card">
            @for (m of p.monitors; track m.id) {
              <div class="sp-row">
                <div>
                  <div class="name">{{ m.name }}</div>
                  <span [class]="'sp-state ' + m.status">{{ label(m.status) }}</span>
                </div>
                <app-heartbeat-bar [statuses]="m.bars" [details]="times(m.times)" [max]="bars" [labels]="true" />
                <div class="sp-uptime">{{ m.uptime_30d === null ? '--' : (m.uptime_30d | number: '1.0-2') + '%' }}</div>
              </div>
            }
          </section>

          @if (p.incidents.length) {
            <h2 style="margin: 8px 0 0">Recent incidents</h2>
            <section class="sp-card">
              @for (i of p.incidents; track $index) {
                <div class="sp-incident">
                  <div class="row">
                    <b>{{ i.monitor_name }}</b>
                    <span [class]="'pill ' + (i.resolved_at ? 'up' : 'down')">{{ i.resolved_at ? 'Resolved' : 'Ongoing' }}</span>
                    <span class="spacer"></span>
                    <span class="muted">{{ i.started_at | date: 'MMM d, HH:mm' }}{{ i.resolved_at ? ' - ' + (i.resolved_at | date: 'MMM d, HH:mm') : '' }}</span>
                  </div>
                  @if (i.note) { <div class="sp-note">{{ i.note }}</div> }
                </div>
              }
            </section>
          }

          <div class="sp-foot">
            <div class="sp-legend">
              <span><i style="background: var(--up)"></i>Operational</span>
              <span><i style="background: var(--degraded)"></i>Degraded</span>
              <span><i style="background: var(--down)"></i>Outage</span>
              <span><i style="background: var(--maintenance)"></i>Maintenance</span>
            </div>
            <span class="spacer"></span>
            <span>Uptime = last 30 days &middot; refreshes every {{ refreshSec() }}s</span>
          </div>
        } @else if (notFound()) {
          <div class="sp-banner down"><b>Status page not found</b></div>
        }
      </div>
    </div>
  `,
})
export class PublicStatusComponent {
  private api = inject(ApiService);
  readonly bars = BARS;
  page = signal<PublicStatus | null>(null);
  notFound = signal(false);
  updatedAt = signal(new Date());
  refreshSec = signal(5);

  operational = computed(() => this.page()?.monitors.filter((m) => m.status === 'up').length ?? 0);

  headline = computed(() => {
    const p = this.page();
    if (!p) return '';
    const down = p.monitors.filter((m) => m.status === 'down').length;
    if (down && down === p.monitors.length) return 'Major outage';
    if (down) return 'Partial outage';
    if (p.monitors.some((m) => m.status === 'maintenance')) return 'Maintenance in progress';
    if (p.overall === 'degraded') return 'Some systems are degraded';
    return 'All systems operational';
  });

  constructor() {
    const slug = inject(ActivatedRoute).snapshot.paramMap.get('slug') ?? '';
    // Same refresh interval as the app (Settings > General)
    inject(AuthService)
      .options()
      .pipe(
        map((o) => Math.max(5, o.refresh_sec)),
        catchError(() => of(5)),
        tap((sec) => this.refreshSec.set(sec)),
        switchMap((sec) => timer(0, sec * 1000)),
        switchMap(() =>
          this.api.publicStatus(slug).pipe(
            tap(() => this.updatedAt.set(new Date())),
            catchError(() => {
              this.notFound.set(true);
              return EMPTY;
            }),
          ),
        ),
        takeUntilDestroyed(),
      )
      .subscribe((p) => this.page.set(p));
  }

  label(status: string): string {
    return STATE_LABEL[status] ?? 'No data';
  }

  times(list: string[] | undefined): BeatDetail[] {
    return (list ?? []).map((time) => ({ time }));
  }
}
