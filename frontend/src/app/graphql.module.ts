import { NgModule, inject } from "@angular/core";
import { provideApollo } from "apollo-angular";
import { HttpLink } from "apollo-angular/http";
import { InMemoryCache } from "@apollo/client/core";
@NgModule({
  providers: [
    provideApollo(() => ({
      link: inject(HttpLink).create({ uri: "/api/graphql" }),
      cache: new InMemoryCache(),
    })),
  ],
})
export class GraphQLModule {}
