import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService, errorText } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { GeneralSettings } from '../../core/models';
import { LIMITS, PrefsService } from '../../core/prefs.service';

@Component({
  selector: 'app-settings-general',
  standalone: true,
  imports: [FormsModule],
  template: `
    <h2 class="s-title">General</h2>

    <section class="s-section">
      <h3>Auto refresh</h3>
      <p class="muted">How often the dashboard, monitor pages and public status pages load new data. Applies to everyone.</p>
      <div class="s-fields">
        <label>Refresh every (seconds)
          <input type="number" min="5" max="3600" [(ngModel)]="form.refresh_sec" [disabled]="!auth.isAdmin()" />
          <small class="muted">5 to 3600. Default 5.</small>
        </label>
        <label>App address
          <input [(ngModel)]="form.app_url" placeholder="https://uptime.example.com" [disabled]="!auth.isAdmin()" />
          <small class="muted">Used for links in alerts and password reset emails.</small>
        </label>
      </div>
      @if (auth.isAdmin()) {
        <div class="row"><button class="btn primary" (click)="save()">Save</button>
          @if (notice()) { <span class="muted">{{ notice() }}</span> }</div>
      }
    </section>

    <section class="s-section">
      <h3>Display limits</h3>
      <p class="muted">How many activity rows to show. Saved in this browser only.</p>
      <div class="s-fields">
        @for (l of limitList; track l.key) {
          <label>{{ l.label }}
            <input type="number" [min]="l.min" [max]="l.max" [value]="prefs.limits()[l.key]"
                   (change)="prefs.set(l, $any($event.target).value); $any($event.target).value = prefs.limits()[l.key]" />
            <small class="muted">{{ l.hint }} ({{ l.min }}&ndash;{{ l.max }}, default {{ l.def }})</small>
          </label>
        }
      </div>
      <div class="row"><button class="btn" (click)="prefs.reset()">Reset to defaults</button></div>
    </section>
  `,
})
export class GeneralSettingsComponent {
  private api = inject(ApiService);
  auth = inject(AuthService);
  prefs = inject(PrefsService);
  readonly limitList = LIMITS;
  form: GeneralSettings = { app_url: '', refresh_sec: 5 };
  notice = signal('');

  constructor() {
    this.api.general().subscribe((g) => (this.form = { ...g }));
  }

  save(): void {
    this.api.saveGeneral({ ...this.form, refresh_sec: Number(this.form.refresh_sec) }).subscribe({
      next: (g) => {
        this.form = { ...g };
        this.notice.set('Saved.');
        this.auth.reload();
      },
      error: (e) => this.notice.set(errorText(e)),
    });
  }
}
