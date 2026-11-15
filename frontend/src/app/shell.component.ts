import { Component, ElementRef, HostListener, computed, effect, inject, signal, viewChild } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { IsActiveMatchOptions, NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter } from 'rxjs';
import { ApiService } from './core/api.service';
import { AuthService } from './core/auth.service';
import { ROLE_LABEL, Tenant } from './core/models';
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
          <div class="user-box">
            <button #userBtn type="button" class="user-link" [class.open]="menuOpen()" (click)="toggleMenu()"
                    aria-haspopup="menu" [attr.aria-expanded]="menuOpen()" [title]="collapsed() ? 'Account' : ''">
              <span class="avatar">{{ initials() }}</span>
              <span class="label user-text"><b>{{ auth.user()?.name || auth.user()?.username }}</b><small>{{ roleLabel() }}</small></span>
              <app-icon class="label user-chev" name="chevrons-up-down" [size]="16" />
            </button>
          </div>
        </div>
      </aside>
      @if (menuOpen()) {
        <div #menu class="user-menu" role="menu" [style.left.px]="menuPos().left" [style.bottom.px]="menuPos().bottom"
             [style.width.px]="menuPos().width" (keydown)="menuKey($event)">
          <div class="um-head">
            <span class="avatar">{{ initials() }}</span>
            <span class="user-text"><b>{{ auth.user()?.name || auth.user()?.username }}</b><small>&#64;{{ auth.user()?.username }} &middot; {{ role() }}</small></span>
          </div>
          <button type="button" class="um-item" role="menuitemcheckbox" [attr.aria-checked]="theme.theme() === 'dark'" (click)="theme.toggle()">
            <app-icon name="moon" [size]="16" /> Dark mode
            <span class="um-switch" [class.on]="theme.theme() === 'dark'"></span>
          </button>
          <a class="um-item" role="menuitem" routerLink="/settings/security" (click)="menuOpen.set(false)">
            <app-icon name="shield" [size]="16" /> Account &amp; security
          </a>
          <div class="um-sep" role="separator"></div>
          <button type="button" class="um-item danger" role="menuitem" (click)="logout()">
            <app-icon name="logout" [size]="16" /> Sign out
          </button>
        </div>
      }
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

  // Default accounts are named after their role; show the user name instead of repeating it
  roleLabel = computed(() => {
    const u = this.auth.user();
    const role = ROLE_LABEL[u?.role ?? ''] ?? '';
    return u?.name?.toLowerCase() === role.toLowerCase() ? '@' + u.username : role;
  });
  role = computed(() => ROLE_LABEL[this.auth.user()?.role ?? ''] ?? '');
  tenants = signal<Tenant[]>([]);

  menuOpen = signal(false);
  menuPos = signal({ left: 0, bottom: 0, width: 240 });
  private userBtn = viewChild.required<ElementRef<HTMLButtonElement>>('userBtn');
  private menu = viewChild<ElementRef<HTMLElement>>('menu');

  logout(): void {
    this.menuOpen.set(false);
    this.auth.logout();
    this.router.navigate(['/login']);
  }

  // Fixed position so the menu is not clipped by the scrolling sidebar
  toggleMenu(): void {
    if (this.menuOpen()) return this.closeMenu();
    const r = this.userBtn().nativeElement.getBoundingClientRect();
    const beside = this.collapsed() && window.innerWidth > 720;
    this.menuPos.set(beside
      ? { left: r.right + 10, bottom: window.innerHeight - r.bottom, width: 240 }
      : { left: r.left, bottom: window.innerHeight - r.top + 6, width: Math.max(r.width, 220) });
    this.menuOpen.set(true);
  }

  closeMenu(focusTrigger = false): void {
    this.menuOpen.set(false);
    if (focusTrigger) this.userBtn().nativeElement.focus();
  }

  menuKey(e: KeyboardEvent): void {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    const items = Array.from(this.menu()?.nativeElement.querySelectorAll<HTMLElement>('.um-item') ?? []);
    const i = items.indexOf(document.activeElement as HTMLElement);
    const next = e.key === 'ArrowDown' ? (i + 1) % items.length : (i - 1 + items.length) % items.length;
    items[next]?.focus();
  }

  @HostListener('document:click', ['$event'])
  onDocClick(e: MouseEvent): void {
    if (!this.menuOpen()) return;
    const t = e.target as Node;
    if (!this.userBtn().nativeElement.contains(t) && !this.menu()?.nativeElement.contains(t)) this.closeMenu();
  }

  @HostListener('document:keydown.escape')
  onEsc(): void {
    if (this.menuOpen()) this.closeMenu(true);
  }

  @HostListener('window:resize')
  onResize(): void {
    if (this.menuOpen()) this.closeMenu();
  }

  constructor() {
    effect(() => this.menu()?.nativeElement.querySelector<HTMLElement>('.um-item')?.focus());
    this.router
      .events.pipe(filter((e) => e instanceof NavigationEnd), takeUntilDestroyed())
      .subscribe(() => {
        this.navOpen.set(false);
        this.menuOpen.set(false);
      });
    if (this.auth.isSuper()) this.api.tenants().subscribe((t) => this.tenants.set(t));
  }

  toggleCollapsed(): void {
    this.menuOpen.set(false);
    this.collapsed.update((c) => !c);
    localStorage.setItem('navCollapsed', this.collapsed() ? '1' : '0');
  }
}
