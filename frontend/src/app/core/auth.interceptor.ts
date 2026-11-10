import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { AuthService } from './auth.service';

export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  const token = auth.token;
  const tenant = auth.tenant;
  const isPublic = req.url.startsWith('/api/public') || req.url.startsWith('/api/auth/');
  const headers: Record<string, string> = {};
  if (token) headers['Authorization'] = `Bearer ${token}`;
  if (token && tenant && !req.url.startsWith('/api/public')) headers['X-Tenant'] = tenant;
  return next(token ? req.clone({ setHeaders: headers }) : req).pipe(
    catchError((err: HttpErrorResponse) => {
      if (!isPublic && err.status === 401) {
        auth.clear();
        router.navigate(['/login'], { queryParams: { next: router.url === '/' ? null : router.url } });
      } else if (!isPublic && err.status === 403 && err.error?.detail === 'Password change required') {
        router.navigate(['/change-password']);
      }
      return throwError(() => err);
    }),
  );
};
