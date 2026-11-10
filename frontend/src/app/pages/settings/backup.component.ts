import { Component, inject, signal } from '@angular/core';
import { ApiService, errorText } from '../../core/api.service';
import { ExportData } from '../../core/models';
import { MonitorStore } from '../../core/monitor-store.service';

@Component({
  selector: 'app-settings-backup',
  standalone: true,
  template: `
    <h2 class="s-title">Backup</h2>

    <section class="s-section">
      <h3>Export and import</h3>
      <p class="muted">Export saves monitors, teams and alert channels to a JSON file.
        Import adds them to this server. Users and passwords are not included.</p>
      <div class="row">
        <button class="btn" (click)="exportConfig()">Export JSON</button>
        <label class="btn" style="flex-direction: row">Import JSON
          <input type="file" accept="application/json" (change)="importConfig($event)" hidden />
        </label>
      </div>
      @if (notice()) { <p class="muted">{{ notice() }}</p> }
    </section>

    <section class="s-section">
      <h3>Prometheus</h3>
      <p class="muted">Scrape endpoint. Send the API token as a bearer token.</p>
      <div class="code-box mono">{{ origin }}/api/metrics</div>
    </section>
  `,
})
export class BackupSettingsComponent {
  private api = inject(ApiService);
  private store = inject(MonitorStore);
  readonly origin = location.origin;
  notice = signal('');

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
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    file.text().then((text) => {
      input.value = '';
      let data: ExportData;
      try {
        data = JSON.parse(text);
      } catch {
        this.notice.set('File is not valid JSON.');
        return;
      }
      this.api.importConfig(data).subscribe({
        next: (r) => {
          this.notice.set(`Imported ${r.monitors} monitors, ${r.projects} teams and ${r.channels} channels.`);
          this.store.refresh();
        },
        error: (e) => this.notice.set(errorText(e)),
      });
    });
  }
}
