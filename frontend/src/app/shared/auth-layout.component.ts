import { Component, input } from '@angular/core';
import { APP_NAME } from '../core/app-info';
import { IconComponent } from './icon.component';
import { LogoComponent } from './logo.component';

/** Two-column frame shared by sign-in, reset and change-password pages. */
@Component({
  selector: 'app-auth-layout',
  standalone: true,
  imports: [LogoComponent, IconComponent],
  template: `
    <div class="auth-page">
      <aside class="auth-brand">
        <app-logo [size]="44" [tagline]="false" />
        <div class="auth-pitch">
          <h1>Know the moment<br />something goes down.</h1>
          <p>One place to watch your apps, sites and devices, grouped by team, with alerts to the right people.</p>
          <ul>
            <li><app-icon name="check-circle" [size]="18" /> HTTP, TCP, ping, DNS, SSL and push checks</li>
            <li><app-icon name="check-circle" [size]="18" /> Alerts by Microsoft Teams, email, Slack or webhook</li>
            <li><app-icon name="check-circle" [size]="18" /> Tenants and teams with their own members and alerts</li>
          </ul>
        </div>
        <small>&copy; {{ year }} {{ appName }}</small>
      </aside>
      <main class="auth-main">
        <div class="auth-card">
          <div class="auth-logo-sm"><app-logo [size]="36" [tagline]="false" /></div>
          <h2>{{ heading() }}</h2>
          @if (sub()) { <p class="muted">{{ sub() }}</p> }
          <ng-content />
        </div>
      </main>
    </div>
  `,
})
export class AuthLayoutComponent {
  heading = input.required<string>();
  sub = input('');
  readonly appName = APP_NAME;
  readonly year = new Date().getFullYear();
}
