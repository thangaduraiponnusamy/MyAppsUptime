import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, catchError, map, of, tap } from 'rxjs';
import { Me } from './models';

const KEY = 'session';

export interface AuthOptions {
  version: string;
  email_reset: boolean;
  refresh_sec: number;
}

@Injectable({ providedIn: 'root' })
export class AuthService {
  private http = inject(HttpClient);
  readonly user = signal<Me | null>(null);
  readonly isAdmin = computed(() => this.user()?.role === 'admin');
  readonly refreshMs = computed(() => Math.max(5, this.user()?.refresh_sec ?? 5) * 1000);

  get token(): string | null {
    return localStorage.getItem(KEY);
  }

  login(username: string, password: string): Observable<Me> {
    return this.http.post<Me & { token: string }>('/api/auth/login', { username, password }).pipe(
      tap(({ token, ...me }) => {
        localStorage.setItem(KEY, token);
        this.user.set(me);
      }),
    );
  }

  /** Loads the signed-in user once; false when there is no valid session. */
  ensure(): Observable<boolean> {
    if (this.user()) return of(true);
    if (!this.token) return of(false);
    return this.http.get<Me>('/api/auth/me').pipe(
      tap((me) => this.user.set(me)),
      map(() => true),
      catchError(() => of(false)),
    );
  }

  reload(): void {
    this.http.get<Me>('/api/auth/me').subscribe((me) => this.user.set(me));
  }

  changePassword(current: string, next: string): Observable<Me> {
    return this.http.post<Me>('/api/auth/password', { current, new: next }).pipe(tap((me) => this.user.set(me)));
  }

  options(): Observable<AuthOptions> {
    return this.http.get<AuthOptions>('/api/auth/options');
  }

  forgot(username: string) {
    return this.http.post('/api/auth/forgot', { username });
  }

  reset(token: string, password: string) {
    return this.http.post('/api/auth/reset', { token, new: password });
  }

  logout(): void {
    if (this.token) this.http.post('/api/auth/logout', {}).subscribe({ error: () => undefined });
    this.clear();
  }

  clear(): void {
    localStorage.removeItem(KEY);
    this.user.set(null);
  }
}
