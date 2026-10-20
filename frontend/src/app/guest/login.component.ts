import { CHANGE_MY_PASSWORD, CONFIRM_MFA, BEGIN_MFA, ACCOUNT_REQUIREMENTS } from "../store/api.graphql";
import { ChangeDetectionStrategy, Component, inject } from "@angular/core";
import { CommonModule } from "@angular/common";
import { FormsModule } from "@angular/forms";
import { ActivatedRoute, Router } from "@angular/router";
import { AuthService } from "../services/auth.service";
import { DataService, errorMessage } from "../services/data.service";
import { BrandComponent } from "../shared/brand.component";
import { IconComponent } from "../shared/icon.component";
import { ThemeService } from "../services/theme.service";
@Component({
  changeDetection: ChangeDetectionStrategy.Eager,
  selector: "ad-login",
  standalone: true,
  imports: [CommonModule, FormsModule, BrandComponent, IconComponent],
  template: ` <div class="login-layout">
    <aside class="login-story">
      <ad-brand />
      <div>
        <span class="eyebrow">Your contact centre</span>
        <h1>Every call.<br />A clear next step.</h1>
        <p>Your people, queues, and conversations in one workspace.</p>
        <div class="login-proof">
          <ad-icon name="phone" /><span>Agent workspace</span><ad-icon name="users" /><span>Supervision</span
          ><ad-icon name="report" /><span>Reports</span>
        </div>
      </div>
      <small>AlgoDial contact centre</small>
    </aside>
    <main class="login-main">
      <button class="icon-button login-theme" (click)="theme.toggle()" aria-label="Switch colour theme">
        <ad-icon name="sun" />
      </button>
      <div class="login-card">
        <ad-brand />
        <p class="eyebrow">Welcome back</p>
        <h1>
          {{
            mode === "password"
              ? "Choose a new password"
              : mode === "mfa"
                ? "Set up two-factor authentication"
                : "Sign in to your workspace"
          }}
        </h1>
        <p class="muted">
          {{
            mode ? "Complete the required account step to continue." : "Use your AlgoDial email and password."
          }}
        </p>
        @if (recoveryCodes.length) {
          <p>Save these recovery codes somewhere safe. Each code works once.</p>
          <pre class="secret">{{ recoveryText }}</pre>
          <button class="button primary full" (click)="finish()">I saved my codes — continue</button>
        } @else {
          <form (ngSubmit)="submit()" #form="ngForm">
            @if (!mode) {
              <label
                >Email address<input
                  type="email"
                  id="email"
                  name="email"
                  [(ngModel)]="email"
                  autocomplete="username"
                  required /></label
              ><label
                >Password<input
                  type="password"
                  id="password"
                  name="password"
                  [(ngModel)]="password"
                  autocomplete="current-password"
                  required
              /></label>
            }
            @if (mode === "password") {
              <label
                >Current password<input
                  type="password"
                  name="current"
                  [(ngModel)]="password"
                  autocomplete="current-password"
                  required /></label
              ><label
                >New password<input
                  type="password"
                  name="new"
                  [(ngModel)]="newPassword"
                  autocomplete="new-password"
                  required
                  minlength="10" /></label
              ><label
                >Confirm new password<input
                  type="password"
                  name="confirm"
                  [(ngModel)]="confirmPassword"
                  autocomplete="new-password"
                  required
              /></label>
            }
            @if (otpNeeded || mode === "mfa") {
              <label
                >Two-factor code<input
                  name="otp"
                  [(ngModel)]="otp"
                  autocomplete="one-time-code"
                  inputmode="numeric"
                  required
              /></label>
            }
            @if (mode === "mfa" && mfaSecret) {
              <p>Add this key to your authenticator, then enter its code.</p>
              <code class="secret">{{ mfaSecret }}</code>
            }
            @if (error) {
              <p class="alert danger" role="alert">{{ error }}</p>
            }
            <button class="button primary full" type="submit" [disabled]="busy || form.invalid">
              {{ busy ? "Please wait…" : mode ? "Continue" : "Sign in" }}<ad-icon name="chevron" />
            </button>
          </form>
        }
        <p class="login-foot">Use the account provided by your contact centre.</p>
      </div>
    </main>
  </div>`,
  styleUrl: "./login.component.scss",
})
export class LoginComponent {
  auth = inject(AuthService);
  theme = inject(ThemeService);
  private api = inject(DataService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);
  email = "";
  password = "";
  newPassword = "";
  confirmPassword = "";
  otp = "";
  error = "";
  busy = false;
  otpNeeded = false;
  mode = "";
  mfaSecret = "";
  recoveryCodes: string[] = [];
  get recoveryText() {
    return this.recoveryCodes.join("\n");
  }
  async finish() {
    this.error = "";
    try {
      const requirements: any = await this.api.graph(ACCOUNT_REQUIREMENTS);
      if (requirements.mfaEnrolmentRequired) {
        const data: any = await this.api.graph(BEGIN_MFA, {}, true);
        this.mfaSecret = data.secret;
        this.mode = "mfa";
        this.otp = "";
        return;
      }
      await this.auth.bootstrap();
      const requested = this.route.snapshot.queryParamMap.get("next");
      await this.router.navigateByUrl(
        requested?.startsWith("/") && !requested.startsWith("//") ? requested : "/",
      );
    } catch (e: any) {
      this.error = errorMessage(e);
    }
  }
  async submit() {
    this.busy = true;
    this.error = "";
    try {
      if (this.mode === "password") {
        await this.api.graph(
          CHANGE_MY_PASSWORD,
          { old: this.password, next: this.newPassword, confirm: this.confirmPassword },
          true,
        );
        this.mode = "";
      } else if (this.mode === "mfa") {
        const data: any = await this.api.graph(CONFIRM_MFA, { otp: this.otp }, true);
        this.recoveryCodes = data.recoveryCodes;
        this.mode = "";
        return;
      } else {
        const reply = await this.auth.login(this.email, this.password, this.otp);
        if (reply.requirements?.password_change_required) {
          this.mode = "password";
          return;
        }
        if (reply.requirements?.mfa_enrolment_required) {
          const data: any = await this.api.graph(BEGIN_MFA, {}, true);
          this.mfaSecret = data.secret;
          this.mode = "mfa";
          return;
        }
      }
      await this.finish();
    } catch (e: any) {
      if (e?.error?.code === 8014 || e?.code === 8014) this.otpNeeded = true;
      this.error = errorMessage(e);
    } finally {
      this.busy = false;
    }
  }
}
