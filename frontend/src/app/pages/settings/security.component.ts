import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { errorText } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';

@Component({
  selector: 'app-settings-security',
  standalone: true,
  imports: [FormsModule],
  template: `
    <h2 class="s-title">Security</h2>

    <section class="s-section">
      <h3>My account</h3>
      <dl class="s-facts">
        <dt>User name</dt><dd>{{ auth.user()?.username }}</dd>
        <dt>Name</dt><dd>{{ auth.user()?.name || '-' }}</dd>
        <dt>Email</dt><dd>{{ auth.user()?.email || '-' }}</dd>
        <dt>Role</dt><dd class="cap">{{ auth.user()?.role }}</dd>
      </dl>
    </section>

    <section class="s-section">
      <h3>Change password</h3>
      <form class="s-fields" (ngSubmit)="change()">
        <label>Current password <input name="c" type="password" [(ngModel)]="current" autocomplete="current-password" /></label>
        <label>New password <input name="n" type="password" [(ngModel)]="next" autocomplete="new-password" />
          <small class="muted">At least 8 characters.</small></label>
        <label>Repeat new password <input name="r" type="password" [(ngModel)]="repeat" autocomplete="new-password" /></label>
        <div class="row full">
          <button class="btn primary" [disabled]="!current || next.length < 8 || next !== repeat">Change password</button>
          @if (notice()) { <span class="muted">{{ notice() }}</span> }
        </div>
      </form>
    </section>

    <section class="s-section">
      <h3>Forgot password</h3>
      @if (auth.isAdmin()) {
        <p class="muted">Use "Forgot password?" on the sign-in page to get a reset link by email
          (needs the email server in Notifications and the app address in General).
          An admin can also set a new password in Users.
          If every admin is locked out, run this on the server:</p>
        <div class="code-box mono">python -m app.reset_password admin NEW_PASSWORD</div>
      } @else {
        <p class="muted">Use "Forgot password?" on the sign-in page to get a reset link by email, or ask an admin to set a new password for you.</p>
      }
    </section>

    <section class="s-section">
      <h3>API access</h3>
      <p class="muted">Scripts and Prometheus can use a fixed token: start the server with
        <span class="mono">API_TOKEN</span> set and send it as <span class="mono">Authorization: Bearer ...</span>.
        It has admin rights, so keep it secret.</p>
    </section>

    <section class="s-section">
      <h3>Sign out</h3>
      <button class="btn" (click)="logout()">Sign out of this browser</button>
    </section>
  `,
})
export class SecuritySettingsComponent {
  auth = inject(AuthService);
  private router = inject(Router);
  current = '';
  next = '';
  repeat = '';
  notice = signal('');

  change(): void {
    this.auth.changePassword(this.current, this.next).subscribe({
      next: () => {
        this.current = this.next = this.repeat = '';
        this.notice.set('Password changed.');
      },
      error: (e) => this.notice.set(errorText(e)),
    });
  }

  logout(): void {
    this.auth.logout();
    this.router.navigate(['/login']);
  }
}
