import { NgModule } from "@angular/core";
import { RouterModule, Routes } from "@angular/router";
import { RecordsComponent } from "./records.component";
import { SettingsComponent } from "./settings.component";
const routes: Routes = [
  { path: "settings", component: SettingsComponent },
  {
    path: ":key",
    component: RecordsComponent,
    canDeactivate: [(component: RecordsComponent) => component.mayLeave()],
  },
];
@NgModule({ imports: [RouterModule.forChild(routes)] })
export class ManagementModule {}
