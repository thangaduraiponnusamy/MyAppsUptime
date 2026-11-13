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
import { BackupSettingsComponent } from './pages/settings/backup.component';
import { AboutSettingsComponent } from './pages/settings/about.component';
import { TenantSettingsComponent } from './pages/settings/tenants.component';
import { LocationSettingsComponent } from './pages/settings/locations.component';

export const routes: Routes = [
  { path: 'status/:slug', component: PublicStatusComponent },
  { path: 'login', component: LoginComponent, title: 'Sign in - MyAppsUptime' },
  { path: 'change-password', component: SetPasswordComponent },
  { path: 'reset-password', component: SetPasswordComponent },
  {
    path: '',
    component: ShellComponent,
    canActivate: [authGuard],
    children: [
      { path: '', component: DashboardComponent },
      { path: 'activity', component: ActivityComponent },
      { path: 'monitors/new', component: MonitorFormComponent, canActivate: [adminGuard] },
      { path: 'monitors/:id', component: MonitorDetailComponent },
      { path: 'monitors/:id/edit', component: MonitorFormComponent, canActivate: [adminGuard] },
      { path: 'incidents', component: IncidentsComponent },
      { path: 'maintenance', component: MaintenanceComponent, canActivate: [adminGuard] },
      { path: 'notifications', redirectTo: 'settings/notifications' },
      { path: 'status-pages', component: StatusPagesComponent, canActivate: [adminGuard] },
      {
        path: 'settings',
        component: SettingsComponent,
        children: [
          { path: '', redirectTo: 'general', pathMatch: 'full' },
          { path: 'general', component: GeneralSettingsComponent },
          { path: 'notifications', component: NotificationSettingsComponent, canActivate: [adminGuard] },
          { path: 'teams', redirectTo: 'projects' },
          { path: 'projects', component: ProjectSettingsComponent, canActivate: [adminGuard] },
          { path: 'users', component: UserSettingsComponent, canActivate: [adminGuard] },
          { path: 'locations', component: LocationSettingsComponent, canActivate: [adminGuard] },
          { path: 'tenants', component: TenantSettingsComponent, canActivate: [superGuard] },
          { path: 'security', component: SecuritySettingsComponent },
          { path: 'backup', component: BackupSettingsComponent, canActivate: [adminGuard] },
          { path: 'about', component: AboutSettingsComponent },
        ],
      },
    ],
  },
  { path: '**', redirectTo: '' },
];
