import { Injectable, inject, signal } from "@angular/core";
import { HttpClient, HttpBackend } from "@angular/common/http";
import { firstValueFrom } from "rxjs";
import { Store } from "@ngrx/store";
import { Bootstrap, Session } from "../common";
import { signedIn, signedOut } from "../store";
@Injectable({ providedIn: "root" })
export class AuthService {
  private http = new HttpClient(inject(HttpBackend));
  private store = inject(Store);
  readonly profile = signal<Bootstrap | null>(null);
  readonly session = signal<Session | null>(this.read());
  private refreshFlight: Promise<any> | null = null;
  clientId = "";
  private generation = 0;
  private read(): Session | null {
    try {
      return JSON.parse(sessionStorage.getItem("algodial.angular.session") || "null");
    } catch {
      return null;
    }
  }
  async configure() {
    const config = await firstValueFrom(
      this.http.get<{ clientId: string }>("/api/workspace/config", { credentials: "omit" }),
    );
    this.clientId = config.clientId;
    return config;
  }
  setSession(reply: any) {
    const session: Session = {
      access: reply.access_token,
      refresh: reply.refresh_token,
      expiresAt: Date.now() + (reply.expires_in || 3600) * 1000,
      requirements: reply.requirements,
    };
    this.session.set(session);
    sessionStorage.setItem("algodial.angular.session", JSON.stringify(session));
    window.dispatchEvent(new CustomEvent("algodial:tokens", { detail: session }));
  }
  async login(email: string, password: string, otp: string) {
    await this.configure();
    const reply: any = await firstValueFrom(
      this.http.post(
        "/api/oauth2/access_token",
        new URLSearchParams({
          client_id: this.clientId,
          grant_type: "password",
          username: email,
          password,
          ...(otp ? { otp } : {}),
        }).toString(),
        { credentials: "omit", headers: { "Content-Type": "application/x-www-form-urlencoded" } },
      ),
    );
    if (!reply.success) throw reply;
    this.setSession(reply);
    return reply;
  }
  async refresh() {
    if (this.refreshFlight) return this.refreshFlight;
    const previous = this.session(),
      epoch = this.generation;
    if (!previous?.refresh) throw new Error("Your session ended. Please sign in again.");
    this.refreshFlight = (async () => {
      const reply: any = await firstValueFrom(
        this.http.post(
          "/api/oauth2/access_token",
          new URLSearchParams({
            client_id: this.clientId,
            grant_type: "refresh_token",
            refresh_token: previous.refresh,
          }).toString(),
          { credentials: "omit", headers: { "Content-Type": "application/x-www-form-urlencoded" } },
        ),
      );
      if (epoch !== this.generation) throw new Error("Session changed.");
      if (!reply.success) throw reply;
      this.setSession(reply);
      return reply;
    })().finally(() => (this.refreshFlight = null));
    return this.refreshFlight;
  }
  async access() {
    let session = this.session();
    if (!session) throw new Error("Please sign in.");
    if (session.expiresAt < Date.now() + 30000) {
      await this.refresh();
      session = this.session();
    }
    if (!session) throw new Error("Session ended.");
    return session.access;
  }
  async bootstrap() {
    if (!this.clientId) await this.configure();
    const access = await this.access(),
      epoch = this.generation;
    const profile = await firstValueFrom(
      this.http.get<Bootstrap>("/api/workspace/bootstrap", {
        credentials: "omit",
        headers: { Authorization: `Bearer ${access}` },
      }),
    );
    if (epoch !== this.generation) throw new Error("Session changed.");
    this.profile.set(profile);
    this.store.dispatch(signedIn({ profile }));
    return profile;
  }
  can(...codes: string[]) {
    const p = this.profile();
    return !!p && (p.user.superuser || codes.some((c) => p.permissions.includes(c)));
  }
  async logout() {
    const access = this.session()?.access;
    this.generation++;
    this.session.set(null);
    this.profile.set(null);
    sessionStorage.removeItem("algodial.angular.session");
    sessionStorage.removeItem("algodial.tokens");
    sessionStorage.removeItem("algodial.supervisor.tokens");
    this.store.dispatch(signedOut());
    window.dispatchEvent(new Event("algodial:logout"));
    if (access)
      await firstValueFrom(
        this.http.post(
          "/api/oauth2/revoke_token",
          new URLSearchParams({ client_id: this.clientId, token: access }).toString(),
          { credentials: "omit", headers: { "Content-Type": "application/x-www-form-urlencoded" } },
        ),
      ).catch(() => null);
  }
}
