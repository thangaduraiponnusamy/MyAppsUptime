import { Component, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { MonitorStore } from './core/monitor-store.service';
import { ThemeService } from './core/theme.service';
import { IconComponent } from './shared/icon.component';
import { LogoComponent } from './shared/logo.component';

@Component({
  selector: 'app-shell',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive, IconComponent, LogoComponent],
  template: `
    <div class="app">
      <aside class="side">
        <a routerLink="/" class="logo"><app-logo [size]="40" /></a>
        @for (item of nav; track item.path) {
          <a class="nav-link" [routerLink]="item.path" routerLinkActive="active"
             [routerLinkActiveOptions]="{ exact: item.path === '/' }">
            <app-icon [name]="item.icon" /> {{ item.label }}
            @if (item.path === '/incidents' && (store.summary()?.unacknowledged ?? 0) > 0) {
              <span class="count">{{ store.summary()?.unacknowledged }}</span>
            }
          </a>
        }
        <div class="bottom">
          <a routerLink="/monitors/new" class="btn primary"><app-icon name="plus" /> New monitor</a>
          <button class="btn" (click)="theme.toggle()">
            <app-icon [name]="theme.theme() === 'light' ? 'moon' : 'sun'" />
            {{ theme.theme() === 'light' ? 'Dark mode' : 'Light mode' }}
          </button>
        </div>
      </aside>
      <main class="main"><router-outlet /></main>
    </div>
  `,
})
export class ShellComponent {
  store = inject(MonitorStore);
  theme = inject(ThemeService);

  readonly nav = [
    { path: '/', label: 'Overview', icon: 'grid' },
    { path: '/incidents', label: 'Incidents', icon: 'alert' },
    { path: '/maintenance', label: 'Maintenance', icon: 'wrench' },
    { path: '/notifications', label: 'Alerting', icon: 'bell' },
    { path: '/status-pages', label: 'Status pages', icon: 'globe' },
    { path: '/settings', label: 'Settings', icon: 'cog' },
  ];
}
