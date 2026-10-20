import { describe, it, expect, vi } from "vitest";
import { ActivatedRouteSnapshot, DetachedRouteHandle } from "@angular/router";
import { DeskReuseStrategy } from "./desk-reuse.strategy";
function route(parent: string) {
  const leaf = { routeConfig: { path: "" }, data: { retainDesk: true } } as unknown as ActivatedRouteSnapshot;
  (leaf as any).pathFromRoot = [{ routeConfig: { path: "" } }, { routeConfig: { path: parent } }, leaf];
  return leaf;
}
describe("desk route ownership", () => {
  it("keeps agent and supervisor component handles separate despite empty child paths", () => {
    const strategy = new DeskReuseStrategy(),
      agent = route("agent"),
      supervisor = route("supervisor");
    const a = { componentRef: { destroy: vi.fn() } },
      s = { componentRef: { destroy: vi.fn() } };
    strategy.store(agent, a as DetachedRouteHandle);
    expect(strategy.shouldAttach(supervisor)).toBe(false);
    strategy.store(supervisor, s as DetachedRouteHandle);
    expect(strategy.retrieve(agent)).toBe(a);
    expect(strategy.retrieve(supervisor)).toBe(s);
    window.dispatchEvent(new Event("algodial:logout"));
  });
  it("destroys views on logout and refuses to cache the departing user’s active view", () => {
    const strategy = new DeskReuseStrategy(),
      agent = route("agent"),
      destroy = vi.fn();
    strategy.store(agent, { componentRef: { destroy } } as DetachedRouteHandle);
    window.dispatchEvent(new Event("algodial:logout"));
    expect(destroy).toHaveBeenCalledOnce();
    expect(strategy.shouldDetach(agent)).toBe(false);
    strategy.store(agent, { oldUser: true } as DetachedRouteHandle);
    expect(strategy.retrieve(agent)).toBe(null);
    window.dispatchEvent(new Event("algodial:tokens"));
    expect(strategy.shouldDetach(agent)).toBe(true);
    expect(strategy.shouldAttach(agent)).toBe(false);
  });
});
