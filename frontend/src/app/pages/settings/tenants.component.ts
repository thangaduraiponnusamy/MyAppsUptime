import { Component, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService, errorText } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { ConfirmService } from '../../core/confirm.service';
import { Tenant, TenantCreate } from '../../core/models';

@Component({
  selector: 'app-settings-tenants',
  standalone: true,
  imports: [FormsModule, DatePipe],
  template: `
    <h2 class="s-title">Tenants</h2>
    <p class="muted">Each tenant has its own database file, one or more admins, and its own projects, services and users. Only you (super admin) see this page.</p>

    <section class="s-section">
      <div class="s-head">
        <h3>All tenants</h3>
        @if (!creating()) { <button class="btn primary sm" (click)="startCreate()">Add tenant</button> }
      </div>

      @if (creating(); as t) {
        <form class="s-fields s-box" (ngSubmit)="create()">
          <label>Tenant name <input name="nm" [(ngModel)]="t.name" (ngModelChange)="autoSlug($event)" required placeholder="Acme Corp" /></label>
          <label>Tenant ID <input name="sl" [(ngModel)]="t.slug" (input)="slugTouched = true" required placeholder="acme" />
            <small class="muted">Lowercase letters, numbers and dashes. Used for the database file name. Cannot be changed.</small>
          </label>
          <label>Admin user name <input name="au" [(ngModel)]="t.admin_username" autocomplete="off" required /></label>
          <label>Admin email <input name="ae" type="email" [(ngModel)]="t.admin_email" placeholder="Optional" /></label>
          <label>Admin password <input name="ap" type="password" [(ngModel)]="t.admin_password" autocomplete="new-password" />
            <small class="muted">At least 8 characters. The admin must change it at first sign-in.</small>
          </label>
          @if (error()) { <p class="error full">{{ error() }}</p> }
          <div class="row full">
            <button class="btn primary" [disabled]="!t.name || !t.slug || !t.admin_username || t.admin_password.length < 8">Create tenant</button>
            <button type="button" class="btn" (click)="creating.set(null)">Cancel</button>
          </div>
        </form>
      }

      @if (editing(); as e) {
        <form class="s-fields s-box" (ngSubmit)="saveEdit()">
          <label>Tenant name <input name="en" [(ngModel)]="e.name" required /></label>
          @if (!e.is_default) {
            <label class="check"><input type="checkbox" name="ea" [(ngModel)]="e.active" /> Active (users can sign in, checks run)</label>
            <label>New admin password <input name="ep" type="password" [(ngModel)]="resetPw" autocomplete="new-password" placeholder="Leave empty to keep" /></label>
          }
          @if (error()) { <p class="error full">{{ error() }}</p> }
          <div class="row full">
            <button class="btn primary" [disabled]="!e.name">Save</button>
            <button type="button" class="btn" (click)="editing.set(null)">Cancel</button>
          </div>
        </form>
      }

      <table>
        <thead><tr><th>Tenant</th><th>Admin</th><th class="hide-sm">Usage</th><th class="hide-sm">Database</th><th></th></tr></thead>
        <tbody>
          @for (t of tenants(); track t.slug) {
            <tr [class.muted]="!t.active">
              <td>
                <b>{{ t.name }}</b> @if (t.slug === auth.user()?.active_tenant) { <span class="tag">current</span> }
                <div class="muted small">{{ t.slug }} · {{ t.is_default ? 'Default' : t.active ? 'Active' : 'Disabled' }} · since {{ t.created_at | date: 'MMM d, y' }}</div>
              </td>
              <td>{{ t.admins.join(', ') || '-' }}</td>
              <td class="hide-sm">{{ t.users }} users · {{ t.teams }} projects · {{ t.monitors }} services
                @if (t.down) { <div class="small error">{{ t.down }} down</div> }
              </td>
              <td class="hide-sm"><code>{{ t.db_file }}</code><div class="muted small">{{ t.db_size_kb }} KB</div></td>
              <td class="row nowrap">
                @if (t.active && t.slug !== auth.user()?.active_tenant) { <button class="btn sm primary" (click)="open(t)">Open</button> }
                <button class="btn sm" (click)="edit(t)">Edit</button>
                @if (!t.is_default) { <button class="btn sm danger" (click)="remove(t)">Delete</button> }
              </td>
            </tr>
          }
        </tbody>
      </table>
    </section>
  `,
})
export class TenantSettingsComponent {
  private api = inject(ApiService);
  auth = inject(AuthService);
  private confirm = inject(ConfirmService);
  tenants = signal<Tenant[]>([]);
  creating = signal<TenantCreate | null>(null);
  editing = signal<Tenant | null>(null);
  error = signal('');
  resetPw = '';
  slugTouched = false;

  constructor() {
    this.load();
  }

  load(): void {
    this.api.tenants().subscribe((t) => this.tenants.set(t));
  }

  startCreate(): void {
    this.error.set('');
    this.editing.set(null);
    this.slugTouched = false;
    this.creating.set({ slug: '', name: '', admin_username: '', admin_email: '', admin_password: '' });
  }

  autoSlug(name: string): void {
    const t = this.creating();
    if (!t || this.slugTouched) return;
    t.slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 30);
  }

  create(): void {
    const t = this.creating()!;
    this.slugTouched = true;
    const body = { ...t, name: t.name.trim(), slug: t.slug.trim(), admin_username: t.admin_username.trim(), admin_email: t.admin_email?.trim() || null };
    this.api.createTenant(body).subscribe({
      next: () => { this.creating.set(null); this.load(); },
      error: (e) => this.error.set(errorText(e)),
    });
  }

  edit(t: Tenant): void {
    this.error.set('');
    this.creating.set(null);
    this.resetPw = '';
    this.editing.set({ ...t });
  }

  saveEdit(): void {
    const e = this.editing()!;
    this.api.updateTenant(e.slug, { name: e.name.trim(), active: e.active }).subscribe({
      next: () => {
        if (!this.resetPw) return this.done();
        this.api.resetTenantAdmin(e.slug, this.resetPw).subscribe({
          next: (r) => { alert(`New password set for ${r.username}. They must change it at next sign-in.`); this.done(); },
          error: (err) => this.error.set(errorText(err)),
        });
      },
      error: (err) => this.error.set(errorText(err)),
    });
  }

  private done(): void {
    this.editing.set(null);
    this.load();
    this.auth.reload();
  }

  open(t: Tenant): void {
    this.auth.switchTenant(t.is_default ? null : t.slug);
  }

  async remove(t: Tenant): Promise<void> {
    const ok = await this.confirm.ask({
      title: 'Delete tenant?',
      message: `You are about to delete the tenant "${t.name}".`,
      details: [
        `${t.users} users, ${t.teams} projects and ${t.monitors} services stop working.`,
        'Nobody in this tenant can sign in any more.',
        `The database file ${t.db_file} is moved to tenants/deleted as a backup.`,
      ],
      typeToConfirm: t.slug,
      action: 'Delete tenant',
    });
    if (!ok) return;
    this.api.deleteTenant(t.slug).subscribe({
      next: () => (t.slug === this.auth.user()?.active_tenant ? this.auth.switchTenant(null) : this.load()),
      error: (e) => alert(errorText(e)),
    });
  }
}
