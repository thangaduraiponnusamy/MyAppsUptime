import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import { errorText } from '../core/api.service';
import { AuthService } from '../core/auth.service';
import { AuthLayoutComponent } from '../shared/auth-layout.component';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [FormsModule, AuthLayoutComponent],
  template: `
    @if (mode() === 'login') {
      <app-auth-layout heading="Sign in" sub="Welcome back. Sign in to your account.">
        <form class="auth-form" (ngSubmit)="login()">
          <label>User name <input name="u" [(ngModel)]="username" autocomplete="username" required autofocus /></label>
          <label>
            <span class="label-row">Password <a class="link" (click)="switchMode('forgot')" tabindex="0">Forgot password?</a></span>
            <span class="pw-field">
              <input name="p" [type]="show() ? 'text' : 'password'" [(ngModel)]="password" autocomplete="current-password" required />
              <button type="button" class="pw-toggle" (click)="show.set(!show())">{{ show() ? 'Hide' : 'Show' }}</button>
            </span>
          </label>
          @if (error()) { <p class="auth-error">{{ error() }}</p> }
          <button class="btn primary block" [disabled]="busy() || !username || !password">
            {{ busy() ? 'Signing in...' : 'Sign in' }}
          </button>
        </form>
      </app-auth-layout>
    } @else {
      <app-auth-layout heading="Reset password" sub="Enter your user name or email address.">
        @if (sent()) {
          <div class="auth-ok">
            @if (emailReset()) {
              If the account exists and has an email address, a reset link is on its way. The link works once and expires in 30 minutes.
            } @else {
              Email is not set up on this server, so a link cannot be sent. Ask an administrator to set a new password for you.
            }
          </div>
        } @else {
          <form class="auth-form" (ngSubmit)="forgot()">
            <label>User name or email <input name="f" [(ngModel)]="username" required autofocus /></label>
            @if (!emailReset()) {
              <p class="muted small">Email is not set up on this server. An administrator can reset your password in Settings &gt; Users.</p>
            }
            @if (error()) { <p class="auth-error">{{ error() }}</p> }
            <button class="btn primary block" [disabled]="busy() || !username">Send reset link</button>
          </form>
        }
        <p class="auth-foot"><a class="link" (click)="switchMode('login')" tabindex="0">Back to sign in</a></p>
      </app-auth-layout>
    }
  `,
})
export class LoginComponent {
  private auth = inject(AuthService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);
  mode = signal<'login' | 'forgot'>('login');
  username = '';
  password = '';
  show = signal(false);
  busy = signal(false);
  error = signal('');
  sent = signal(false);
  emailReset = signal(false);

  constructor() {
    this.auth.options().subscribe((o) => this.emailReset.set(o.email_reset));
  }

  switchMode(mode: 'login' | 'forgot'): void {
    this.mode.set(mode);
    this.error.set('');
    this.sent.set(false);
  }

  login(): void {
    this.busy.set(true);
    this.error.set('');
    this.auth.login(this.username.trim(), this.password).subscribe({
      next: (me) => {
        const next = this.route.snapshot.queryParamMap.get('next');
        // Only follow in-app paths after sign-in
        const target = next && next.startsWith('/') && !next.startsWith('//') ? next : '/';
        this.router.navigateByUrl(me.must_change_password ? '/change-password' : target);
      },
      error: (e) => {
        this.busy.set(false);
        this.error.set(errorText(e));
      },
    });
  }

  forgot(): void {
    this.busy.set(true);
    this.auth.forgot(this.username.trim()).subscribe({
      next: () => {
        this.busy.set(false);
        this.sent.set(true);
      },
      error: (e) => {
        this.busy.set(false);
        this.error.set(errorText(e));
      },
    });
  }
}
