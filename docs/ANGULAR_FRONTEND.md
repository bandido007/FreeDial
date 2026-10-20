# AlgoDial Angular frontend

The user approved an independent Angular frontend using the architecture and design of `/home/administrator/scc-frontend`, with feature scope limited to the pages already implemented in AlgoDial. The implementation lives in `frontend/`; the SCC checkout is a reference and is not changed or required at runtime.

## Running it

Run the normal Django/brain/writer/live stack, then `make frontend-build`. Open **http://127.0.0.1:8000/app/**. Existing AlgoDial credentials, roles and agent assignments apply. Direct links and browser refresh work on all Angular routes. The original Django pages remain available during migration.

For UI development, `make frontend` starts Angular on 127.0.0.1:4240 with a proxy to Django on 8000. Node.js 22.23+ is required. Production uses the compiled build on Django's existing origin, not the development server.

## Design reference and AlgoDial identity

- Angular 22, feature NgModules with lazy routes, standalone shared components, root NgRx store and Apollo GraphQL client.
- A sidebar and topbar shell, semantic light/dark tokens, Material theme bridge, surfaces, forms, tables, status pills, segmented controls, skeletons, keycaps and spinners.
- Feature folders (`guest`, `page`, `modules`), services, guards, shared UI and a small root store. Only dependencies used by these patterns are installed.

AlgoDial retains its logo, Atkinson Hyperlegible font, teal identity, call-state colours and operational language. SCC's muted text colours are adjusted for contrast. No SCC business menus, workflow engine, client connections, financial-year selector, messaging application or unrelated features are included.

AlgoDial's presentation uses its own light/dark surfaces, spacing and page composition. The overview and searchable sidebar organise the existing permission-filtered tools into people, routing, AI, calls, access and centre settings. Agent and supervision shortcuts lead directly to the existing desks. Mobile navigation supports focus trapping and Escape. Record editors provide an independently scrolling form and a persistent save footer, alongside clearer search, filters, selection and sorting feedback. Desk panels and sign-in share the same visual language; call controls and state colours retain their operational meaning.

## Existing pages and their Angular routes

| Existing AlgoDial surface | Angular route | Behaviour retained |
| --- | --- | --- |
| OAuth desk sign-in | `/app/login` | Email/password, OTP and required account steps |
| Agent desk | `/app/agent` | Phone setup/health, Ready/Not Ready with reasons, incoming call, answer, hold, transfer/consult/conference, notes, wrap-up, statistics and AI handoff context |
| Supervisor desk | `/app/supervisor` | Existing team/queue data, agent history/state actions, monitor/whisper/barge and stop |
| Existing admin index | `/app/` | Navigation filtered by the signed-in user's permissions |
| Registered management pages | `/app/manage/<app.model>` | Search, declared filters, date range where supported, sorting, paging, add/change/delete, native inline forms, registered bulk actions and change history |
| User password change | User editor | Native validation and optional password sign-in disabling |
| System settings | `/app/manage/settings` | Declared setting types, choices, limits, save and restore default |
| Reports | `/app/reports` | Existing tables, date periods and PDF/Excel/CSV exports |
| Setup guide | `/app/setup` | Live checklist, confirm/clear tests, and the existing user manual |

The management catalogue comes from registered AlgoDial ModelAdmins in an explicit allowlist of existing apps. It covers agents, skills, teams, routing/queues, telephony, AI configuration, users, roles, authentication records, notifications, attachments, audit and call records as permitted by the current admin. Writer-owned records remain read-only. Recording and voicemail playback use the existing protected media endpoints.

## Backend boundary

`apps/workspace` is a JSON adapter for the **existing** management pages. It uses their registered ModelAdmins, form classes, inline formsets, scoped querysets, permission methods, save hooks and delete policies. It does not implement a second set of call or configuration rules. It validates the parent and every inline before saving atomically. This preserves SIP provisioning and configuration-change notifications from the existing service hooks.

Call commands, reports exports, authentication and account steps use the existing GraphQL/OAuth APIs. Desk updates use the existing `/ws/agent` and `/ws/supervisor`. Django exposes the same APIs under `/api/` for the Angular client. No new database models or migrations are introduced.

The adapter exposes `/workspace/config`, `bootstrap`, `catalogue`, `records`, `choices`, `action`, `history`, `settings`, `reports`, `setup` and `user-password`, also reachable under `/api/workspace/`. Only public OAuth client configuration and compiled application files are anonymous. Management endpoints require an unrestricted active staff OAuth session and the native page/object permissions. Bootstrap permits authenticated non-staff agents.

## Phone lifecycle

The Angular desk components host AlgoDial's existing framework-independent DOM/media controllers. `scripts/sync_desk_frontend.py` generates scoped markup/styles and lifecycle adapters from the original source. Angular owns authentication, routing, theme, configuration and cleanup. The controllers retain their existing GraphQL/WebSocket/JsSIP behaviour; call decisions remain in the brain.

A route reuse strategy retains the desk when navigating to another workspace page, preserving the phone and active call. It distinguishes agent and supervisor routes, destroys cached desks at logout, and prevents a departing view being cached after logout. Controller disposal aborts listeners, clears timers and shuts down media/transports. Sign-out ends the existing backend desk sessions before revoking OAuth, and is blocked during a call/wrap-up.

This is a deliberate migration seam: the original desk and Angular desk share the tested call controller. A future conversion of each DOM action into Angular bindings can happen separately without changing the telephony protocol.

## Authentication and data handling

OAuth tokens live in this tab's session storage. Refresh is single-flight, with generation checks to prevent an old request restoring a signed-out session. All Angular API requests explicitly omit cookies, so an existing Django admin cookie cannot change the bearer user's identity. Password hashes, stored secrets and encrypted credentials are not serialized into form metadata. Secret inputs are blank and one-time native provisioning messages retain `no-store` responses.

The compiled entry uses Django's existing security headers, a per-response Angular style nonce, microphone permissions and a same-origin base URI. Assets are served separately; missing assets return 404 and resolved paths must stay inside the build directory. Media is fetched with OAuth into a temporary blob URL.

## Verification and releases

```bash
npm run format:check --prefix frontend
npm run typecheck --prefix frontend
npm test --prefix frontend
.venv/bin/python scripts/sync_desk_frontend.py --check
make frontend-test
.venv/bin/python lab/agentpage.py --angular --no-ai --use-running-stack
```

`ANGULAR_TEST_URL=live` runs the real compiled app on Django's isolated test server/database. Management browser tests use real permission checks, forms and saves. Desk screenshot tests use the existing deterministic phone/brain fixtures. They capture 1366×768 and 1920×1080 in light and dark, assert horizontal fit, and run axe WCAG checks. Screenshots are in `test-artifacts/angular/`; `.venv/bin/python scripts/ui_gallery.py` creates `test-artifacts/index.html` with Angular selected by default. Live-lab checks separately exercise actual Asterisk media and supervision.

CI builds the pinned frontend, checks formatting/types/unit tests, passes the build to Django browser tests, and checks generated desk source drift. `scripts/deploy.sh` builds the frontend before migrations/services restart and keeps the previous compiled UI for rollback; the release host needs Node.js and npm in addition to Python. No production deployment is performed by local builds.

## Validation on 19 October 2026

- Production Angular build, strict TypeScript checks, Python types, formatting and all nine import contracts pass. Four frontend unit tests pass.
- All 37 focused API/browser/security cases have passed across the final runs. These include real management saves, cookie isolation, role restrictions, dirty-form sign-out, retained phone sessions, OTP sign-in, required password/MFA enrollment and recovery-code display.
- The screenshot matrix contains 108 English captures across 1366×768 and 1920×1080, light and dark, plus 11 Swahili laptop captures. Every capture passes axe checks and horizontal-fit assertions; transfer actions also remain inside the laptop viewport.
- The 932-case repository regression run measured 86.89% backend coverage (85% required). Its six findings were corrected and verified in focused reruns; the other 926 cases passed in that run.
- All 37 live Asterisk checks pass through `/app/`: two-way browser audio, hold/resume, wrap-up, backend event history, recordings, supervisor monitor/whisper/barge, consult/conference, blind transfers and report totals. Live AI calls were not repeated; the AI handoff presentation uses the retained controller and deterministic browser fixtures.
- `npm audit --omit=dev` reports zero runtime advisories. No production deployment was performed.

## UI polish validation on 20 October 2026

- Production build, strict TypeScript, formatting, generated desk drift check and five frontend unit tests pass. The added state test prevents a different account inheriting the previous user's tool catalogue.
- All 22 focused browser cases pass across the final runs: two light management-layout cases, then the remaining 20 after correcting dark-mode Delete-label contrast. Checks cover real saves, a visible save footer, mobile page search/navigation, keyboard focus, retained phone sessions, supervisor monitoring, sign-in/account steps and Swahili desk labels.
- The 1366×768 and 1920×1080 screenshot matrix is refreshed in light and dark, with axe accessibility and horizontal-fit checks. A separate 390×844 mobile capture verifies navigation and record-list layout. The gallery is `test-artifacts/index.html`.
- The local app at `/app/` serves the rebuilt frontend. Backend regression and live Asterisk checks were not repeated for this presentation pass; their earlier results remain documented above.
