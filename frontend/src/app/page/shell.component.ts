import {
  ChangeDetectionStrategy,
  Component,
  inject,
  OnInit,
  OnDestroy,
  HostListener,
  ElementRef,
  ViewChild,
} from "@angular/core";
import { CommonModule } from "@angular/common";
import { FormsModule } from "@angular/forms";
import { Router, RouterLink, RouterLinkActive, RouterOutlet, NavigationEnd } from "@angular/router";
import { Subscription, filter } from "rxjs";
import { CdkTrapFocus } from "@angular/cdk/a11y";
import { workspaceAreas, resourceLink, resourceIcon } from "../shared/workspace-navigation";
import { Store } from "@ngrx/store";
import { AuthService } from "../services/auth.service";
import { DataService, errorMessage } from "../services/data.service";
import { ThemeService } from "../services/theme.service";
import { DeskService } from "../services/desk.service";
import { Resource } from "../common";
import { catalogueLoaded, catalogueRequested, catalogueFailed } from "../store";
import { IconComponent } from "../shared/icon.component";
import { BrandComponent } from "../shared/brand.component";
@Component({
  changeDetection: ChangeDetectionStrategy.Eager,
  standalone: true,
  selector: "ad-shell",
  imports: [
    CommonModule,
    FormsModule,
    RouterLink,
    RouterLinkActive,
    RouterOutlet,
    IconComponent,
    BrandComponent,
    CdkTrapFocus,
  ],
  template: `<a class="skip-link" href="#main-content">Skip to content</a>
    <div class="app-shell" [class.nav-open]="navOpen">
      @if (navOpen) {
        <button class="nav-backdrop" aria-label="Close navigation" (click)="closeNavigation()"></button>
      }
      <aside class="sidebar" id="workspace-navigation" [cdkTrapFocus]="navOpen">
        <div class="sidebar-brand">
          <a routerLink="/" class="brand-link" aria-label="AlgoDial home"><ad-brand /></a>
          <button class="icon-button mobile-menu" aria-label="Close navigation" (click)="closeNavigation()">
            <ad-icon name="close" />
          </button>
        </div>
        <div class="workspace-badge"><ad-icon name="phone" />Contact centre</div>
        <label class="nav-search"
          ><ad-icon name="search" /><input
            #navigationSearch
            type="search"
            [(ngModel)]="filter"
            aria-label="Find a page"
            placeholder="Find a page…"
          />
          @if (filter) {
            <button class="nav-clear" aria-label="Clear page search" (click)="filter = ''">
              <ad-icon name="close" />
            </button>
          }
        </label>
        <nav aria-label="Main navigation">
          <a
            routerLink="/"
            [hidden]="!matches('Workspace')"
            routerLinkActive="active"
            [routerLinkActiveOptions]="{ exact: true }"
            ><ad-icon name="grid" />Workspace</a
          >
          @if (auth.can("can_take_calls") && matches("Agent workspace")) {
            <a routerLink="/agent" routerLinkActive="active"><ad-icon name="phone" />Agent workspace</a>
          }
          @if (
            auth.can("can_monitor_calls", "can_whisper_calls", "can_barge_calls", "can_force_agent_state") &&
            matches("Supervision")
          ) {
            <a routerLink="/supervisor" routerLinkActive="active"><ad-icon name="users" />Supervision</a>
          }
          @if (groups.length) {
            <p class="nav-section-label">Manage your centre</p>
          }
          @for (group of groups; track group.key) {
            <details
              class="nav-group"
              [open]="!!filter || openAreas.has(group.key)"
              (toggle)="toggleArea(group.key, $event)"
            >
              <summary>
                <ad-icon [name]="group.icon" /><span>{{ group.name }}</span
                ><ad-icon name="chevron" class="nav-chevron" />
              </summary>
              <div class="nav-group-links">
                @for (resource of group.rows; track resource.key) {
                  <a [routerLink]="link(resource)" routerLinkActive="active" ariaCurrentWhenActive="page"
                    ><span>{{ resource.name }}</span></a
                  >
                }
              </div>
            </details>
          }
          @if (filter && !groups.length && !matches("Workspace Agent workspace Supervision")) {
            <p class="nav-empty" role="status">No pages match “{{ filter }}”.</p>
          }
        </nav>
        <footer class="sidebar-footer"><ad-icon name="shield" /><span>Access follows your role</span></footer>
      </aside>
      <div class="app-body">
        <header class="app-topbar">
          <div class="topbar-start">
            <button
              class="icon-button mobile-menu"
              aria-label="Toggle navigation"
              #navigationToggle
              aria-controls="workspace-navigation"
              [attr.aria-expanded]="navOpen"
              (click)="toggleNavigation()"
            >
              <ad-icon name="menu" /></button
            ><span class="breadcrumb"
              ><a routerLink="/">Workspace</a> <ad-icon name="chevron" /><strong>{{
                currentTitle
              }}</strong></span
            >
          </div>
          <div class="topbar-actions">
            <div class="language-control" role="group" aria-label="Language">
              <button
                [class.selected]="language === 'en'"
                (click)="setLanguage('en')"
                [attr.aria-pressed]="language === 'en'"
              >
                EN</button
              ><button
                [class.selected]="language === 'sw'"
                (click)="setLanguage('sw')"
                [attr.aria-pressed]="language === 'sw'"
                title="Swahili labels are drafts for Tostao’s review"
              >
                SW
              </button>
            </div>
            <button class="icon-button" (click)="theme.toggle()" aria-label="Switch colour theme">
              <ad-icon [name]="theme.dark() ? 'sun' : 'moon'" />
            </button>
            <div class="profile-avatar" aria-hidden="true">{{ initials }}</div>
            <div class="profile-name">
              <strong>{{ auth.profile()?.user?.name }}</strong
              ><small>{{ auth.profile()?.user?.email }}</small>
            </div>
            <button
              class="icon-button"
              (click)="logout()"
              aria-label="Sign out"
              [disabled]="desk.busy()"
              [title]="desk.busy() ? 'Finish your call and wrap-up before signing out' : 'Sign out'"
            >
              <ad-icon name="logout" />
            </button>
          </div>
        </header>
        <main id="main-content" class="main-content" [class.desk-content]="isDesk">
          @if (error) {
            <p class="alert danger" role="alert">{{ error }}</p>
          }
          <router-outlet />
        </main>
        <footer class="app-footer" [hidden]="isDesk">
          AlgoDial contact centre <span>Tools available to your account.</span>
        </footer>
      </div>
    </div>`,
})
export class ShellComponent implements OnInit, OnDestroy {
  @ViewChild("navigationToggle") navigationToggle?: ElementRef<HTMLButtonElement>;
  @ViewChild("navigationSearch") navigationSearch?: ElementRef<HTMLInputElement>;
  private navigation?: Subscription;
  openAreas = new Set<string>();
  auth = inject(AuthService);
  theme = inject(ThemeService);
  desk = inject(DeskService);
  private api = inject(DataService);
  private router = inject(Router);
  private store = inject(Store);
  resources: Resource[] = [];
  filter = "";
  error = "";
  navOpen = false;
  language = localStorage.getItem("algodial.language") || "en";
  get initials() {
    return (this.auth.profile()?.user.name || "")
      .split(" ")
      .map((s) => s[0])
      .slice(0, 2)
      .join("")
      .toUpperCase();
  }
  get isDesk() {
    return /^\/(agent|supervisor)/.test(this.router.url);
  }
  get currentTitle() {
    if (this.router.url.startsWith("/agent")) return "Agent workspace";
    if (this.router.url.startsWith("/supervisor")) return "Supervision";
    const resource = this.resources.find((r) => this.router.url.startsWith(this.link(r)));
    return resource?.name || "Overview";
  }
  get groups() {
    return workspaceAreas(this.resources, this.filter);
  }
  matches(label: string) {
    return !this.filter.trim() || label.toLowerCase().includes(this.filter.trim().toLowerCase());
  }
  toggleArea(key: string, event: Event) {
    if ((event.target as HTMLDetailsElement).open) this.openAreas.add(key);
    else this.openAreas.delete(key);
  }
  private revealCurrentArea() {
    const area = workspaceAreas(this.resources).find((group) =>
      group.rows.some((resource) => this.router.url.split("?")[0] === resourceLink(resource)),
    );
    if (area) this.openAreas.add(area.key);
  }
  toggleNavigation() {
    if (this.navOpen) return this.closeNavigation();
    this.navOpen = true;
    setTimeout(() => this.navigationSearch?.nativeElement.focus());
  }
  @HostListener("document:keydown.escape")
  closeNavigation() {
    if (!this.navOpen) return;
    this.navOpen = false;
    this.navigationToggle?.nativeElement.focus();
  }
  @HostListener("window:resize")
  closeDesktopNavigation() {
    if (window.innerWidth > 760) this.navOpen = false;
  }
  async ngOnInit() {
    this.navigation = this.router.events
      .pipe(filter((event) => event instanceof NavigationEnd))
      .subscribe(() => {
        this.navOpen = false;
        this.filter = "";
        this.revealCurrentArea();
      });
    if (this.auth.profile()?.user.staff) {
      this.store.dispatch(catalogueRequested());
      try {
        this.resources = await this.api.get<Resource[]>("catalogue");
        this.store.dispatch(catalogueLoaded({ resources: this.resources }));
        this.revealCurrentArea();
      } catch (e) {
        this.store.dispatch(catalogueFailed());
        this.error = errorMessage(e);
      }
    }
  }
  ngOnDestroy() {
    this.navigation?.unsubscribe();
  }
  link = resourceLink;
  icon = resourceIcon;
  setLanguage(language: string) {
    this.language = language;
    localStorage.setItem("algodial.language", language);
    document.documentElement.lang = language;
    window.dispatchEvent(new CustomEvent("algodial:language", { detail: language }));
  }
  async logout() {
    try {
      await this.desk.endSessions();
      await this.auth.logout();
      await this.router.navigateByUrl("/login");
    } catch (e) {
      this.error = errorMessage(e);
    }
  }
}
