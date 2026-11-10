import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { ApiService, errorText } from '../core/api.service';
import { Channel, ChannelType } from '../core/models';

@Component({
  selector: 'app-notifications',
  standalone: true,
  imports: [ReactiveFormsModule],
  template: `
    <div class="page-head">
      <div>
        <h1>Alerting</h1>
        <div class="sub">Alerts fire on DOWN, DEGRADED and recovery. Use tags to route alerts to the right team.</div>
      </div>
    </div>

    <section class="card">
      <form [formGroup]="form" (ngSubmit)="save()" class="form-grid">
        <label>Name <input formControlName="name" placeholder="Payments on-call" /></label>
        <label>Type
          <select formControlName="type">
            <option value="teams">Microsoft Teams</option>
            <option value="slack">Slack</option>
            <option value="webhook">Generic webhook (JSON)</option>
          </select>
        </label>
        <label class="full">Webhook URL <input formControlName="url" placeholder="https://..." /></label>
        <label class="full">Only for tags (optional, comma separated) <input formControlName="tags" placeholder="prod, team-payments" /></label>
        @if (error()) { <p class="error full">{{ error() }}</p> }
        <div class="full"><button class="btn primary" [disabled]="form.invalid">Add channel</button></div>
      </form>
    </section>

    <section class="card">
      <table>
        <thead><tr><th>Name</th><th>Type</th><th>Routing</th><th></th></tr></thead>
        <tbody>
          @for (c of channels(); track c.id) {
            <tr>
              <td><b>{{ c.name }}</b></td>
              <td><span class="type-badge">{{ c.type }}</span></td>
              <td>
                @for (t of c.tags; track t) { <span class="tag">#{{ t }}</span> } @empty { <span class="muted">All monitors</span> }
              </td>
              <td class="row">
                <button class="btn sm" (click)="test(c.id!)">Send test</button>
                <button class="btn sm danger" (click)="remove(c.id!)">Delete</button>
              </td>
            </tr>
          } @empty {
            <tr><td colspan="4" class="muted">No channels yet.</td></tr>
          }
        </tbody>
      </table>
      @if (notice()) { <p class="muted">{{ notice() }}</p> }
    </section>
  `,
})
export class NotificationsComponent {
  private api = inject(ApiService);
  channels = signal<Channel[]>([]);
  error = signal('');
  notice = signal('');

  form = inject(FormBuilder).nonNullable.group({
    name: ['', Validators.required],
    type: ['teams' as ChannelType],
    url: ['', [Validators.required, Validators.pattern(/^https?:\/\/.+/)]],
    tags: [''],
  });

  constructor() {
    this.load();
  }

  load(): void {
    this.api.channels().subscribe((list) => this.channels.set(list));
  }

  save(): void {
    const v = this.form.getRawValue();
    const tags = v.tags.split(',').map((t) => t.trim()).filter(Boolean);
    this.api.createChannel({ ...v, tags, active: true }).subscribe({
      next: () => {
        this.form.reset();
        this.error.set('');
        this.load();
      },
      error: (e) => this.error.set(errorText(e)),
    });
  }

  test(id: number): void {
    this.api.testChannel(id).subscribe(() => this.notice.set('Test notification sent.'));
  }

  remove(id: number): void {
    this.api.deleteChannel(id).subscribe(() => this.load());
  }
}
