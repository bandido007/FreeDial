import { HttpInterceptorFn } from "@angular/common/http";
import { inject } from "@angular/core";
import { from, switchMap } from "rxjs";
import { AuthService } from "../services/auth.service";
export const authInterceptor: HttpInterceptorFn = (request, next) => {
  const auth = inject(AuthService);
  if (
    !request.url.startsWith("/api/") ||
    request.url.includes("/oauth2/") ||
    request.url.endsWith("/workspace/config")
  )
    return next(request.clone({ credentials: "omit" }));
  return from(auth.access()).pipe(
    switchMap((access) =>
      next(
        request.clone({
          credentials: "omit",
          setHeaders: {
            Authorization: `Bearer ${access}`,
            "Accept-Language": localStorage.getItem("algodial.language") || "en",
          },
        }),
      ),
    ),
  );
};
