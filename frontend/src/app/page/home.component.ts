import { ChangeDetectionStrategy, Component, inject } from "@angular/core";
import { CommonModule } from "@angular/common";
import { RouterLink } from "@angular/router";
import { Store } from "@ngrx/store";
import { AuthService } from "../services/auth.service";
import { workspaceAreas, resourceLink, WorkspaceArea } from "../shared/workspace-navigation";
import { WorkspaceState } from "../store";
import { IconComponent } from "../shared/icon.component";
@Component({
  changeDetection: ChangeDetectionStrategy.Eager,
  standalone: true,
  imports: [CommonModule, RouterLink, IconComponent],
  template: `<section class="page-heading overview-heading">
      <div>
        <p class="eyebrow">Your contact centre</p>
        <h1>Welcome, {{ auth.profile()?.user?.name }}</h1>
        <p class="muted">A clear place to start your next conversation.</p>
      </div>
      <div class="overview-signature" aria-hidden="true"><span></span><span></span><span></span></div>
    </section>
    <section class="workspace-shortcuts" aria-label="Your workspaces">
      @if (auth.can("can_take_calls")) {
        <a class="surface workspace-shortcut agent-shortcut" routerLink="/agent">
          <span class="shortcut-icon"><ad-icon name="phone" /></span>
          <div>
            <p class="shortcut-kicker">Your conversations</p>
            <h2>Agent workspace</h2>
            <p>Take calls, manage your state, and complete wrap-up.</p>
          </div>
          <span class="shortcut-open"><ad-icon name="arrow" /></span>
        </a>
      }
      @if (auth.can("can_monitor_calls", "can_whisper_calls", "can_barge_calls", "can_force_agent_state")) {
        <a class="surface workspace-shortcut supervisor-shortcut" routerLink="/supervisor">
          <span class="shortcut-icon"><ad-icon name="users" /></span>
          <div>
            <p class="shortcut-kicker">Your people</p>
            <h2>Supervision</h2>
            <p>Your teams, queues, and calls, live.</p>
          </div>
          <span class="shortcut-open"><ad-icon name="arrow" /></span>
        </a>
      }
    </section>
    @if (state$ | async; as state) {
      @if (state.busy) {
        <div class="workspace-directory" role="status" aria-label="Loading your tools" aria-busy="true">
          @for (item of [1, 2, 3, 4, 5, 6]; track item) {
            <div class="surface directory-skeleton"><span></span><span></span><span></span></div>
          }
        </div>
      } @else if (state.resources.length) {
        <div class="directory-heading">
          <div>
            <h2>Manage your centre</h2>
            <p class="muted">The tools available to your account, organised around your work.</p>
          </div>
          <span class="count-badge">{{ state.resources.length }} tools</span>
        </div>
        <div class="workspace-directory">
          @for (area of group(state.resources); track area.key) {
            <section class="surface directory-group">
              <header>
                <span class="area-icon"><ad-icon [name]="area.icon" /></span>
                <div>
                  <h2>{{ area.name }}</h2>
                  <p>{{ area.description }}</p>
                </div>
              </header>
              <div class="directory-links">
                @for (row of visible(area); track row.key) {
                  <a [routerLink]="link(row)"
                    ><span>{{ row.name }}</span
                    ><ad-icon name="chevron"
                  /></a>
                }
              </div>
              @if (area.rows.length > 4) {
                <button
                  class="directory-expand"
                  [attr.aria-expanded]="expanded.has(area.key)"
                  (click)="toggle(area.key)"
                >
                  {{ expanded.has(area.key) ? "Show fewer" : "Show all " + area.rows.length + " tools"
                  }}<ad-icon name="chevron" />
                </button>
              }
            </section>
          }
        </div>
      } @else if (
        !auth.can(
          "can_take_calls",
          "can_monitor_calls",
          "can_whisper_calls",
          "can_barge_calls",
          "can_force_agent_state"
        )
      ) {
        <section class="surface empty-state">
          <ad-icon name="users" />
          <h2>No workspace assigned</h2>
          <p>Ask your administrator to check your AlgoDial roles.</p>
        </section>
      }
    }`,
})
export class HomeComponent {
  auth = inject(AuthService);
  state$ = inject(Store<{ workspace: WorkspaceState }>).select("workspace");
  expanded = new Set<string>();
  group = workspaceAreas;
  link = resourceLink;
  visible(area: WorkspaceArea) {
    return this.expanded.has(area.key) ? area.rows : area.rows.slice(0, 4);
  }
  toggle(key: string) {
    if (this.expanded.has(key)) this.expanded.delete(key);
    else this.expanded.add(key);
  }
}
