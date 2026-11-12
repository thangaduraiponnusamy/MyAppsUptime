import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService, errorText } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { ConfirmService } from '../../core/confirm.service';
import { AdminDefaults, DatabaseInfo, GeneralSettings, ServerSettings } from '../../core/models';
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
          <input [(ngModel)]="form.app_url" [placeholder]="defaults()?.app_url ? 'Admin default: ' + defaults()!.app_url : 'https://uptime.example.com'" [disabled]="!auth.isAdmin()" />
          <small class="muted">Used for links and buttons in alerts and password reset emails.
            @if (defaults()?.app_url) { Leave empty to use the admin's. }
            @if (auth.isAdmin() && form.app_url !== origin) {
              <a href="" (click)="$event.preventDefault(); form.app_url = origin">Use {{ origin }}</a>
            }
          </small>
        </label>
        <label>Time zone for alerts
          <input list="tz-list" [(ngModel)]="form.timezone" [disabled]="!auth.isAdmin()"
                 [placeholder]="defaults()?.timezone ? 'Admin default: ' + defaults()!.timezone : 'Server default (type to search, e.g. Kolkata)'" />
          <datalist id="tz-list">
            @for (z of zones; track z) { <option [value]="z"></option> }
          </datalist>
          <small class="muted">Times shown in all alerts.
            @if (defaults()?.timezone) { Leave empty to use the admin's. }
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
      <h3>Alerts</h3>
      <div class="s-fields">
        <label>Alert after N failed checks in a row
          <input type="number" min="1" max="20" [(ngModel)]="form.down_after_checks" [disabled]="!auth.isAdmin()" />
          <small class="muted">1 to 20. Default 3. A monitor can override it.</small>
        </label>
        <label>Warn before SSL / domain expiry (days)
          <input [(ngModel)]="warnDays" placeholder="30, 14, 7" [disabled]="!auth.isAdmin()" />
          <small class="muted">Comma separated, up to 5 values. One alert per step.</small>
        </label>
        <label class="inline"><input type="checkbox" [(ngModel)]="form.domain_expiry" [disabled]="!auth.isAdmin()" />
          Check domain expiry (RDAP) for website monitors</label>
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

    @if (auth.isAdmin() && db(); as d) {
      <section class="s-section">
        <h3>Monitor history</h3>
        <p class="muted">Database size {{ d.size_kb }} KB &middot; {{ d.heartbeats }} check results &middot; {{ d.incidents }} incidents</p>
        <div class="row">
          <button class="btn" (click)="shrink()">Shrink database</button>
          <button class="btn danger" (click)="clearStats()">Clear all statistics</button>
          @if (dbNotice()) { <span class="muted">{{ dbNotice() }}</span> }
        </div>
      </section>
    }

    @if (auth.isSuper() && server(); as s) {
      <section class="s-section">
        <h3>Server</h3>
        <p class="muted">Applies to the whole server (all tenants).</p>
        <div class="s-fields">
          <label class="inline"><input type="checkbox" [(ngModel)]="s.backup_enabled" /> Daily database backup</label>
          <label>Keep backups for (days)
            <input type="number" min="1" max="365" [(ngModel)]="s.backup_keep_days" />
            <small class="muted">Saved in {{ s.backup_dir }}. Default 7.</small>
          </label>
          <label class="inline"><input type="checkbox" [(ngModel)]="s.dns_cache" /> DNS cache (60 seconds)</label>
        </div>
        <div class="row">
          <button class="btn primary" (click)="saveServer(s)">Save</button>
          <button class="btn" (click)="backupNow()">Backup now</button>
          @if (serverNotice()) { <span class="muted">{{ serverNotice() }}</span> }
        </div>
        @if (s.backups?.length) {
          <p class="muted">Backups: @for (b of s.backups; track b.name) { <span class="tag">{{ b.name }} ({{ b.size_kb }} KB)</span> }</p>
        }
      </section>
    }
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
  form: GeneralSettings = {
    app_url: '', timezone: '', refresh_sec: 5, retention_value: 1, retention_unit: 'years',
    down_after_checks: 3, expiry_warn_days: [30, 14, 7], domain_expiry: true,
  };
  warnDays = '30, 14, 7';
  notice = signal('');
  db = signal<DatabaseInfo | null>(null);
  dbNotice = signal('');
  server = signal<ServerSettings | null>(null);
  serverNotice = signal('');
  defaults = signal<AdminDefaults | null>(null);
  private confirm = inject(ConfirmService);

  constructor() {
    this.api.general().subscribe((g) => {
      this.setForm(g);
      if (g.timezone && !this.zones.includes(g.timezone)) this.zones = [g.timezone, ...this.zones];
    });
    if (this.auth.isAdmin()) {
      this.api.database().subscribe((d) => this.db.set(d));
      this.api.adminDefaults().subscribe((d) => this.defaults.set(d));
    }
    if (this.auth.isSuper()) this.api.server().subscribe((s) => this.server.set(s));
  }

  private setForm(g: GeneralSettings): void {
    this.form = { ...g };
    this.warnDays = g.expiry_warn_days.join(', ');
  }

  save(): void {
    const days = this.warnDays.split(',').map((d) => Number(d.trim())).filter((d) => d > 0);
    this.api.saveGeneral({
      ...this.form, refresh_sec: Number(this.form.refresh_sec), retention_value: Number(this.form.retention_value),
      down_after_checks: Number(this.form.down_after_checks), expiry_warn_days: days,
    }).subscribe({
      next: (g) => {
        this.setForm(g);
        this.notice.set('Saved.');
        this.auth.reload();
      },
      error: (e) => this.notice.set(errorText(e)),
    });
  }

  shrink(): void {
    this.dbNotice.set('Shrinking...');
    this.api.shrinkDatabase().subscribe({
      next: (d) => { this.db.set(d); this.dbNotice.set('Done.'); },
      error: (e) => this.dbNotice.set(errorText(e)),
    });
  }

  async clearStats(): Promise<void> {
    const ok = await this.confirm.ask({
      title: 'Clear all statistics?',
      message: 'All check results and incidents of this tenant are deleted.',
      details: ['Monitors, channels and settings are kept.', 'This cannot be undone.'],
      typeToConfirm: 'clear',
      action: 'Clear statistics',
    });
    if (!ok) return;
    this.api.clearStats().subscribe({
      next: (d) => { this.db.set(d); this.dbNotice.set('Statistics cleared.'); },
      error: (e) => this.dbNotice.set(errorText(e)),
    });
  }

  saveServer(s: ServerSettings): void {
    const body = { backup_enabled: s.backup_enabled, backup_keep_days: Number(s.backup_keep_days), dns_cache: s.dns_cache };
    this.api.saveServer(body).subscribe({
      next: (r) => { this.server.set(r); this.serverNotice.set('Saved.'); },
      error: (e) => this.serverNotice.set(errorText(e)),
    });
  }

  backupNow(): void {
    this.serverNotice.set('Backing up...');
    this.api.backupNow().subscribe({
      next: (r) => { this.server.set(r); this.serverNotice.set('Backup done.'); },
      error: (e) => this.serverNotice.set(errorText(e)),
    });
  }
}
