import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ApiService, errorText } from '../core/api.service';
import { ConfirmService } from '../core/confirm.service';
import { Maintenance } from '../core/models';
import { MonitorStore } from '../core/monitor-store.service';
import { MultiSelectComponent } from '../shared/multi-select.component';

@Component({
  selector: 'app-maintenance',
  standalone: true,
  imports: [DatePipe, ReactiveFormsModule, MultiSelectComponent],
  template: `
    <div class="page-head">
      <div>
        <h1>Maintenance windows</h1>
        <div class="sub">Monitors in a window show as maintenance. No alerts and no incidents are created.</div>
      </div>
    </div>
    <section class="card">
      <h2>Schedule a window</h2>
      <form [formGroup]="form" (ngSubmit)="save()" class="form-grid">
        <label class="full">Title <input formControlName="title" placeholder="Database upgrade" /></label>
        <label>Start <input type="datetime-local" formControlName="starts_at" /></label>
        <label>End <input type="datetime-local" formControlName="ends_at" /></label>
        <label class="full">Monitors
          <app-multi-select [options]="monitorOptions()" [picked]="selected()" (pickedChange)="selected.set($event)"
            placeholder="All monitors" />
        </label>
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
              <td class="muted">{{ w.monitor_ids.length ? w.monitor_ids.length + (w.monitor_ids.length === 1 ? ' monitor' : ' monitors') : 'All monitors' }}</td>
              <td><button class="btn sm danger" (click)="remove(w)">Delete</button></td>
            </tr>
          } @empty {
            <tr><td colspan="5" class="empty">No maintenance windows yet.</td></tr>
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
  monitorOptions = computed(() => this.store.monitors().map((m) => ({ id: m.id, label: m.name })));
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

  save(): void {
    const v = this.form.getRawValue();
    if (new Date(v.ends_at) <= new Date(v.starts_at)) {
      this.error.set('End must be after start.');
      return;
    }
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
