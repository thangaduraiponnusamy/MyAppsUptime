import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService, errorText } from '../../core/api.service';
import { ConfirmService } from '../../core/confirm.service';
import { Channel, ChannelType, Project, SmtpSettings } from '../../core/models';

const TYPE_LABELS: Record<ChannelType, string> = {
  teams: 'Microsoft Teams', email: 'Email', telegram: 'Telegram', discord: 'Discord', googlechat: 'Google Chat',
  ntfy: 'ntfy (phone push)', slack: 'Slack / Mattermost / Rocket.Chat', webhook: 'Webhook',
};

const URL_HELP: Partial<Record<ChannelType, [string, string, string]>> = {
  teams: ['Teams workflow / incoming webhook URL', 'https://...',
    'In Teams: channel > Workflows > "Post to a channel when a webhook request is received". Alerts arrive as cards.'],
  discord: ['Discord webhook URL', 'https://discord.com/api/webhooks/...',
    'In Discord: Server settings > Integrations > Webhooks > New webhook > Copy webhook URL.'],
  googlechat: ['Google Chat webhook URL', 'https://chat.googleapis.com/v1/spaces/...',
    'In Google Chat: space name > Apps & integrations > Webhooks > Add webhook, copy the URL.'],
  ntfy: ['ntfy server and topic', 'https://ntfy.sh/my-team-alerts',
    'Install the ntfy app and subscribe to the same topic. Use a long, hard-to-guess topic name: anyone who knows it can read the alerts.'],
  slack: ['Incoming webhook URL', 'https://hooks.slack.com/services/...', 'Also works with Mattermost and Rocket.Chat incoming webhooks.'],
};

const TG_PREFIX = 'https://api.telegram.org/bot';

const emptyChannel = (): Channel => ({
  name: '', type: 'teams', url: '', recipients: [], tags: [], project_ids: [], active: true,
});

@Component({
  selector: 'app-settings-notifications',
  standalone: true,
  imports: [FormsModule],
  template: `
    <h2 class="s-title">Notifications</h2>
    <p class="muted">Alerts go out when a monitor goes down, becomes degraded or recovers.
      Members of a team also get emails for that team (see Teams).</p>

    <section class="s-section">
      <div class="s-head">
        <h3>Alert channels</h3>
        @if (!editing()) { <button class="btn primary sm" (click)="edit(null)">Add channel</button> }
      </div>

      @if (editing(); as c) {
        <form class="s-fields s-box" (ngSubmit)="saveChannel()">
          <label>Name <input name="name" [(ngModel)]="c.name" placeholder="Team 1 on-call" required /></label>
          <label>Type
            <select name="type" [(ngModel)]="c.type">
              @for (t of types; track t) { <option [value]="t">{{ typeLabels[t] }}</option> }
            </select>
          </label>
          @if (c.type === 'email') {
            <label class="full">Email addresses (comma separated)
              <input name="rcpt" [(ngModel)]="recipientsText" placeholder="ops@example.com, lead@example.com" />
            </label>
          } @else if (c.type === 'telegram') {
            <label>Bot token
              <input name="tgToken" type="password" [(ngModel)]="tgToken" placeholder="123456789:AA..." autocomplete="off" />
            </label>
            <label>Chat ID
              <input name="tgChat" [(ngModel)]="tgChat" placeholder="-1001234567890 or @mychannel" />
            </label>
            <small class="muted full">1. In Telegram, message &#64;BotFather, send /newbot and copy the token.
              2. Add the bot to your group or channel (as admin for channels) and send it a message.
              3. Open https://api.telegram.org/bot&lt;token&gt;/getUpdates and copy "chat":{{ '{' }}"id": ...{{ '}' }}.</small>
          } @else {
            <label class="full">{{ urlHelp[c.type]?.[0] ?? 'Webhook URL' }}
              <input name="url" [(ngModel)]="c.url" [placeholder]="urlHelp[c.type]?.[1] ?? 'https://...'" />
              @if (urlHelp[c.type]; as h) {
                <small class="muted">{{ h[2] }}</small>
              }
            </label>
          }
          <div class="full">
            <span class="field-label">Teams</span>
            <div class="check-list">
              @for (p of projects(); track p.id) {
                <label class="check"><input type="checkbox" [checked]="c.project_ids.includes(p.id!)" (change)="toggleProject(c, p.id!)" /> {{ p.name }}</label>
              } @empty { <span class="muted small">No teams yet.</span> }
            </div>
            <small class="muted">None selected = all projects.</small>
          </div>
          <label class="full">Only for tags (optional, comma separated)
            <input name="tags" [(ngModel)]="tagsText" placeholder="prod, critical" />
          </label>
          <label class="check full"><input type="checkbox" name="active" [(ngModel)]="c.active" /> Active</label>
          @if (error()) { <p class="error full">{{ error() }}</p> }
          <div class="row full">
            <button class="btn primary" [disabled]="!c.name">Save</button>
            <button type="button" class="btn" (click)="editing.set(null)">Cancel</button>
          </div>
        </form>
      }

      <table>
        <thead><tr><th>Name</th><th>Type</th><th>Sends to</th><th class="hide-sm">Teams</th><th></th></tr></thead>
        <tbody>
          @for (c of channels(); track c.id) {
            <tr [class.muted]="!c.active">
              <td><b>{{ c.name }}</b> @if (!c.active) { <span class="tag">off</span> }</td>
              <td><span class="type-badge">{{ typeLabels[c.type] }}</span></td>
              <td class="ellipsis">{{ sendsTo(c) }}</td>
              <td class="hide-sm">
                @for (n of projectNames(c); track n) { <span class="tag">{{ n }}</span> } @empty { <span class="muted">All</span> }
                @for (t of c.tags; track t) { <span class="tag">#{{ t }}</span> }
              </td>
              <td class="row nowrap">
                <button class="btn sm" (click)="test(c)">Test</button>
                <button class="btn sm" (click)="edit(c)">Edit</button>
                <button class="btn sm danger" (click)="remove(c)">Delete</button>
              </td>
            </tr>
          } @empty {
            <tr><td colspan="5" class="muted">No channels yet. Add Teams or email to get alerts.</td></tr>
          }
        </tbody>
      </table>
      @if (notice()) { <p class="muted">{{ notice() }}</p> }
    </section>

    <section class="s-section">
      <h3>Email server (SMTP)</h3>
      <p class="muted">Needed for email alerts and password reset emails. For Office 365 use smtp.office365.com, port 587, STARTTLS.</p>
      <form class="s-fields" (ngSubmit)="saveSmtp()">
        <label>Host <input name="host" [(ngModel)]="smtp.host" placeholder="smtp.office365.com" /></label>
        <label>Port <input name="port" type="number" [(ngModel)]="smtp.port" /></label>
        <label>Security
          <select name="sec" [(ngModel)]="smtp.security">
            <option value="starttls">STARTTLS</option><option value="ssl">SSL/TLS</option><option value="none">None</option>
          </select>
        </label>
        <label>From address <input name="from" [(ngModel)]="smtp.from_addr" placeholder="uptime@example.com" /></label>
        <label>User name <input name="user" [(ngModel)]="smtp.username" autocomplete="off" /></label>
        <label>Password
          <input name="pw" type="password" [(ngModel)]="smtpPassword" autocomplete="new-password"
                 [placeholder]="smtp.has_password ? 'Saved - leave empty to keep' : ''" />
        </label>
        <div class="row full">
          <button class="btn primary">Save</button>
          <input name="to" [(ngModel)]="testTo" placeholder="Send test to..." style="max-width: 240px" />
          <button type="button" class="btn" [disabled]="!testTo" (click)="testSmtp()">Send test email</button>
        </div>
        @if (smtpNotice()) { <p class="muted full">{{ smtpNotice() }}</p> }
      </form>
    </section>
  `,
})
export class NotificationSettingsComponent {
  private api = inject(ApiService);
  private confirm = inject(ConfirmService);
  readonly types: ChannelType[] = ['teams', 'email', 'telegram', 'discord', 'googlechat', 'ntfy', 'slack', 'webhook'];
  readonly typeLabels = TYPE_LABELS;
  readonly urlHelp = URL_HELP;
  channels = signal<Channel[]>([]);
  projects = signal<Project[]>([]);
  editing = signal<Channel | null>(null);
  recipientsText = '';
  tagsText = '';
  tgToken = '';
  tgChat = '';
  error = signal('');
  notice = signal('');
  smtp: SmtpSettings = { host: '', port: 587, security: 'starttls', username: '', from_addr: '' };
  smtpPassword = '';
  testTo = '';
  smtpNotice = signal('');
  private projectById = computed(() => new Map(this.projects().map((p) => [p.id!, p.name])));

  constructor() {
    this.load();
    this.api.projects().subscribe((p) => this.projects.set(p));
    this.api.smtp().subscribe((s) => (this.smtp = s));
  }

  load(): void {
    this.api.channels().subscribe((list) => this.channels.set(list));
  }

  projectNames(c: Channel): string[] {
    return c.project_ids.map((id) => this.projectById().get(id) ?? `#${id}`);
  }

  host(url: string): string {
    try {
      return new URL(url).host;
    } catch {
      return url;
    }
  }

  sendsTo(c: Channel): string {
    if (c.type === 'email') return c.recipients.join(', ');
    if (c.type === 'telegram') return `Chat ${c.url.split('?chat_id=')[1] ?? ''}`;
    if (c.type === 'ntfy') return c.url.replace(/^https?:\/\//, '');
    return this.host(c.url);
  }

  edit(c: Channel | null): void {
    const copy = c ? { ...c, project_ids: [...c.project_ids] } : emptyChannel();
    this.recipientsText = copy.recipients.join(', ');
    this.tagsText = copy.tags.join(', ');
    const tg = copy.type === 'telegram' ? copy.url.slice(TG_PREFIX.length).split('/sendMessage?chat_id=') : [];
    this.tgToken = tg[0] ?? '';
    this.tgChat = tg[1] ?? '';
    this.error.set('');
    this.editing.set(copy);
  }

  toggleProject(c: Channel, id: number): void {
    c.project_ids = c.project_ids.includes(id) ? c.project_ids.filter((p) => p !== id) : [...c.project_ids, id];
  }

  saveChannel(): void {
    const c = this.editing()!;
    const split = (s: string) => s.split(',').map((t) => t.trim()).filter(Boolean);
    const body: Channel = { ...c, recipients: split(this.recipientsText), tags: split(this.tagsText) };
    if (c.type === 'telegram') {
      body.url = `${TG_PREFIX}${this.tgToken.trim()}/sendMessage?chat_id=${encodeURIComponent(this.tgChat.trim()).replace('%40', '@')}`;
    }
    const call = c.id ? this.api.updateChannel(c.id, body) : this.api.createChannel(body);
    call.subscribe({
      next: () => {
        this.editing.set(null);
        this.load();
      },
      error: (e) => this.error.set(errorText(e)),
    });
  }

  test(c: Channel): void {
    this.notice.set(`Sending test to ${c.name}...`);
    this.api.testChannel(c.id!).subscribe({
      next: () => this.notice.set(`Test sent to ${c.name}.`),
      error: (e) => this.notice.set(errorText(e)),
    });
  }

  async remove(c: Channel): Promise<void> {
    const ok = await this.confirm.ask({
      title: 'Delete notification channel?',
      message: `You are about to delete the channel "${c.name}".`,
      details: ['Alerts will no longer be sent through this channel.'],
      typeToConfirm: c.name,
      action: 'Delete channel',
    });
    if (ok) this.api.deleteChannel(c.id!).subscribe(() => this.load());
  }

  saveSmtp(): void {
    const body = { ...this.smtp, port: Number(this.smtp.port), password: this.smtpPassword || null };
    delete body.has_password;
    this.api.saveSmtp(body).subscribe({
      next: (s) => {
        this.smtp = s;
        this.smtpPassword = '';
        this.smtpNotice.set('Saved.');
      },
      error: (e) => this.smtpNotice.set(errorText(e)),
    });
  }

  testSmtp(): void {
    this.smtpNotice.set('Sending...');
    this.api.testSmtp(this.testTo.trim()).subscribe({
      next: () => this.smtpNotice.set(`Test email sent to ${this.testTo}.`),
      error: (e) => this.smtpNotice.set(errorText(e)),
    });
  }
}
