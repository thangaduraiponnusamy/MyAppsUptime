import { Component, computed, effect, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiService, errorText } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { LoginEvent, ROLE_LABEL, ServerSettings, SessionInfo } from '../../core/models';
import { ConfirmService } from '../../core/confirm.service';
import { PrefsService } from '../../core/prefs.service';
import { IconComponent } from '../../shared/icon.component';
import { MultiSelectComponent } from '../../shared/multi-select.component';

@Component({
  selector: 'app-settings-security',
  standalone: true,
  imports: [FormsModule, DatePipe, RouterLink, IconComponent, MultiSelectComponent],
  template: `
    <h2 class="s-title">Security</h2>

    <section class="s-section">
      <h3>My account</h3>
      <dl class="s-facts">
        <dt>User name</dt><dd>{{ auth.user()?.username }}</dd>
        <dt>Name</dt><dd>{{ auth.user()?.name || '-' }}</dd>
        <dt>Email</dt><dd>{{ auth.user()?.email || '-' }}</dd>
        <dt>Role</dt><dd>{{ roleLabel[auth.user()?.role ?? ''] }}</dd>
      </dl>
    </section>

    <section class="s-section">
      <h3>My alerts</h3>
      <div class="row">
        <app-multi-select class="grow" [options]="alertOptions()" [picked]="myAlerts()" (pickedChange)="saveAlerts($event)" placeholder="No alerts" />
        @if (alertsNotice()) { <span class="muted small">{{ alertsNotice() }}</span> }
      </div>
    </section>

    @if (auth.isAdmin()) {
    <section class="s-section">
      <h3>Sign-in history</h3>
      <p class="muted">Sign-ins of all users are in the <a class="link" routerLink="/settings/audit">Audit log</a> (type "Sign-ins &amp; account").</p>
    </section>
    } @else {
    <section class="s-section">
      <h3>My sign-ins</h3>
      <p class="muted">Your last {{ prefs.limits().logins }} sign-ins (change in General).</p>
      @if (logins().length) {
        <div class="table-wrap"><table>
          <thead><tr><th>Time</th><th>Device</th><th class="hide-sm">IP address</th><th>Result</th></tr></thead>
          <tbody>
            @for (l of logins(); track l.id) {
              <tr>
                <td>{{ l.created_at | date: 'MMM d, y, HH:mm:ss' }}</td>
                <td>@if (l.device) {
                  <span class="device"><app-icon [name]="isPhone(l.device) ? 'phone' : 'monitor'" [size]="15" />{{ l.device }}</span>
                } @else { - }</td>
                <td class="hide-sm mono">{{ l.ip || '-' }}</td>
                <td><span class="pill" [class.up]="l.success" [class.down]="!l.success">{{ l.success ? 'Success' : 'Failed' }}</span></td>
              </tr>
            }
          </tbody>
        </table></div>
      } @else {
        <p class="muted">No sign-ins yet.</p>
      }
    </section>
    }

    <section class="s-section">
      <h3>Active sessions</h3>
      <p class="muted">Browsers where you are signed in.</p>
      @if (sessions().length) {
        <div class="table-wrap"><table>
          <thead><tr><th>Device</th><th class="hide-sm">IP address</th><th>Signed in</th><th>Last used</th><th></th></tr></thead>
          <tbody>
            @for (s of sessions(); track s.id) {
              <tr>
                <td><span class="device"><app-icon [name]="isPhone(s.device) ? 'phone' : 'monitor'" [size]="15" />{{ s.device || 'Unknown' }}</span>
                  @if (s.current) { <span class="pill up">This browser</span> }</td>
                <td class="hide-sm mono">{{ s.ip || '-' }}</td>
                <td class="nowrap">{{ s.created_at | date: 'MMM d, y, HH:mm' }}</td>
                <td class="nowrap">{{ s.last_used_at | date: 'MMM d, y, HH:mm' }}</td>
                <td>@if (!s.current) { <button class="btn sm" (click)="revoke(s)">Revoke</button> }</td>
              </tr>
            }
          </tbody>
        </table></div>
        @if (sessions().length > 1) {
          <button class="btn sm" (click)="revokeOthers()">Sign out all other sessions</button>
        }
      } @else {
        <p class="muted">No sessions (signed in with an API token).</p>
      }
      @if (sessionNotice()) { <span class="muted small">{{ sessionNotice() }}</span> }
    </section>

    @if (auth.isSuper()) {
      <section class="s-section">
        <h3>Monitor targets</h3>
        <p class="muted">Checks run by this server cannot reach private, local or cloud metadata addresses.
          Private locations are not limited.</p>
        <form class="s-fields" (ngSubmit)="saveTargets()">
          <label class="check full"><input type="checkbox" name="ap" [(ngModel)]="allowPrivate" /> Allow private targets</label>
          <label class="full">Always allowed networks (CIDR, one per line)
            <textarea name="cidrs" rows="3" [(ngModel)]="cidrs" placeholder="10.20.0.0/16"></textarea></label>
          <div class="row full">
            <button class="btn primary">Save</button>
            @if (targetNotice()) { <span class="muted">{{ targetNotice() }}</span> }
          </div>
        </form>
      </section>
    }

    <section class="s-section">
      <h3>Change password</h3>
      <form class="s-fields" (ngSubmit)="change()">
        <label>Current password <input name="c" type="password" [(ngModel)]="current" autocomplete="current-password" /></label>
        <label>New password <input name="n" type="password" [(ngModel)]="next" autocomplete="new-password" />
          <small class="muted">At least 8 characters.</small></label>
        <label>Repeat new password <input name="r" type="password" [(ngModel)]="repeat" autocomplete="new-password" /></label>
        <div class="row full">
          <button class="btn primary" [disabled]="!current || next.length < 8 || next !== repeat">Change password</button>
          @if (notice()) { <span class="muted">{{ notice() }}</span> }
        </div>
      </form>
    </section>

    <section class="s-section">
      <h3>Forgot password</h3>
      @if (auth.isAdmin()) {
        <p class="muted">Use "Forgot password?" on the sign-in page to get a reset link by email
          (needs the email server in Notifications and the app address in General).
          An admin can also set a new password in Users.
          If every admin is locked out, run this on the server:</p>
        <div class="code-box mono">python -m app.reset_password admin NEW_PASSWORD</div>
      } @else {
        <p class="muted">Use "Forgot password?" on the sign-in page to get a reset link by email, or ask an admin to set a new password for you.</p>
      }
    </section>

    <section class="s-section">
      <h3>API access</h3>
      <p class="muted">Scripts and Prometheus can use a fixed token: start the server with
        <span class="mono">API_TOKEN</span> set and send it as <span class="mono">Authorization: Bearer ...</span>.
        It has admin rights, so keep it secret.</p>
    </section>

  `,
})
export class SecuritySettingsComponent {
  auth = inject(AuthService);
  readonly roleLabel = ROLE_LABEL;
  prefs = inject(PrefsService);
  private api = inject(ApiService);
  private confirm = inject(ConfirmService);
  logins = signal<LoginEvent[]>([]);
  sessions = signal<SessionInfo[]>([]);
  sessionNotice = signal('');
  private server: ServerSettings | null = null;
  allowPrivate = false;
  cidrs = '';
  targetNotice = signal('');
  current = '';
  next = '';
  repeat = '';
  notice = signal('');
  alertOptions = computed(() => (this.auth.user()?.alert_options ?? []).map((o) => ({ id: o.id, label: o.name })));
  myAlerts = computed(() => new Set<number>(this.auth.user()?.alert_channels ?? this.alertOptions().map((o) => o.id)));
  alertsNotice = signal('');

  constructor() {
    effect(() => {
      if (this.auth.isAdmin()) return;
      this.api.logins(this.prefs.limits().logins).subscribe((l) => this.logins.set(l));
    });
    this.loadSessions();
    if (this.auth.isSuper()) {
      this.api.server().subscribe((s) => {
        this.server = s;
        this.allowPrivate = !!s.allow_private_targets;
        this.cidrs = (s.allowed_cidrs ?? []).join('\n');
      });
    }
  }

  loadSessions(): void {
    this.api.sessions().subscribe((s) => this.sessions.set(s));
  }

  async revoke(s: SessionInfo): Promise<void> {
    const ok = await this.confirm.ask({ title: 'Revoke session', message: `Sign out ${s.device || 'this session'}?`, action: 'Revoke' });
    if (!ok) return;
    this.api.revokeSession(s.id).subscribe({
      next: () => this.loadSessions(),
      error: (e) => this.sessionNotice.set(errorText(e)),
    });
  }

  async revokeOthers(): Promise<void> {
    const ok = await this.confirm.ask({ title: 'Sign out other sessions', message: 'Sign out everywhere except this browser?', action: 'Sign out' });
    if (!ok) return;
    this.api.revokeOtherSessions().subscribe({
      next: (r) => { this.sessionNotice.set(`${r.revoked} signed out.`); this.loadSessions(); },
      error: (e) => this.sessionNotice.set(errorText(e)),
    });
  }

  saveTargets(): void {
    if (!this.server) return;
    const { backup_dir, backups, ...rest } = this.server;
    const body: ServerSettings = { ...rest, allow_private_targets: this.allowPrivate,
      allowed_cidrs: this.cidrs.split(/[\s,]+/).filter(Boolean) };
    this.api.saveServer(body).subscribe({
      next: (s) => {
        this.server = s;
        this.cidrs = (s.allowed_cidrs ?? []).join('\n');
        this.targetNotice.set('Saved.');
      },
      error: (e) => this.targetNotice.set(errorText(e)),
    });
  }

  isPhone(device: string): boolean {
    return /Android|iPhone|iPad/.test(device);
  }

  saveAlerts(picked: Set<number>): void {
    this.auth.setAlerts([...picked]).subscribe({
      next: () => this.alertsNotice.set('Saved.'),
      error: (e) => this.alertsNotice.set(errorText(e)),
    });
  }

  change(): void {
    this.auth.changePassword(this.current, this.next).subscribe({
      next: () => {
        this.current = this.next = this.repeat = '';
        this.notice.set('Password changed.');
      },
      error: (e) => this.notice.set(errorText(e)),
    });
  }
}
