import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService, errorText } from '../core/api.service';
import { ExportData } from '../core/models';
import { MonitorStore } from '../core/monitor-store.service';
import { ThemeService } from '../core/theme.service';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [FormsModule],
  template: `
    <div class="page-head"><div><h1>Settings</h1><div class="sub">Appearance, access and backups.</div></div></div>

    <section class="card">
      <h2>Appearance</h2>
      <div class="row" style="margin-top: 12px">
        <button class="chip" [class.active]="theme.theme() === 'light'" (click)="theme.theme.set('light')">Light</button>
        <button class="chip" [class.active]="theme.theme() === 'dark'" (click)="theme.theme.set('dark')">Dark</button>
      </div>
    </section>

    <section class="card">
      <h2>API token</h2>
      <p class="muted">Only needed when the server runs with <span class="mono">API_TOKEN</span>. Stored in this browser only.</p>
      <div class="row">
        <input type="password" [(ngModel)]="token" placeholder="Token" style="max-width: 360px" />
        <button class="btn primary" (click)="saveToken()">Save</button>
        <button class="btn" (click)="clearToken()">Clear</button>
      </div>
    </section>

    <section class="card">
      <h2>Backup &amp; restore</h2>
      <p class="muted">Export monitors and alert channels to JSON. Import adds them to this server.</p>
      <div class="row">
        <button class="btn" (click)="exportConfig()">Export JSON</button>
        <label class="btn" style="flex-direction: row">Import JSON
          <input type="file" accept="application/json" (change)="importConfig($event)" hidden />
        </label>
      </div>
    </section>

    <section class="card">
      <h2>Observability</h2>
      <p class="muted">Prometheus scrape endpoint (send the API token as a bearer token if set):</p>
      <div class="code-box mono">{{ origin }}/api/metrics</div>
    </section>

    @if (notice()) { <p class="muted">{{ notice() }}</p> }
  `,
})
export class SettingsComponent {
  private api = inject(ApiService);
  private store = inject(MonitorStore);
  theme = inject(ThemeService);
  readonly origin = location.origin;
  token = localStorage.getItem('apiToken') ?? '';
  notice = signal('');

  saveToken(): void {
    localStorage.setItem('apiToken', this.token);
    this.notice.set('Token saved.');
    this.store.refresh();
  }

  clearToken(): void {
    localStorage.removeItem('apiToken');
    this.token = '';
    this.notice.set('Token cleared.');
  }

  exportConfig(): void {
    this.api.exportConfig().subscribe((data) => {
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = `myappsuptime-backup-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(url);
    });
  }

  importConfig(event: Event): void {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (!file) return;
    file.text().then((text) => {
      let data: ExportData;
      try {
        data = JSON.parse(text);
      } catch {
        this.notice.set('File is not valid JSON.');
        return;
      }
      this.api.importConfig(data).subscribe({
        next: (r) => {
          this.notice.set(`Imported ${r.monitors} monitors and ${r.channels} channels.`);
          this.store.refresh();
        },
        error: (e) => this.notice.set(errorText(e)),
      });
    });
  }
}
