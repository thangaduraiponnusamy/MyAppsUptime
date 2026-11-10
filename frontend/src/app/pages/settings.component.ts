import { Component, computed, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AuthService } from '../core/auth.service';
import { IconComponent } from '../shared/icon.component';

export const SETTINGS_SECTIONS = [
  { path: 'tenants', label: 'Tenants', icon: 'globe', admin: true, super: true },
  { path: 'general', label: 'General', icon: 'sliders', admin: false },
  { path: 'notifications', label: 'Notifications', icon: 'bell', admin: true },
  { path: 'teams', label: 'Teams', icon: 'folder', admin: true },
  { path: 'users', label: 'Users', icon: 'users', admin: true },
  { path: 'security', label: 'Security', icon: 'shield', admin: false },
  { path: 'backup', label: 'Backup', icon: 'database', admin: true },
  { path: 'about', label: 'About', icon: 'info', admin: false },
];

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive, IconComponent],
  template: `
    <div class="page-head"><div><h1>Settings</h1></div></div>
    <div class="settings-layout card">
      <nav class="settings-nav">
        @for (s of sections(); track s.path) {
          <a [routerLink]="s.path" routerLinkActive="active"><app-icon [name]="s.icon" [size]="16" /> {{ s.label }}</a>
        }
      </nav>
      <div class="settings-body"><router-outlet /></div>
    </div>
  `,
})
export class SettingsComponent {
  private auth = inject(AuthService);
  sections = computed(() => SETTINGS_SECTIONS.filter((s) =>
    ('super' in s ? this.auth.isSuper() : true) && (!s.admin || this.auth.isAdmin())));
}
