import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { errorText } from '../core/api.service';
import { AuthService } from '../core/auth.service';
import { AuthLayoutComponent } from '../shared/auth-layout.component';

/** /change-password (signed in, needs the current password) and /reset-password?token=... (from the email link). */
@Component({
  selector: 'app-set-password',
  standalone: true,
  imports: [FormsModule, AuthLayoutComponent, RouterLink],
  template: `
    <app-auth-layout [heading]="token ? 'Choose a new password' : 'Change your password'"
                     [sub]="token ? 'Set a new password for your account.' : 'Please choose your own password before you continue.'">
      @if (done()) {
        <div class="auth-ok">Your password was changed. Sign in with the new password.</div>
        <p class="auth-foot"><a class="link" routerLink="/login">Go to sign in</a></p>
      } @else {
        <form class="auth-form" (ngSubmit)="save()">
          @if (!token) {
            <label>Current password <input name="c" type="password" [(ngModel)]="current" autocomplete="current-password" required /></label>
          }
          <label>New password <input name="n" type="password" [(ngModel)]="next" autocomplete="new-password" required minlength="8" /></label>
          <label>Repeat new password <input name="r" type="password" [(ngModel)]="repeat" autocomplete="new-password" required /></label>
          <ul class="pw-rules">
            <li [class.ok]="next.length >= 8">At least 8 characters</li>
            <li [class.ok]="next.length > 0 && next === repeat">Both passwords match</li>
          </ul>
          @if (error()) { <p class="auth-error">{{ error() }}</p> }
          <button class="btn primary block" [disabled]="busy() || next.length < 8 || next !== repeat || (!token && !current)">
            Save password
          </button>
        </form>
        <p class="auth-foot"><a class="link" (click)="cancel()" tabindex="0">{{ token ? 'Back to sign in' : 'Sign out' }}</a></p>
      }
    </app-auth-layout>
  `,
})
export class SetPasswordComponent {
  private auth = inject(AuthService);
  private router = inject(Router);
  readonly token = inject(ActivatedRoute).snapshot.queryParamMap.get('token');
  current = '';
  next = '';
  repeat = '';
  busy = signal(false);
  error = signal('');
  done = signal(false);

  constructor() {
    if (!this.token) this.auth.ensure().subscribe((ok) => ok || this.router.navigate(['/login']));
  }

  save(): void {
    this.busy.set(true);
    this.error.set('');
    const call = this.token ? this.auth.reset(this.token, this.next) : this.auth.changePassword(this.current, this.next);
    call.subscribe({
      next: () => {
        if (this.token) this.done.set(true);
        else this.router.navigate(['/']);
      },
      error: (e) => {
        this.busy.set(false);
        this.error.set(errorText(e));
      },
    });
  }

  cancel(): void {
    if (!this.token) this.auth.logout();
    this.router.navigate(['/login']);
  }
}
