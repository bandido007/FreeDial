import { NgModule } from "@angular/core";
import { RouterModule } from "@angular/router";
import { SupervisorComponent } from "./desk.component";
@NgModule({
  imports: [
    RouterModule.forChild([{ path: "", component: SupervisorComponent, data: { retainDesk: true } }]),
  ],
})
export class SupervisorModule {}
