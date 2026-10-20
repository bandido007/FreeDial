import { ChangeDetectionStrategy, Component, inject, OnInit } from "@angular/core";
import { CommonModule } from "@angular/common";
import { FormsModule } from "@angular/forms";
import { RouterLink, Router } from "@angular/router";
import { DataService, errorMessage } from "../../services/data.service";
import { StatusPillComponent } from "../../shared/ui/status-pill/status-pill.component";
@Component({
  changeDetection: ChangeDetectionStrategy.Eager,
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, StatusPillComponent],
  template: `<section class="page-heading">
      <div>
        <p class="eyebrow">GETTING STARTED</p>
        <h1>Setup guide</h1>
        <p class="muted">A live checklist for your people, routing, phones, and running services.</p>
      </div>
      <button class="button" [disabled]="busy" (click)="load()">Refresh checks</button>
    </section>
    @if (error) {
      <p class="alert danger" role="alert">{{ error }}</p>
    }
    @if (data) {
      <section class="surface setup-progress">
        <strong>{{ data.done }} of {{ data.counted }} checks complete</strong
        ><progress [value]="data.done" [max]="data.counted || 1"></progress
        ><span>{{ data.attention }} need attention</span>
      </section>
      @for (section of data.sections; track section.title) {
        <section class="surface setup-section">
          <header>
            <h2>{{ section.title }}</h2>
            <p class="muted">{{ section.intro }}</p>
          </header>
          @for (check of section.checks; track check.key) {
            <article [id]="'check-' + check.key" class="setup-check">
              <div>
                <h3>{{ check.title }}</h3>
                <p>{{ check.summary }}</p>
                @for (line of check.problems; track $index) {
                  <p class="field-error">
                    {{ line.text }}
                    @if (line.url) {
                      <a [routerLink]="route(line.url)">Review</a>
                    }
                  </p>
                }
                @for (line of check.notes; track $index) {
                  <p class="help">{{ line.text }}</p>
                }
                @if (check.fix) {
                  <a class="button small" [routerLink]="route(check.fix.url)">{{ check.fix.text }}</a>
                }
                @if (check.hands_on && data.canConfirm) {
                  <p>{{ check.how }}</p>
                  <label
                    >Test notes<input
                      [(ngModel)]="notes[check.key]"
                      [attr.aria-label]="'Notes for ' + check.title"
                  /></label>
                  <div class="dialog-actions">
                    <button class="button small" (click)="act(check.key, 'confirm')">Confirm tested</button
                    ><button class="button small" (click)="act(check.key, 'clear')">
                      Clear confirmation
                    </button>
                  </div>
                }
              </div>
              <ad-status-pill
                [tone]="
                  check.status === 'done' ? 'success' : check.status === 'unused' ? 'neutral' : 'caution'
                "
                >{{ check.status }}</ad-status-pill
              >
            </article>
          }
        </section>
      }
      <section class="surface setup-manual">
        <h2>User manual</h2>
        <div [innerHTML]="manual" (click)="follow($event)"></div>
      </section>
    }`,
})
export class SetupComponent implements OnInit {
  private api = inject(DataService);
  private router = inject(Router);
  data: any;
  manual = "";
  notes: Record<string, string> = {};
  error = "";
  busy = false;
  ngOnInit() {
    this.load();
  }
  async load() {
    this.busy = true;
    try {
      this.data = await this.api.get("setup");
      this.manual = this.data.manual || "";
    } catch (e) {
      this.error = errorMessage(e);
    } finally {
      this.busy = false;
    }
  }
  follow(event: MouseEvent) {
    const a = (event.target as HTMLElement).closest("a");
    if (a?.getAttribute("href")?.startsWith("/")) {
      event.preventDefault();
      this.router.navigateByUrl(a.getAttribute("href")!);
    }
  }
  route(url: string) {
    const match = url.match(/\/admin\/([^/]+)\/([^/]+)\/(?:(\d+)\/change\/|(add)\/)?/);
    if (!match) return "/";
    const key = match[1] + "." + match[2];
    if (key === "reports.report") return "/reports";
    if (key === "setup.setupconfirmation") return "/setup";
    if (key === "configuration.systemsetting") return "/manage/settings";
    return "/manage/" + key + (match[3] ? "?record=" + match[3] : match[4] ? "?record=new" : "");
  }
  async act(check: string, action: string) {
    try {
      this.data = await this.api.post("setup", { check, action, note: this.notes[check] || "" });
    } catch (e) {
      this.error = errorMessage(e);
    }
  }
}
