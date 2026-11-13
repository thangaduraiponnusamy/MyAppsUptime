import { Component, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { IsActiveMatchOptions, NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { ApiService } from './core/api.service';
import { AuthService } from './core/auth.service';
import { Tenant } from './core/models';
import { MonitorStore } from './core/monitor-store.service';
import { ThemeService } from './core/theme.service';
import { IconComponent } from './shared/icon.component';
import { LogoComponent } from './shared/logo.component';

@Component({
  selector: 'app-shell',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive, IconComponent, LogoComponent],
  template: `
    <div class="app" [class.nav-open]="navOpen()" [class.collapsed]="collapsed()">
      <header class="mobile-bar">
        <button class="icon-btn" (click)="navOpen.set(true)" aria-label="Open menu"><app-icon name="menu" /></button>
        <a routerLink="/"><app-logo [size]="28" [tagline]="false" /></a>
        <span class="spacer"></span>
        @if (auth.isAdmin()) {
          <a routerLink="/monitors/new" class="icon-btn primary" aria-label="New monitor"><app-icon name="plus" /></a>
        }
      </header>
      <div class="backdrop" (click)="navOpen.set(false)"></div>

      <aside class="side">
        <div class="side-head">
          <a routerLink="/" class="logo"><app-logo [size]="34" /></a>
          <button class="collapse-btn" (click)="toggleCollapsed()" [title]="collapsed() ? 'Expand menu' : 'Collapse menu'">
            <app-icon [name]="collapsed() ? 'chevrons-right' : 'chevrons-left'" [size]="18" />
          </button>
        </div>
        <div class="tenant-box" [class.visiting]="auth.visiting()" [title]="'Tenant: ' + (auth.user()?.active_tenant_name ?? '')">
          <app-icon name="globe" [size]="16" />
          @if (auth.isSuper() && tenants().length > 1) {
            <select class="label" (change)="auth.switchTenant($any($event.target).value)" aria-label="Switch tenant">
              @for (t of tenants(); track t.slug) {
                <option [value]="t.slug" [selected]="t.slug === auth.user()?.active_tenant">{{ t.name }}{{ t.active ? '' : ' (disabled)' }}</option>
              }
            </select>
          } @else {
            <span class="label">{{ auth.user()?.active_tenant_name }}</span>
          }
        </div>
        @if (auth.visiting()) {
          <button class="btn sm back-home" (click)="auth.switchTenant(null)" title="Back to Admin">
            <app-icon name="chevrons-left" [size]="16" /> <span class="label">Back to Admin</span>
          </button>
        }
        <div class="nav-caption">Monitoring</div>
        @for (item of nav; track item.path) {
          @if (!item.admin || auth.isAdmin()) {
          <a class="nav-link" [routerLink]="item.path" routerLinkActive="active" [title]="item.label"
             [routerLinkActiveOptions]="item.path === '/' ? homeMatch : subMatch">
            <app-icon [name]="item.icon" /> <span class="label">{{ item.label }}</span>
            @if (item.path === '/incidents' && (store.summary()?.unacknowledged ?? 0) > 0) {
              <span class="count">{{ store.summary()?.unacknowledged }}</span>
            }
          </a>
          }
        }
        <div class="bottom">
          @if (auth.isAdmin()) {
            <a routerLink="/monitors/new" class="btn primary" title="New monitor"><app-icon name="plus" /> <span class="label">New monitor</span></a>
          }
          <button class="btn" (click)="theme.toggle()" [title]="theme.theme() === 'light' ? 'Dark mode' : 'Light mode'">
            <app-icon [name]="theme.theme() === 'light' ? 'moon' : 'sun'" />
            <span class="label">{{ theme.theme() === 'light' ? 'Dark mode' : 'Light mode' }}</span>
          </button>
          <div class="user-box">
            <a routerLink="/settings/security" class="user-link" [title]="auth.user()?.username ?? ''">
              <span class="avatar">{{ initials() }}</span>
              <span class="label user-text"><b>{{ auth.user()?.name || auth.user()?.username }}</b><small class="cap">{{ roleLabel() }}</small></span>
            </a>
            <button class="btn signout" (click)="logout()" title="Sign out"><app-icon name="logout" [size]="16" /> <span class="label">Sign out</span></button>
          </div>
        </div>
      </aside>
      <main class="main"><router-outlet /></main>
    </div>
  `,
})
export class ShellComponent {
  store = inject(MonitorStore);
  theme = inject(ThemeService);
  auth = inject(AuthService);
  private router = inject(Router);
  private api = inject(ApiService);
  navOpen = signal(false);
  // Tablets start with the compact menu unless the user chose otherwise
  collapsed = signal(
    (localStorage.getItem('navCollapsed') ?? (window.innerWidth <= 1100 ? '1' : '0')) === '1',
  );

  // Dashboard filters live in the URL, so match on path only
  readonly homeMatch: IsActiveMatchOptions = { paths: 'exact', queryParams: 'ignored', fragment: 'ignored', matrixParams: 'ignored' };
  readonly subMatch: IsActiveMatchOptions = { paths: 'subset', queryParams: 'ignored', fragment: 'ignored', matrixParams: 'ignored' };

  readonly nav: { path: string; label: string; icon: string; admin?: boolean }[] = [
    { path: '/', label: 'Dashboard', icon: 'grid' },
    { path: '/activity', label: 'Alerts', icon: 'history' },
    { path: '/incidents', label: 'Incidents', icon: 'alert' },
    { path: '/maintenance', label: 'Maintenance', icon: 'wrench', admin: true },
    { path: '/status-pages', label: 'Status pages', icon: 'globe', admin: true },
    { path: '/settings', label: 'Settings', icon: 'cog' },
  ];

  initials = computed(() => {
    const u = this.auth.user();
    const words = (u?.name || u?.username || '?').split(/[\s._-]+/).filter(Boolean);
    return words.slice(0, 2).map((w) => w[0].toUpperCase()).join('');
  });

  roleLabel = computed(() => (this.auth.user()?.role === 'superadmin' ? 'Super admin' : this.auth.user()?.role ?? ''));
  tenants = signal<Tenant[]>([]);

  logout(): void {
    this.auth.logout();
    this.router.navigate(['/login']);
  }

  constructor() {
    this.router
      .events.pipe(filter((e) => e instanceof NavigationEnd), takeUntilDestroyed())
      .subscribe(() => this.navOpen.set(false));
    if (this.auth.isSuper()) this.api.tenants().subscribe((t) => this.tenants.set(t));
  }

  toggleCollapsed(): void {
    this.collapsed.update((c) => !c);
    localStorage.setItem('navCollapsed', this.collapsed() ? '1' : '0');
  }
}
