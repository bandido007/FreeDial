import { describe, it, expect } from "vitest";
import { Bootstrap, Resource } from "../common";
import { reducer, signedIn, catalogueLoaded } from "./index";

describe("workspace account ownership", () => {
  it("clears the previous account's management catalogue when signing into another account", () => {
    const profile = (uid: string) => ({ user: { uid } }) as Bootstrap;
    let state = reducer(undefined, signedIn({ profile: profile("operator") }));
    state = reducer(state, catalogueLoaded({ resources: [{ key: "users.user" } as Resource] }));
    state = reducer(state, signedIn({ profile: profile("agent") }));
    expect(state.resources).toEqual([]);
    expect(state.profile?.user.uid).toBe("agent");
  });
});
