import { Component, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { MonitorStore } from './core/monitor-store.service';
import { ThemeService } from './core/theme.service';
import { IconComponent } from './shared/icon.component';
import { LogoComponent } from './shared/logo.component';

@Component({
  selector: 'app-shell',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive, IconComponent, LogoComponent],
  template: `
    <div class="app" [class.nav-open]="navOpen()">
      <header class="mobile-bar">
        <button class="icon-btn" (click)="navOpen.set(true)" aria-label="Open menu"><app-icon name="menu" /></button>
        <a routerLink="/"><app-logo [size]="28" [tagline]="false" /></a>
        <span class="spacer"></span>
        <a routerLink="/monitors/new" class="icon-btn primary" aria-label="New monitor"><app-icon name="plus" /></a>
      </header>
      <div class="backdrop" (click)="navOpen.set(false)"></div>

      <aside class="side">
        <a routerLink="/" class="logo"><app-logo [size]="40" /></a>
        <div class="nav-caption">Monitoring</div>
        @for (item of nav; track item.path) {
          <a class="nav-link" [routerLink]="item.path" routerLinkActive="active" [title]="item.label"
             [routerLinkActiveOptions]="{ exact: item.path === '/' }">
            <app-icon [name]="item.icon" /> <span class="label">{{ item.label }}</span>
            @if (item.path === '/incidents' && (store.summary()?.unacknowledged ?? 0) > 0) {
              <span class="count">{{ store.summary()?.unacknowledged }}</span>
            }
          </a>
        }
        <div class="bottom">
          <a routerLink="/monitors/new" class="btn primary" title="New monitor"><app-icon name="plus" /> <span class="label">New monitor</span></a>
          <button class="btn" (click)="theme.toggle()" [title]="theme.theme() === 'light' ? 'Dark mode' : 'Light mode'">
            <app-icon [name]="theme.theme() === 'light' ? 'moon' : 'sun'" />
            <span class="label">{{ theme.theme() === 'light' ? 'Dark mode' : 'Light mode' }}</span>
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
  navOpen = signal(false);

  readonly nav = [
    { path: '/', label: 'Dashboard', icon: 'grid' },
    { path: '/incidents', label: 'Incidents', icon: 'alert' },
    { path: '/maintenance', label: 'Maintenance', icon: 'wrench' },
    { path: '/notifications', label: 'Alerting', icon: 'bell' },
    { path: '/status-pages', label: 'Status pages', icon: 'globe' },
    { path: '/settings', label: 'Settings', icon: 'cog' },
  ];

  constructor() {
    inject(Router)
      .events.pipe(filter((e) => e instanceof NavigationEnd), takeUntilDestroyed())
      .subscribe(() => this.navOpen.set(false));
  }
}
