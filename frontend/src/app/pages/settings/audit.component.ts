import { Component, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { ApiService, errorText } from '../../core/api.service';
import { AuditEvent, AuditFilter } from '../../core/models';
import { DayRange, RangePickerComponent } from '../../shared/range-picker.component';
import { SelectComponent } from '../../shared/select.component';

const PAGE = 50;
// Values of AuditEvent.kind written by backend/app/audit.py
const KINDS: { id: string | null; label: string }[] = [
  { id: null, label: 'All types' },
  { id: 'account', label: 'Sign-ins & account' }, { id: 'monitor', label: 'Services' }, { id: 'incident', label: 'Incidents' },
  { id: 'maintenance', label: 'Maintenance' }, { id: 'status page', label: 'Status pages' }, { id: 'project', label: 'Projects' },
  { id: 'channel', label: 'Notification channels' }, { id: 'user', label: 'Users' }, { id: 'private location', label: 'Private locations' },
  { id: 'organization', label: 'Tenants' }, { id: 'settings', label: 'Settings' }, { id: 'config', label: 'Import' },
  { id: 'push', label: 'Push notifications' },
];

@Component({
  selector: 'app-settings-audit',
  standalone: true,
  imports: [DatePipe, RangePickerComponent, SelectComponent],
  template: `
    <h2 class="s-title">Audit log</h2>
    <p class="muted">Every sign-in and every change to services, projects, channels, users and settings.
      Entries cannot be edited or deleted; secrets are masked.</p>

    <section class="s-section">
      <div class="s-head">
        <h3>Events <span class="count-badge">{{ rows().length }}{{ rows().length === pageSize ? '+' : '' }}</span></h3>
        <button class="btn sm" (click)="exportCsv()">Export CSV</button>
      </div>
      <div class="toolbar">
        <input class="search" placeholder="Search user, name, action or IP..." [value]="q()" (input)="typed($any($event.target).value)" />
        <app-select class="tag-select" label="Type" [options]="kinds" [value]="kind()" (valueChange)="kind.set($event); go(0)" />
        <span class="spacer"></span>
        <app-range-picker [hours]="hours()" [custom]="custom()" (pick)="hours.set($event); custom.set(null); go(0)" (range)="custom.set($event); go(0)" />
      </div>
    @if (notice()) { <p class="muted">{{ notice() }}</p> }

    @if (rows().length) {
      <div class="table-wrap"><table>
        <thead><tr><th>Time</th><th>User</th><th>Action</th><th>What</th><th class="hide-sm">IP address</th></tr></thead>
        <tbody>
          @for (a of rows(); track a.id) {
            <tr>
              <td class="nowrap">{{ a.created_at | date: 'MMM d, y, HH:mm:ss' }}</td>
              <td>{{ a.username }}</td>
              <td>{{ a.action }}</td>
              <td>{{ a.kind }} @if (a.target) { <b>{{ a.target }}</b> }
                @if (a.detail) { <div class="muted small">{{ a.detail }}</div> }
                @if (a.changes) { <div class="muted small mono">{{ a.changes }}</div> }</td>
              <td class="hide-sm mono">{{ a.ip || '-' }}</td>
            </tr>
          }
        </tbody>
      </table></div>
    } @else {
      <p class="muted">Nothing found.</p>
    }
    @if (page() > 0 || rows().length === pageSize) {
      <div class="row">
        <button class="btn sm" [disabled]="page() === 0" (click)="go(page() - 1)">Previous</button>
        <span class="muted small">Page {{ page() + 1 }}</span>
        <button class="btn sm" [disabled]="rows().length < pageSize" (click)="go(page() + 1)">Next</button>
      </div>
    }
    </section>
  `,
})
export class AuditSettingsComponent {
  private api = inject(ApiService);
  readonly pageSize = PAGE;
  readonly kinds = KINDS;
  q = signal('');
  kind = signal<string | null>(null);
  hours = signal(720);
  custom = signal<DayRange | null>(null);
  rows = signal<AuditEvent[]>([]);
  page = signal(0);
  notice = signal('');
  private timer?: ReturnType<typeof setTimeout>;

  constructor() {
    this.load();
  }

  typed(value: string): void {
    this.q.set(value);
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.go(0), 300);
  }

  go(page: number): void {
    this.page.set(page);
    this.load();
  }

  private query(): AuditFilter {
    const c = this.custom();
    // Custom days are local; "to" includes the whole day
    const since = c ? new Date(`${c.from}T00:00:00`) : new Date(Date.now() - this.hours() * 3600_000);
    const until = c ? new Date(`${c.to}T00:00:00`) : null;
    until?.setDate(until.getDate() + 1);
    return { q: this.q().trim() || undefined, kind: this.kind() ?? undefined,
             since: since.toISOString(), until: until?.toISOString() };
  }

  private load(): void {
    this.notice.set('');
    this.api.audit(this.query(), PAGE, this.page() * PAGE).subscribe({
      next: (r) => this.rows.set(r),
      error: (e) => this.notice.set(errorText(e)),
    });
  }

  exportCsv(): void {
    this.api.auditCsv(this.query()).subscribe({
      next: (blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'audit-log.csv';
        a.click();
        URL.revokeObjectURL(url);
      },
      error: (e) => this.notice.set(errorText(e)),
    });
  }
}
