import { ChangeDetectionStrategy, Component, NgModule } from "@angular/core";
import { RouterModule, RouterLink, Routes } from "@angular/router";
import { authGuard, permissionGuard, staffGuard } from "./guards/auth.guard";
import { ShellComponent } from "./page/shell.component";
import { LoginComponent } from "./guest/login.component";
import { HomeComponent } from "./page/home.component";
@Component({
  changeDetection: ChangeDetectionStrategy.Eager,
  standalone: true,
  imports: [RouterLink],
  template: `<section class="surface empty-state">
    <h1>Page unavailable</h1>
    <p>This page does not exist or your role does not allow it.</p>
    <a class="button primary" routerLink="/">Open your workspace</a>
  </section>`,
})
class UnavailableComponent {}
const routes: Routes = [
  { path: "login", component: LoginComponent },
  {
    path: "",
    component: ShellComponent,
    canActivate: [authGuard],
    children: [
      { path: "", component: HomeComponent },
      {
        path: "agent",
        canActivate: [permissionGuard],
        data: { permissions: ["can_take_calls"] },
        loadChildren: () => import("./modules/desk/desk.module").then((m) => m.AgentDeskModule),
      },
      {
        path: "supervisor",
        canActivate: [permissionGuard],
        data: {
          permissions: ["can_monitor_calls", "can_whisper_calls", "can_barge_calls", "can_force_agent_state"],
        },
        loadChildren: () => import("./modules/desk/supervisor.module").then((m) => m.SupervisorModule),
      },
      {
        path: "manage",
        canActivate: [staffGuard],
        loadChildren: () => import("./modules/management/management.module").then((m) => m.ManagementModule),
      },
      {
        path: "reports",
        canActivate: [staffGuard, permissionGuard],
        data: { permissions: ["can_view_reports"] },
        loadComponent: () => import("./modules/reports/reports.component").then((m) => m.ReportsComponent),
      },
      {
        path: "setup",
        canActivate: [staffGuard, permissionGuard],
        data: { permissions: ["can_view_setup_guide"] },
        loadComponent: () => import("./modules/setup/setup.component").then((m) => m.SetupComponent),
      },
      { path: "not-allowed", component: UnavailableComponent },
      { path: "**", component: UnavailableComponent },
    ],
  },
];
@NgModule({
  imports: [
    RouterModule.forRoot(routes, { scrollPositionRestoration: "enabled", anchorScrolling: "enabled" }),
  ],
  exports: [RouterModule],
})
export class AppRoutingModule {}
