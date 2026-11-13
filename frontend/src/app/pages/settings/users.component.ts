import { Component, computed, inject, signal } from '@angular/core';
import { DatePipe } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService, errorText } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { ConfirmService } from '../../core/confirm.service';
import { User } from '../../core/models';

@Component({
  selector: 'app-settings-users',
  standalone: true,
  imports: [FormsModule, DatePipe],
  template: `
    <h2 class="s-title">Users</h2>
    <p class="muted">Admins manage everything. Members see only the projects they belong to and can acknowledge incidents.</p>

    <section class="s-section">
      <div class="s-head">
        <h3>All users</h3>
        @if (!editing()) { <button class="btn primary sm" (click)="edit(null)">Add user</button> }
      </div>

      @if (editing(); as u) {
        <form class="s-fields s-box" (ngSubmit)="save()">
          <label>User name <input name="un" [(ngModel)]="u.username" autocomplete="off" required /></label>
          <label>Full name <input name="nm" [(ngModel)]="u.name" /></label>
          <label>Email <input name="em" type="email" [(ngModel)]="u.email" placeholder="Needed for alerts and password reset" /></label>
          <label>Role
            <select name="role" [(ngModel)]="u.role">
              <option value="member">Member</option>
              @if (inAdminTenant()) {
                @if (auth.isSuper()) { <option value="superadmin">Super admin</option> }
                @if (u.role === 'admin') { <option value="admin">Admin</option> }
              } @else {
                <option value="admin">Admin (one per tenant)</option>
              }
            </select>
          </label>
          <label>{{ u.id ? 'New password (optional)' : 'Password' }}
            <input name="pw" type="password" [(ngModel)]="u.password" autocomplete="new-password" />
            <small class="muted">At least 8 characters. The user must change it at next sign-in.</small>
          </label>
          <label class="check"><input type="checkbox" name="act" [(ngModel)]="u.active" /> Active (can sign in)</label>
          @if (error()) { <p class="error full">{{ error() }}</p> }
          <div class="row full">
            <button class="btn primary" [disabled]="!u.username || (!u.id && (u.password?.length ?? 0) < 8)">Save</button>
            <button type="button" class="btn" (click)="editing.set(null)">Cancel</button>
          </div>
        </form>
      }

      <table>
        <thead><tr><th>User</th><th>Role</th><th class="hide-sm">Email</th><th class="hide-sm">Last sign-in</th><th class="hide-sm">Status</th><th></th></tr></thead>
        <tbody>
          @for (u of users(); track u.id) {
            <tr [class.muted]="!u.active">
              <td><b>{{ u.username }}</b> @if (u.name) { <div class="muted small">{{ u.name }}</div> }</td>
              <td class="cap">{{ u.role === 'superadmin' ? 'Super admin' : u.role }}</td>
              <td class="hide-sm">{{ u.email || '-' }}</td>
              <td class="hide-sm">{{ u.last_login_at ? (u.last_login_at | date: 'MMM d, y, HH:mm') : 'Never' }}</td>
              <td class="hide-sm">
                {{ !u.active ? 'Disabled' : u.must_change_password ? 'Must change password' : 'Active' }}
                <div class="muted small">since {{ u.created_at | date: 'MMM d, y' }}</div>
              </td>
              <td class="row nowrap">
                <button class="btn sm" (click)="edit(u)">Edit</button>
                @if (u.id !== auth.user()?.id || auth.visiting()) { <button class="btn sm danger" (click)="remove(u)">Delete</button> }
              </td>
            </tr>
          }
        </tbody>
      </table>
    </section>
  `,
})
export class UserSettingsComponent {
  private api = inject(ApiService);
  auth = inject(AuthService);
  private confirm = inject(ConfirmService);
  users = signal<User[]>([]);
  editing = signal<User | null>(null);
  error = signal('');
  inAdminTenant = computed(() => this.auth.user()?.active_tenant === 'admin');

  constructor() {
    this.load();
  }

  load(): void {
    this.api.users().subscribe((u) => this.users.set(u));
  }

  edit(u: User | null): void {
    this.error.set('');
    this.editing.set(u ? { ...u, password: '' } : { username: '', name: '', email: '', role: 'member', active: true, password: '' });
  }

  save(): void {
    const u = this.editing()!;
    const body: User = {
      username: u.username.trim(), name: u.name, email: u.email?.trim() || null, role: u.role, active: u.active,
      password: u.password || null,
    };
    const call = u.id ? this.api.updateUser(u.id, body) : this.api.createUser(body);
    call.subscribe({
      next: () => {
        this.editing.set(null);
        this.load();
        if (u.id === this.auth.user()?.id && !this.auth.visiting()) this.auth.reload();
      },
      error: (e) => this.error.set(errorText(e)),
    });
  }

  async remove(u: User): Promise<void> {
    const ok = await this.confirm.ask({
      title: 'Delete user?',
      message: `You are about to delete the user "${u.username}".`,
      details: ['They can no longer sign in.', 'They are removed from all projects and stop getting alerts.'],
      typeToConfirm: u.username,
      action: 'Delete user',
    });
    if (!ok) return;
    this.api.deleteUser(u.id!).subscribe({ next: () => this.load(), error: (e) => alert(errorText(e)) });
  }
}
