import { ActivatedRouteSnapshot, DetachedRouteHandle, RouteReuseStrategy } from "@angular/router";
/** Keep each desk alive during navigation; destroy every retained view on sign-out. */
export class DeskReuseStrategy implements RouteReuseStrategy {
  private handles = new Map<string, DetachedRouteHandle>();
  private enabled = true;
  constructor() {
    window.addEventListener("algodial:tokens", () => {
      this.enabled = true;
    });
    window.addEventListener("algodial:logout", () => {
      this.enabled = false;
      for (const handle of this.handles.values()) (handle as any).componentRef?.destroy();
      this.handles.clear();
    });
  }
  private key(route: ActivatedRouteSnapshot) {
    return route.pathFromRoot.map((part) => part.routeConfig?.path || "").join("/");
  }
  shouldDetach(route: ActivatedRouteSnapshot) {
    return this.enabled && route.data["retainDesk"] === true;
  }
  store(route: ActivatedRouteSnapshot, handle: DetachedRouteHandle | null) {
    if (handle && this.enabled) this.handles.set(this.key(route), handle);
  }
  shouldAttach(route: ActivatedRouteSnapshot) {
    return this.enabled && this.handles.has(this.key(route));
  }
  retrieve(route: ActivatedRouteSnapshot) {
    return this.enabled ? this.handles.get(this.key(route)) || null : null;
  }
  shouldReuseRoute(future: ActivatedRouteSnapshot, current: ActivatedRouteSnapshot) {
    return future.routeConfig === current.routeConfig;
  }
}
