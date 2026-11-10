import { Routes } from '@angular/router';
import { ShellComponent } from './shell.component';
import { DashboardComponent } from './pages/dashboard.component';
import { MonitorDetailComponent } from './pages/monitor-detail.component';
import { MonitorFormComponent } from './pages/monitor-form.component';
import { IncidentsComponent } from './pages/incidents.component';
import { MaintenanceComponent } from './pages/maintenance.component';
import { NotificationsComponent } from './pages/notifications.component';
import { StatusPagesComponent } from './pages/status-pages.component';
import { SettingsComponent } from './pages/settings.component';
import { PublicStatusComponent } from './pages/public-status.component';

export const routes: Routes = [
  { path: 'status/:slug', component: PublicStatusComponent },
  {
    path: '',
    component: ShellComponent,
    children: [
      { path: '', component: DashboardComponent },
      { path: 'monitors/new', component: MonitorFormComponent },
      { path: 'monitors/:id', component: MonitorDetailComponent },
      { path: 'monitors/:id/edit', component: MonitorFormComponent },
      { path: 'incidents', component: IncidentsComponent },
      { path: 'maintenance', component: MaintenanceComponent },
      { path: 'notifications', component: NotificationsComponent },
      { path: 'status-pages', component: StatusPagesComponent },
      { path: 'settings', component: SettingsComponent },
    ],
  },
  { path: '**', redirectTo: '' },
];
