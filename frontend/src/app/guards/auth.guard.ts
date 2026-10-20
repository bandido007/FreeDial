import { inject } from "@angular/core";
import { CanActivateFn, Router } from "@angular/router";
import { AuthService } from "../services/auth.service";
export const authGuard: CanActivateFn = async (_route, state) => {
  const auth = inject(AuthService),
    router = inject(Router);
  if (!auth.session()) return router.createUrlTree(["/login"], { queryParams: { next: state.url } });
  try {
    if (!auth.profile()) await auth.bootstrap();
    return true;
  } catch {
    return router.createUrlTree(["/login"], { queryParams: { next: state.url } });
  }
};
export const staffGuard: CanActivateFn = () =>
  inject(AuthService).profile()?.user.staff || inject(Router).createUrlTree(["/not-allowed"]);
export const permissionGuard: CanActivateFn = (route) => {
  const codes = route.data["permissions"] as string[];
  return inject(AuthService).can(...codes) || inject(Router).createUrlTree(["/not-allowed"]);
};
