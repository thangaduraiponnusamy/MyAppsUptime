import { Routes } from '@angular/router';
import { ShellComponent } from './shell.component';
import { adminGuard, authGuard, superGuard } from './core/auth.guard';
import { DashboardComponent } from './pages/dashboard.component';
import { ActivityComponent } from './pages/activity.component';
import { MonitorDetailComponent } from './pages/monitor-detail.component';
import { MonitorFormComponent } from './pages/monitor-form.component';
import { IncidentsComponent } from './pages/incidents.component';
import { MaintenanceComponent } from './pages/maintenance.component';
import { StatusPagesComponent } from './pages/status-pages.component';
import { SettingsComponent } from './pages/settings.component';
import { PublicStatusComponent } from './pages/public-status.component';
import { LoginComponent } from './pages/login.component';
import { SetPasswordComponent } from './pages/set-password.component';
import { GeneralSettingsComponent } from './pages/settings/general.component';
import { NotificationSettingsComponent } from './pages/settings/notifications.component';
import { ProjectSettingsComponent } from './pages/settings/projects.component';
import { UserSettingsComponent } from './pages/settings/users.component';
import { SecuritySettingsComponent } from './pages/settings/security.component';
import { AuditSettingsComponent } from './pages/settings/audit.component';
import { BackupSettingsComponent } from './pages/settings/backup.component';
import { AboutSettingsComponent } from './pages/settings/about.component';
import { TenantSettingsComponent } from './pages/settings/tenants.component';
import { LocationSettingsComponent } from './pages/settings/locations.component';

export const routes: Routes = [
  { path: 'status/:slug', component: PublicStatusComponent },
  { path: 'login', component: LoginComponent, title: 'Sign in - MyAppsUptime' },
  { path: 'change-password', component: SetPasswordComponent, title: 'Change password - MyAppsUptime' },
  { path: 'reset-password', component: SetPasswordComponent, title: 'Reset password - MyAppsUptime' },
  {
    path: '',
    component: ShellComponent,
    canActivate: [authGuard],
    children: [
      { path: '', component: DashboardComponent, title: 'Dashboard - MyAppsUptime' },
      { path: 'activity', component: ActivityComponent, title: 'Alerts - MyAppsUptime' },
      { path: 'monitors/new', component: MonitorFormComponent, canActivate: [adminGuard], title: 'New monitor - MyAppsUptime' },
      { path: 'monitors/:id', component: MonitorDetailComponent, title: 'Monitor - MyAppsUptime' },
      { path: 'monitors/:id/edit', component: MonitorFormComponent, canActivate: [adminGuard], title: 'Edit monitor - MyAppsUptime' },
      { path: 'incidents', component: IncidentsComponent, title: 'Incidents - MyAppsUptime' },
      { path: 'maintenance', component: MaintenanceComponent, canActivate: [adminGuard], title: 'Maintenance - MyAppsUptime' },
      { path: 'notifications', redirectTo: 'settings/notifications' },
      { path: 'status-pages', component: StatusPagesComponent, canActivate: [adminGuard], title: 'Status pages - MyAppsUptime' },
      {
        path: 'settings',
        component: SettingsComponent,
        children: [
          { path: '', redirectTo: 'general', pathMatch: 'full' },
          { path: 'general', component: GeneralSettingsComponent, title: 'General settings - MyAppsUptime' },
          { path: 'notifications', component: NotificationSettingsComponent, canActivate: [adminGuard], title: 'Notifications - MyAppsUptime' },
          { path: 'teams', redirectTo: 'projects' },
          { path: 'projects', component: ProjectSettingsComponent, canActivate: [adminGuard], title: 'Projects - MyAppsUptime' },
          { path: 'users', component: UserSettingsComponent, canActivate: [adminGuard], title: 'Users - MyAppsUptime' },
          { path: 'locations', component: LocationSettingsComponent, canActivate: [adminGuard], title: 'Private locations - MyAppsUptime' },
          { path: 'tenants', component: TenantSettingsComponent, canActivate: [superGuard], title: 'Tenants - MyAppsUptime' },
          { path: 'security', component: SecuritySettingsComponent, title: 'Security - MyAppsUptime' },
          { path: 'audit', component: AuditSettingsComponent, canActivate: [adminGuard], title: 'Audit log - MyAppsUptime' },
          { path: 'backup', component: BackupSettingsComponent, canActivate: [adminGuard], title: 'Backup - MyAppsUptime' },
          { path: 'about', component: AboutSettingsComponent, title: 'About - MyAppsUptime' },
        ],
      },
    ],
  },
  { path: '**', redirectTo: '' },
];
