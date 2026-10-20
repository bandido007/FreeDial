import { ChangeDetectionStrategy, Component, NgModule, provideZoneChangeDetection } from "@angular/core";
import { BrowserModule } from "@angular/platform-browser";
import { provideHttpClient, withInterceptors } from "@angular/common/http";
import { RouteReuseStrategy } from "@angular/router";
import { DialogModule } from "@angular/cdk/dialog";
import { StoreModule } from "@ngrx/store";
import { AppRoutingModule } from "./app-routing.module";
import { GraphQLModule } from "./graphql.module";
import { reducer } from "./store";
import { authInterceptor } from "./guards/auth-interceptor";
import { DeskReuseStrategy } from "./guards/desk-reuse.strategy";
@Component({
  changeDetection: ChangeDetectionStrategy.Eager,
  selector: "app-root",
  standalone: false,
  template: "<router-outlet/>",
})
class AppComponent {}
@NgModule({
  declarations: [AppComponent],
  imports: [
    BrowserModule,
    DialogModule,
    AppRoutingModule,
    GraphQLModule,
    StoreModule.forRoot({ workspace: reducer }),
  ],
  providers: [
    provideZoneChangeDetection(),
    provideHttpClient(withInterceptors([authInterceptor])),
    { provide: RouteReuseStrategy, useClass: DeskReuseStrategy },
  ],
  bootstrap: [AppComponent],
})
export class AppModule {}
