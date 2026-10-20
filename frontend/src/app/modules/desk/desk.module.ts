import { NgModule } from "@angular/core";
import { RouterModule } from "@angular/router";
import { AgentComponent } from "./desk.component";
@NgModule({
  imports: [RouterModule.forChild([{ path: "", component: AgentComponent, data: { retainDesk: true } }])],
})
export class AgentDeskModule {}
