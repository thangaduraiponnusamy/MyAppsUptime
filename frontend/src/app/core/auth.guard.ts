import { inject } from '@angular/core';
import { CanActivateFn, Router } from '@angular/router';
import { map } from 'rxjs';
import { AuthService } from './auth.service';

export const authGuard: CanActivateFn = (_, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  return auth.ensure().pipe(
    map((ok) => {
      if (!ok) return router.createUrlTree(['/login'], { queryParams: { next: state.url === '/' ? null : state.url } });
      return auth.user()?.must_change_password ? router.createUrlTree(['/change-password']) : true;
    }),
  );
};

export const adminGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  return auth.isAdmin() || inject(Router).createUrlTree(['/']);
};

export const superGuard: CanActivateFn = () => {
  const auth = inject(AuthService);
  return auth.isSuper() || inject(Router).createUrlTree(['/']);
};
