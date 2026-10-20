import { END_AGENT_SESSION, END_SUPERVISOR_SESSION } from "../store/api.graphql";
import { Injectable, inject, NgZone, signal } from "@angular/core";
import { Router } from "@angular/router";
import { DataService } from "./data.service";
import { AuthService } from "./auth.service";
import { installDeskUi } from "../modules/desk/desk-ui.js";
interface DeskHandle {
  dispose(): void;
  readonly active: boolean;
}
@Injectable({ providedIn: "root" })
export class DeskService {
  private api = inject(DataService);
  private auth = inject(AuthService);
  private router = inject(Router);
  private zone = inject(NgZone);
  private handles = new Map<string, DeskHandle>();
  private sip: Promise<void> | null = null;
  readonly busy = signal(false);
  constructor() {
    installDeskUi();
    window.addEventListener("algodial:logout", () => {
      for (const key of this.handles.keys()) this.dispose(key);
    });
  }
  private phoneLibrary() {
    if ((window as any).JsSIP) return Promise.resolve();
    if (!this.sip)
      this.sip = new Promise<void>((resolve, reject) => {
        const script = document.createElement("script");
        script.src = "/static/agentdesk/vendor/jssip-3.12.0.min.js";
        script.onload = () => resolve();
        script.onerror = () => {
          this.sip = null;
          reject(new Error("Could not load the browser phone."));
        };
        document.head.append(script);
      });
    return this.sip;
  }
  async mount(kind: "agent" | "supervisor", root: HTMLElement, lifetime: AbortSignal) {
    const profile = this.auth.profile()!;
    if (kind === "supervisor") await this.phoneLibrary();
    const config = {
      clientId: profile.clientId,
      tokenUrl: "/api/oauth2/access_token",
      revokeUrl: "/api/oauth2/revoke_token",
      graphqlUrl: "/api/graphql",
      liveUrl: "/ws/" + kind,
      phoneLibraryUrl: "/static/agentdesk/vendor/jssip-3.12.0.min.js",
      deskPhoneCheckMs: 10000,
      messages: kind === "agent" ? profile.agentMessages : profile.supervisorMessages,
      tokens: () => this.auth.session(),
      refresh: () => this.auth.refresh(),
      setTokens: (reply: any) => this.auth.setSession(reply),
      changed: () => this.zone.run(() => this.busy.set(this.active)),
      expired: (message: string, tokens: any) =>
        this.zone.run(async () => {
          if (this.auth.profile() !== profile || lifetime.aborted) return;
          const dictionaries = kind === "agent" ? profile.agentMessages : profile.supervisorMessages;
          const sessionNotice = Object.values(dictionaries).some((labels) =>
            ["Your session ended. Please sign in again.", "Signed out.", "Session ended."].some(
              (key) => labels[key] === message || key === message,
            ),
          );
          if (tokens && !sessionNotice) {
            this.dispose(kind);
            const note = document.createElement("p");
            note.className = "alert danger";
            note.setAttribute("role", "alert");
            note.textContent = message || "This workspace is not available for your account.";
            root.prepend(note);
            return;
          }
          if (this.auth.session()) await this.auth.logout();
          await this.router.navigateByUrl("/login");
        }),
    };
    const module =
      kind === "agent"
        ? await import("../modules/desk/agent.controller.js")
        : await import("../modules/desk/supervisor.controller.js");
    if (lifetime.aborted || this.auth.profile() !== profile) return;
    this.zone.runOutsideAngular(() => {
      const handle = (module as any)[kind === "agent" ? "mountAgentDesk" : "mountSupervisorDesk"](
        root,
        config,
      );
      this.handles.set(kind, handle);
    });
    root.addEventListener("click", (event) => {
      const anchor = (event.target as HTMLElement).closest('a[href="/"]');
      if (anchor) {
        event.preventDefault();
        this.zone.run(() => this.router.navigateByUrl("/"));
      }
    });
  }
  async endSessions() {
    if (this.active) throw new Error("Finish your call and wrap-up before signing out.");
    for (const kind of this.handles.keys()) {
      await this.api.graph(kind === "agent" ? END_AGENT_SESSION : END_SUPERVISOR_SESSION, {}, true);
    }
  }
  dispose(kind: string) {
    this.handles.get(kind)?.dispose();
    this.handles.delete(kind);
    this.busy.set(this.active);
  }
  get active() {
    return [...this.handles.values()].some((handle) => handle.active);
  }
}
