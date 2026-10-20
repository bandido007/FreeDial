import { createAction, createReducer, on, props } from "@ngrx/store";
import { Bootstrap, Resource } from "../common";
export interface WorkspaceState {
  profile: Bootstrap | null;
  resources: Resource[];
  busy: boolean;
}
export const signedIn = createAction("[Session] Loaded", props<{ profile: Bootstrap }>());
export const catalogueRequested = createAction("[Workspace] Catalogue requested");
export const catalogueFailed = createAction("[Workspace] Catalogue failed");
export const catalogueLoaded = createAction(
  "[Workspace] Catalogue loaded",
  props<{ resources: Resource[] }>(),
);
export const signedOut = createAction("[Session] Signed out");
export const reducer = createReducer<WorkspaceState>(
  { profile: null, resources: [], busy: false },
  on(signedIn, (s, { profile }) => ({
    ...s,
    profile,
    resources: s.profile?.user.uid === profile.user.uid ? s.resources : [],
    busy: false,
  })),
  on(catalogueRequested, (s) => ({ ...s, busy: true })),
  on(catalogueFailed, (s) => ({ ...s, busy: false })),
  on(catalogueLoaded, (s, { resources }) => ({ ...s, resources, busy: false })),
  on(signedOut, () => ({ profile: null, resources: [], busy: false })),
);
