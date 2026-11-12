import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService, errorText } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { GeneralSettings } from '../../core/models';
import { LIMITS, PrefsService } from '../../core/prefs.service';

// Browsers still use some old zone names; show the current ones
const RENAMED: Record<string, string> = {
  'Asia/Calcutta': 'Asia/Kolkata', 'Asia/Saigon': 'Asia/Ho_Chi_Minh', 'Asia/Katmandu': 'Asia/Kathmandu',
  'Asia/Rangoon': 'Asia/Yangon', 'Europe/Kiev': 'Europe/Kyiv', 'Atlantic/Faeroe': 'Atlantic/Faroe',
  'America/Godthab': 'America/Nuuk', 'Pacific/Ponape': 'Pacific/Pohnpei', 'Pacific/Truk': 'Pacific/Chuuk',
};
const modernZone = (z: string) => RENAMED[z] ?? z;

@Component({
  selector: 'app-settings-general',
  standalone: true,
  imports: [FormsModule],
  template: `
    <h2 class="s-title">General</h2>

    <section class="s-section">
      <h3>Refresh, links and logs</h3>
      <p class="muted">Auto refresh, app address and how long logs are kept. Applies to everyone.</p>
      <div class="s-fields">
        <label>Refresh every (seconds)
          <input type="number" min="5" max="3600" [(ngModel)]="form.refresh_sec" [disabled]="!auth.isAdmin()" />
          <small class="muted">5 to 3600. Default 5.</small>
        </label>
        <label>App address
          <input [(ngModel)]="form.app_url" placeholder="https://uptime.example.com" [disabled]="!auth.isAdmin()" />
          <small class="muted">Used for links and buttons in alerts and password reset emails.
            @if (auth.isAdmin() && form.app_url !== origin) {
              <a href="" (click)="$event.preventDefault(); form.app_url = origin">Use {{ origin }}</a>
            }
          </small>
        </label>
        <label>Time zone for alerts
          <input list="tz-list" [(ngModel)]="form.timezone" placeholder="Server default (type to search, e.g. Kolkata)" [disabled]="!auth.isAdmin()" />
          <datalist id="tz-list">
            @for (z of zones; track z) { <option [value]="z"></option> }
          </datalist>
          <small class="muted">Times in Teams, Telegram, Slack and email alerts.
            @if (auth.isAdmin() && form.timezone !== localZone) {
              <a href="" (click)="$event.preventDefault(); form.timezone = localZone">Use {{ localZone }}</a>
            }
          </small>
        </label>
        <label>Keep logs for
          <span class="row nowrap">
            <input type="number" min="1" max="3650" [(ngModel)]="form.retention_value" [disabled]="!auth.isAdmin()" style="flex: 1" />
            <select [(ngModel)]="form.retention_unit" [disabled]="!auth.isAdmin()" style="width: auto">
              <option value="days">Days</option>
              <option value="months">Months</option>
              <option value="years">Years</option>
            </select>
          </span>
          <small class="muted">Older check results and closed incidents are deleted. Max 10 years. Default 1 year.</small>
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
  readonly origin = location.origin;
  readonly localZone = modernZone(Intl.DateTimeFormat().resolvedOptions().timeZone);
  zones: string[] = [...new Set(['UTC', this.localZone, ...((Intl as any).supportedValuesOf?.('timeZone') ?? []).map(modernZone)])].sort();
  form: GeneralSettings = { app_url: '', timezone: '', refresh_sec: 5, retention_value: 1, retention_unit: 'years' };
  notice = signal('');

  constructor() {
    this.api.general().subscribe((g) => {
      this.form = { ...g };
      if (g.timezone && !this.zones.includes(g.timezone)) this.zones = [g.timezone, ...this.zones];
    });
  }

  save(): void {
    this.api.saveGeneral({ ...this.form, refresh_sec: Number(this.form.refresh_sec), retention_value: Number(this.form.retention_value) }).subscribe({
      next: (g) => {
        this.form = { ...g };
        this.notice.set('Saved.');
        this.auth.reload();
      },
      error: (e) => this.notice.set(errorText(e)),
    });
  }
}
