import { DatePipe } from '@angular/common';
import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService, errorText } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { ConfirmService } from '../../core/confirm.service';
import { Probe } from '../../core/models';
import { MonitorStore } from '../../core/monitor-store.service';
import { IconComponent } from '../../shared/icon.component';
import { short } from '../../shared/time';

const IMAGE = 'ghcr.io/thangaduraiponnusamy/myappsuptime-probe:latest';

@Component({
  selector: 'app-settings-locations',
  standalone: true,
  imports: [DatePipe, FormsModule, IconComponent],
  template: `
    <h2 class="s-title">Private locations</h2>
    <p class="muted">Check devices on another network (home, office, lab). A small probe runs there and sends the results here.
      Pick the location in a monitor under "Run from".</p>

    <section class="s-section">
      <div class="s-head">
        <h3>Locations <span class="count-badge">{{ probes().length }}</span></h3>
        <button class="btn sm" (click)="load()">Refresh</button>
      </div>
      <form class="row s-box" (ngSubmit)="add()">
        <input name="name" [(ngModel)]="name" placeholder="home-chennai" maxlength="60" />
        <button class="btn primary sm" [disabled]="!name.trim()">Add location</button>
      </form>
      @if (error()) { <p class="error">{{ error() }}</p> }

      @if (created(); as p) {
        <div class="s-box">
          <p><b>Start the probe for "{{ p.name }}"</b> on a machine in that network. The token is shown only now.</p>
          <div class="code-box mono" style="white-space: pre-wrap">{{ dockerCmd(p) }}
            <span class="spacer"></span>
            <button class="btn sm" (click)="copy(dockerCmd(p))"><app-icon name="copy" [size]="14" /></button>
          </div>
          <p class="muted small">Docker container monitors: add <span class="mono">-v /var/run/docker.sock:/var/run/docker.sock:ro --group-add $(stat -c %g /var/run/docker.sock)</span>.
            Without Docker: <span class="mono">pip install -r probe/requirements.txt</span>, then <span class="mono">SERVER_URL=... PROBE_TOKEN=... python -m probe</span> in the repository folder.</p>
          @if (notice()) { <p class="muted small">{{ notice() }}</p> }
          <button class="btn sm" (click)="created.set(null)">Done</button>
        </div>
      }

      @if (hasOutdated()) {
        <div class="s-box">
          <p><b>A newer probe image is available</b> (version {{ serverVersion() }}). On each outdated machine run:</p>
          <div class="code-box mono" style="white-space: pre-wrap">{{ updateCmd }}
            <span class="spacer"></span>
            <button class="btn sm" (click)="copy(updateCmd)"><app-icon name="copy" [size]="14" /></button>
          </div>
          <p class="muted small">Then start it again with the same docker run command and token. Lost the token? Use "New token".</p>
        </div>
      }

      <table>
        <thead><tr><th>Location</th><th>Status</th><th>For</th><th>Monitors</th><th class="hide-sm">Last contact</th><th class="hide-sm">IP address</th><th class="hide-sm">Version</th><th></th></tr></thead>
        <tbody>
          @for (p of probes(); track p.id) {
            <tr>
              <td><b>{{ p.name }}</b></td>
              <td><span [class]="'pill ' + (p.online ? 'up' : p.last_seen_at ? 'down' : 'pending')">
                {{ p.online ? 'online' : p.last_seen_at ? 'offline' : 'waiting' }}</span></td>
              <td class="num" [title]="sinceTitle(p)">{{ since(p) }}</td>
              <td>{{ p.monitors }}</td>
              <td class="hide-sm">{{ p.last_seen_at ? (p.last_seen_at | date: 'MMM d, HH:mm:ss') : 'never' }}</td>
              <td class="hide-sm mono">{{ p.last_ip || '-' }}</td>
              <td class="hide-sm">{{ p.version || '-' }}
                @if (outdated(p)) { <span class="pill degraded" [title]="'Latest is ' + serverVersion()">update available</span> }</td>
              <td class="row nowrap">
                <button class="btn sm" (click)="newToken(p)">New token</button>
                <button class="btn sm danger" (click)="remove(p)">Delete</button>
              </td>
            </tr>
          } @empty {
            <tr><td colspan="8" class="empty">No private locations yet.</td></tr>
          }
        </tbody>
      </table>
    </section>
  `,
})
export class LocationSettingsComponent {
  private api = inject(ApiService);
  private store = inject(MonitorStore);
  private confirm = inject(ConfirmService);
  probes = signal<Probe[]>([]);
  created = signal<Probe | null>(null);
  error = signal('');
  notice = signal('');
  name = '';
  serverVersion = signal('');
  readonly updateCmd = `docker pull ${IMAGE}\ndocker rm -f myappsuptime-probe`;
  hasOutdated = computed(() => this.probes().some((p) => this.outdated(p)));

  constructor() {
    this.load();
    inject(AuthService).options().subscribe((o) => this.serverVersion.set(o.version));
  }

  outdated(p: Probe): boolean {
    const s = this.serverVersion();
    if (!p.version || !s) return false;
    const a = p.version.split('.').map(Number), b = s.split('.').map(Number);
    for (let i = 0; i < 3; i++) {
      if ((a[i] || 0) !== (b[i] || 0)) return (a[i] || 0) < (b[i] || 0);
    }
    return false;
  }

  private sinceIso(p: Probe): string | null {
    return p.online ? p.online_since : p.last_seen_at;
  }

  since(p: Probe): string {
    const iso = this.sinceIso(p);
    return iso ? short(iso) : '-';
  }

  sinceTitle(p: Probe): string {
    const iso = this.sinceIso(p);
    if (!iso) return '';
    return `${p.online ? 'Online' : 'Offline'} since ${new Date(iso).toLocaleString()}`;
  }

  load(): void {
    this.api.probes().subscribe((p) => this.probes.set(p));
  }

  dockerCmd(p: Probe): string {
    return `docker run -d --name myappsuptime-probe --restart unless-stopped --network host \\\n` +
      `  -e SERVER_URL=${location.origin} -e PROBE_TOKEN=${p.token} \\\n  ${IMAGE}`;
  }

  copy(text: string): void {
    navigator.clipboard.writeText(text).then(() => this.notice.set('Copied to clipboard.'));
  }

  add(): void {
    this.error.set('');
    this.api.createProbe(this.name.trim()).subscribe({
      next: (p) => {
        this.name = '';
        this.notice.set('');
        this.created.set(p);
        this.load();
      },
      error: (e) => this.error.set(errorText(e)),
    });
  }

  async newToken(p: Probe): Promise<void> {
    const ok = await this.confirm.ask({
      title: 'Create a new token?',
      message: `The probe at "${p.name}" stops working until you start it with the new token.`,
      action: 'New token',
    });
    if (!ok) return;
    this.api.probeToken(p.id).subscribe((fresh) => {
      this.notice.set('');
      this.created.set(fresh);
    });
  }

  async remove(p: Probe): Promise<void> {
    const ok = await this.confirm.ask({
      title: 'Delete private location?',
      message: `You are about to delete "${p.name}".`,
      details: [`${p.monitors} ${p.monitors === 1 ? 'monitor moves' : 'monitors move'} back to this server.`],
      typeToConfirm: p.name,
      action: 'Delete location',
    });
    if (!ok) return;
    this.api.deleteProbe(p.id).subscribe(() => {
      this.load();
      this.store.refresh();
    });
  }
}
