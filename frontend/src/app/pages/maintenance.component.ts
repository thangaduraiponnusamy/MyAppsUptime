import { DatePipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ApiService, errorText } from '../core/api.service';
import { ConfirmService } from '../core/confirm.service';
import { Maintenance } from '../core/models';
import { MonitorStore } from '../core/monitor-store.service';

@Component({
  selector: 'app-maintenance',
  standalone: true,
  imports: [DatePipe, ReactiveFormsModule],
  template: `
    <section class="card">
      <h1>Maintenance windows</h1>
      <p class="muted">Monitors in a window show as MAINTENANCE. No alerts and no incidents are created.</p>
      <form [formGroup]="form" (ngSubmit)="save()" class="form-grid">
        <label class="full">Title <input formControlName="title" placeholder="Database upgrade" /></label>
        <label>Start <input type="datetime-local" formControlName="starts_at" /></label>
        <label>End <input type="datetime-local" formControlName="ends_at" /></label>
        <div class="full">
          <div class="muted">Monitors (none selected = all monitors)</div>
          <div class="check-list">
            @for (m of store.monitors(); track m.id) {
              <label><input type="checkbox" [checked]="selected().has(m.id)" (change)="toggle(m.id)" /> {{ m.name }}</label>
            }
          </div>
        </div>
        @if (error()) { <p class="error full">{{ error() }}</p> }
        <div class="full"><button class="btn primary" [disabled]="form.invalid">Schedule</button></div>
      </form>
    </section>

    <section class="card">
      <table>
        <thead><tr><th>Title</th><th>Start</th><th>End</th><th>Scope</th><th></th></tr></thead>
        <tbody>
          @for (w of windows(); track w.id) {
            <tr>
              <td>{{ w.title }}</td>
              <td>{{ w.starts_at | date: 'MMM d, HH:mm' }}</td>
              <td>{{ w.ends_at | date: 'MMM d, HH:mm' }}</td>
              <td class="muted">{{ w.monitor_ids.length ? w.monitor_ids.length + ' monitors' : 'All monitors' }}</td>
              <td><button class="btn sm danger" (click)="remove(w)">Delete</button></td>
            </tr>
          } @empty {
            <tr><td colspan="5" class="muted">No maintenance windows.</td></tr>
          }
        </tbody>
      </table>
    </section>
  `,
})
export class MaintenanceComponent {
  private api = inject(ApiService);
  store = inject(MonitorStore);
  private confirm = inject(ConfirmService);
  windows = signal<Maintenance[]>([]);
  selected = signal(new Set<number>());
  error = signal('');

  form = inject(FormBuilder).nonNullable.group({
    title: ['', Validators.required],
    starts_at: ['', Validators.required],
    ends_at: ['', Validators.required],
  });

  constructor() {
    this.load();
  }

  load(): void {
    this.api.maintenance().subscribe((list) => this.windows.set(list));
  }

  toggle(id: number): void {
    const next = new Set(this.selected());
    next.has(id) ? next.delete(id) : next.add(id);
    this.selected.set(next);
  }

  save(): void {
    const v = this.form.getRawValue();
    this.api
      .createMaintenance({
        title: v.title,
        starts_at: new Date(v.starts_at).toISOString(),
        ends_at: new Date(v.ends_at).toISOString(),
        monitor_ids: [...this.selected()],
      })
      .subscribe({
        next: () => {
          this.form.reset();
          this.selected.set(new Set());
          this.error.set('');
          this.load();
        },
        error: (e) => this.error.set(errorText(e)),
      });
  }

  async remove(w: Maintenance): Promise<void> {
    const ok = await this.confirm.ask({
      title: 'Delete maintenance window?',
      message: `You are about to delete the maintenance window "${w.title}".`,
      details: ['Its monitors return to normal checks and alerts right away.'],
      typeToConfirm: w.title,
      action: 'Delete window',
    });
    if (ok) this.api.deleteMaintenance(w.id!).subscribe(() => this.load());
  }
}
