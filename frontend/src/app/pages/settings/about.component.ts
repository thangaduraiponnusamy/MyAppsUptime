import { Component, inject, signal } from '@angular/core';
import { APP_NAME, REPO_URL, UI_VERSION } from '../../core/app-info';
import { AuthService } from '../../core/auth.service';
import { LogoComponent } from '../../shared/logo.component';

@Component({
  selector: 'app-settings-about',
  standalone: true,
  imports: [LogoComponent],
  template: `
    <h2 class="s-title">About</h2>
    <div class="about">
      <app-logo [size]="96" [wordmark]="false" />
      <h3>{{ appName }}</h3>
      <div>Version {{ serverVersion() || '...' }}</div>
      <div class="muted small">Frontend version {{ uiVersion }}</div>
      @if (repoUrl) {
        <a class="link" [href]="repoUrl" target="_blank" rel="noopener noreferrer">View on GitHub</a>
      } @else {
        <span class="muted small">GitHub link coming soon</span>
      }
      <p class="muted small about-note">Self-hosted uptime monitoring with tenants, projects, users and alerts by Teams, email, Telegram, Discord, Google Chat, ntfy, Slack or webhook.</p>
    </div>
  `,
})
export class AboutSettingsComponent {
  readonly appName = APP_NAME;
  readonly uiVersion = UI_VERSION;
  readonly repoUrl = REPO_URL;
  serverVersion = signal('');

  constructor() {
    inject(AuthService).options().subscribe((o) => this.serverVersion.set(o.version));
  }
}
