# Architecture

How the backend is put together, and why. Read this once before adding code;
[ADDING_AN_APP.md](ADDING_AN_APP.md) is the practical companion.

## 1. Shape

```
                 SPA / other clients
                        │
     ┌──────────────────┼───────────────────────┐
     │  POST /graphql   │  /oauth2/*  /files/*  │  /healthz  /readyz  /admin
     │  (graphene)      │  (DRF views)          │
     └──────────────────┴───────────────────────┘
                        │  one envelope, one error policy, one request id
          ┌─────────────┴─────────────┐
          │  resolvers / views (thin) │   read args → call one service/selector → wrap
          └─────────────┬─────────────┘
          ┌─────────────┴─────────────┐
          │  services (writes+rules)  │   selectors (reads)      ← all business logic
          └─────────────┬─────────────┘
          ┌─────────────┴─────────────┐
          │  models (BaseModel)       │   UUIDv7, timestamps, actor FKs, soft delete
          └───────────────────────────┘
   side effects: signals → notifications (Celery) · audit trail · workflow engine
```

Three rules hold everywhere:

1. **Resolvers are thin.** A GraphQL resolver or DRF view reads the request,
   validates the arguments, calls *one* service or selector, and wraps the result
   in the envelope. It never contains a rule.
2. **Services own writes and rules; selectors own reads.** A service takes
   keyword-only arguments, checks the rule first, writes second, and raises an
   `ApplicationError` subclass with a status code when the rule says no. A selector
   never mutates. Neither knows GraphQL exists — the same service serves a
   mutation, a management command and a Celery task.
3. **Dependencies flow one way.** `import-linter` (in `make lint`) enforces the
   layer order; a business app may import any foundation app, a foundation app
   never imports a business app, and `core` imports no app at all.

```
your apps  →  example  →  sso  →  authentication  →  users  →  workflows  →  audit
           →  rbac | notifications | attachments | configuration  →  core
```

Where a lower layer must *react* to a higher one, it listens to a signal the
lower layer defines (see §5) — `users` announces `user_registered`;
`authentication` and `notifications` react.

## 2. The request path

`config/settings/base.py` → `MIDDLEWARE`, in order of what matters:

| Middleware | Job |
|------------|-----|
| `RequestIdMiddleware` (core) | Reads or mints `X-Request-ID`, stores it in a `contextvar` so every log line and audit row carries it, echoes it on the response, and runs the **after-request queue** (§6) once the response is built |
| `CurrentUserMiddleware` (core) | Puts the authenticated user in a `contextvar` — the audit trail reads the actor from there, so services need no `actor=` plumbing |
| `RequestAuditMiddleware` (audit) | Times every API transaction, classifies it INFO / WARNING / ERROR, stores a sanitised copy of the inputs (secrets masked), and writes the endpoint log |
| `SecurityHeadersMiddleware` (core) | CSP and Permissions-Policy — a strict policy on API responses, a workable one on the admin and GraphiQL pages |
| `OAuth2TokenMiddleware` + `FirstPartyOAuth2Backend` | Bearer tokens → `request.user`, **only** for tokens with a first-party scope (identity tokens from the SSO module are refused) |
| `AxesMiddleware` | Lockout bookkeeping |

GraphQL requests then pass through `HardenedGraphQLView` (`apps/core/graphql/views.py`):
introspection only when `GRAPHIQL` is on, query **limits** checked before execution
(depth, complexity, aliases, root fields, batch size — `apps/core/graphql/limits.py`),
and graphene middleware `RestrictedSessionMiddleware` (`apps/authentication/middleware.py`),
which allows a restricted session (must change password, must set up 2FA) to call
only the operations needed to comply.

Resolvers are decorated, innermost first:

```python
@staticmethod
@has_permission(permissions=[VIEW_INVOICES])  # or @authenticated()
@log_errors_and_warnings  # ApplicationError → envelope; anything else → 9004 + request id
def resolve_get_invoice(root, info, uid): ...
```

`log_errors_and_warnings` is where the error policy lives: an expected failure
(`ApplicationError`) becomes its code and message; an unexpected exception becomes
code `9004` with a generic message and the request id — the traceback goes to the
endpoint log through the `api_error` signal, never to the client (unless
`EXPOSE_ERROR_DETAILS` is on, which production forbids).

## 3. The envelope

Every operation answers the same shape (`apps/core/graphql/response.py`):

```json
{ "status": true, "code": 9001, "message": "Operation successful", "data": {…}, "dataList": [] }
```

Paginated operations (`Page` fields) answer `{status, code, message, content, page}`
with `page = {number, size, totalElements, totalPages, hasNext, hasPrevious}`.
Messages are resolved from the status-code catalogue in the request's language
(`Accept-Language`, first of `API_LANGUAGES` as fallback).

Status codes are declared by each app in `status_codes.json`. The foundation owns
`8000–9999`; business apps use `10000+` in a band they declare on their `AppConfig`
(`status_code_range`). A system check (`starter.E001–E005`) fails on collisions,
missing translations or a code outside its band.

## 4. Registries: how an app plugs in

The starter has **no central file that lists what every app provides**. Each app
ships small declaration files; the platform discovers them (`apps/core/registry.py`
iterates installed apps). Adding an app is one line in `config/modules.py` and
`INSTALLED_APPS` — `make app` does both.

| File in the app | Read by | Effect |
|-----------------|---------|--------|
| `queries.py` / `mutations.py` (`Query`, `Mutation`) | `config/schema.py` | Merged into the root schema. Operation names must be unique across apps — a guard test fails on a collision |
| `permissions.py` (`GROUPS`, `ROLES`) | `apps/rbac/catalogue.py`, `make seed` | Permission catalogue written to the database; role editor groups; locked system roles |
| `status_codes.json` | `apps/core/status_codes.py`, system check | Envelope messages, bilingual |
| `settings_catalogue.py` (`SETTINGS`) | `apps/configuration` | Runtime settings on the System settings screen; `policy("KEY", default)` reads them |
| `notifications.py` (`CATEGORIES`, templates) | `apps/notifications/catalogue.py`, `make seed` | Categories with defaults and mandatory flags; templates seeded |
| `schedule.py` (`BEAT_SCHEDULE`) | `config/schedule.py` | Merged into `CELERY_BEAT_SCHEDULE`; duplicate keys fail loudly |
| `workflows.py` (`WORKFLOWS`, `NOTES`, `AREA_ORDER`) | the workflows module's catalogue, `make seed`, `make workflow-docs` | Designs installed and published; catalogue document and template screen generated (optional module) |
| `seed.py` (`ORDER`, `run(command)`) | `manage.py seed` | A seed step, run in `ORDER` |
| `tasks.py` | Celery autodiscovery | Tasks |
| `receivers.py` (`connect()`), called from `AppConfig.ready` | — | Signal receivers |

Models under `apps/` that subclass `BaseModel` are **audited automatically**
(`apps/audit/signals.py`); a model opts out with `AUDIT_EXCLUDE = True` and masks
fields with `AUDIT_MASK_FIELDS`. A file purpose is registered with
`attachments.policies.register(purpose, rule, bucket=…)` from `ready()`.

## 5. Platform hooks and signals

`apps/core/hooks.py` is the seam between `core` and the apps above it, so `core`
can offer behaviour without importing anyone:

- `api_error` — a signal the decorators send; `audit` listens and writes the endpoint log.
- `set_permission_resolver` / `resolve_permissions(user, request)` — `rbac`
  registers the resolver; `core`'s `@has_permission` and `attachments`' policies
  call it. Results are cached per request.
- `set_policy_provider` / `policy(name, default)` — `configuration` registers the
  provider; the precedence is *saved setting → Django setting → default*.

Domain signals (Django `Signal`s) let lower layers react to higher ones:

| Signal | Sent by | Listened to by |
|--------|---------|----------------|
| `user_registered`, `user_invited`, `email_changed`, `account_gated`, `account_deleted` | `users.signals` | `authentication` (session revocation, audit events), `notifications` |
| `workflow_started`, `workflow_advanced`, `workflow_closed` | `workflows.signals` | the app whose entity is in the workflow (the example app stamps its status from these) |

## 6. Two things about transactions

- **Each resolver runs in a transaction** (`log_errors_and_warnings` wraps the call in
  `transaction.atomic()`; `ATOMIC_REQUESTS` is off so a batch of operations does not
  share one), so a failed write leaves nothing behind. But some records must survive a rollback —
  the audit row of a *failed* sign-in, for instance. `core.context.after_request(callback)`
  queues work that `RequestIdMiddleware` runs after the response is built, outside
  any transaction. `authentication.events` uses it for failure events.
- **Counters that defend against brute force live in the cache, not the database**
  (OTP attempts, throttles, MFA tickets), so a resolver that raises — and rolls back —
  cannot reset them.

## 7. Background work

Celery with Redis. `queue()` in `notifications.services` creates the notification
row and hands delivery to a task on `transaction.on_commit`, so nothing is sent for
a write that rolled back. The beat schedule is assembled from every app's
`schedule.py`; the shipped entries purge expired tokens, OTPs, logs and unverified
accounts, retry notifications, send the daily error digest, and archive expired
example announcements. `CELERY_TASK_ALWAYS_EAGER=True` in development runs tasks
inline so no worker is needed.

## 8. Settings

`config/settings/base.py` reads everything from the environment with
`django-environ` and safe local defaults. `development.py`, `testing.py` and
`production.py` override the few things that differ. `production.py` ends by calling
`config/checks.py:validate_production_settings`, which raises with **every** unsafe
value listed — a deployment is fixed in one pass.

Optional modules are toggled by `ENABLE_WORKFLOWS`, `ENABLE_SSO_PROVIDER`,
`ENABLE_EXAMPLE_APP`. Every line that exists only for a module carries a
`# module:<name>` marker; `scripts/init_project.py` removes a module by deleting its
folder and those lines, leaving no dead references.

## 9. Testing strategy

- `conftest.py` provides the scene-setting fixtures; `gql()` executes an operation
  through the real schema *and* the graphene middleware, so restricted-session rules
  are exercised.
- Each foundation app has service tests (rules), API tests (envelope + permissions)
  and, where relevant, HTTP tests through the Django test client (token endpoints,
  file endpoints, middleware).
- `tests/test_api_contract.py` guards the contracts: schema snapshot current,
  operation names unique, every permission a resolver requires is declared, every
  declared permission is used, status codes valid, modules installed.
- Foundation tests never depend on the example app — it can be removed.

## 10. What is deliberately not here

- No frontend: the SPA is a separate repository. `scripts/validate_fe_docs.py` only
  checks the legal documents the backend links to.
- No multi-tenancy: add it as a business concern (a `tenant` FK on `BaseModel`
  subclasses and a selector filter) when a project needs it.
- No GraphQL subscriptions: in-app notifications are polled
  (`getMyUnreadNotificationCount`).
