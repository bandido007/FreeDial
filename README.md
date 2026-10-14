# AlgoDial

A production-grade Django backend that every new project starts from. It ships the
parts every system needs and nobody wants to write twice — accounts, sign-in, roles
and permissions, notifications, files, audit trails, runtime settings, approval
workflows — hardened, tested, and laid out so the first business feature lands in
an afternoon.

Clone it, run one script, and start on *your* domain.

```bash
git clone <this-repo> acme-backend && cd acme-backend
python scripts/init_project.py --name "Acme Portal" --slug acme --reset-git
make init        # virtualenv, dependencies, .env with fresh keys, migrate, seed
make run         # http://127.0.0.1:8000/graphql
```

Then `make app name=invoices` for your first business app. See
[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md) for the full walk-through.

---

## What you get

| Area | What is included |
|------|------------------|
| **Accounts** (`users`) | Self-registration with e-mail OTP, invitations, profile completeness gate, versioned consents, account status (active / suspended / blocked), e-mail and phone change with verification, account deletion with cooling-off, GDPR-style data export, disposable-address refusal |
| **Sign-in** (`authentication`) | OAuth2 password grant for the first-party SPA, refresh & revoke, account lockout (django-axes) **plus** per-address throttling, two-factor (TOTP with replay protection, recovery codes, admin reset), password policy (history, expiry, breached-password check), forgot-password and invitation links (hashed tokens), restricted sessions (must change password / must set up 2FA), session list & remote sign-out, new-device alerts, login history, authentication audit trail, external sign-in through any OpenID Connect provider (Google, Keycloak, Microsoft…), LDAP/Active Directory |
| **Access control** (`rbac`) | Declarative permission catalogue (each app ships its `permissions.py`), roles with locked system roles, per-request permission cache, `@has_permission` on resolvers, guard tests that stop dead or undeclared codes |
| **Notifications** (`notifications`) | E-mail, SMS and in-app channels; templates per category with variables; per-user preferences (mandatory categories can't be muted); managed SMTP/SMS configurations with test-send; retry queue via Celery; delivery log |
| **Files** (`attachments`) | Upload (multipart or base64) with type sniffing, size limits, image normalisation, optional ClamAV; access policies per purpose (a file's URL is never a permission); attachment-type catalogue; public files; S3/MinIO or local disk |
| **Audit** (`audit`) | Automatic change trail for every model (who, when, before/after, secrets masked); endpoint log of every API transaction with error/warning classification; error work-queue with resolve/reopen; daily error digest; per-user activity page; exports |
| **Runtime settings** (`configuration`) | Settings each app declares (`settings_catalogue.py`) and administrators change on one screen — typed, validated, cached, audited. `policy("KEY", default)` reads them anywhere |
| **Workflows** (`workflows`, optional) | Configurable multi-step approval engine: steps, actions, roles, SLAs; designs shipped as data by apps, published by administrators, driven by signals; catalogue document generated from code |
| **Single sign-on** (`sso`, optional) | This backend as an OpenID Connect provider: client-system registry, consent screen on the SPA, scoped identity tokens that can never be used as API keys |
| **Example app** (`example`, removed on init) | A complete reference business app (announcements with an approval workflow) showing every convention in use |

Cross-cutting: GraphQL-first API with one response envelope and bilingual status codes,
query depth/complexity/alias/batch limits, request IDs on every log line and error,
CSP + Permissions-Policy headers, encrypted secrets at rest with key rotation,
production fail-fast checks, health probes, structured JSON logs, Celery beat schedule
assembled from the apps, table exports (PDF / Word / Excel / CSV).

## Stack

Python 3.12 · Django 5.2 LTS · graphene-django 3 (GraphQL) · Django REST framework
(token & file endpoints) · django-oauth-toolkit · django-axes · Celery 5 + Redis ·
PostgreSQL 16 (psycopg 3) · pytest · ruff · mypy · import-linter · Docker.

## Commands

```
make help            every target, with a one-line description

make init            first run: venv + deps + .env + migrate + seed
make run             development server           make worker / make beat
make up / make down  PostgreSQL, Redis, Mailpit in Docker (local services only)

make test            the suite (coverage ≥ 85% enforced)
make lint            ruff + import-linter          make format
make typecheck       mypy                          make check   django checks + generated files current
make ci              lint → typecheck → check → test (what the pipeline runs)

make app name=…      scaffold a business app       make schema  refresh docs/schema.graphql
make seed            catalogue, templates, workflows, first admin
make secret-key / make encryption-key / make oidc-key
```

## Layout

```
config/            settings (base / development / testing / production), urls, schema,
                   modules.py (the one list of apps), schedule, celery, checks
apps/core/         cross-cutting building blocks only — never imports another app
apps/<app>/        one module each, same shape everywhere:
  models.py        BaseModel subclasses (UUIDv7 uid, timestamps, soft delete, audited)
  selectors.py     reads            services.py       writes + rules (raise ApplicationError)
  types.py         GraphQL output   inputs.py         GraphQL input
  queries.py       thin resolvers   mutations.py      thin mutations
  permissions.py   what it declares status_codes.json its error messages (en/sw)
  notifications.py templates        settings_catalogue.py   runtime settings
  schedule.py      beat entries     workflows.py      approval designs
  seed.py          seed step        tests/            pytest
docs/              ARCHITECTURE · GETTING_STARTED · ADDING_AN_APP · SECURITY · API · DEPLOYMENT ·
                   UPDATING_FROM_STARTER · schema.graphql · WORKFLOW_CATALOGUE.md
deploy/            systemd units (web, worker, beat) · nginx site
scripts/           init_project.py · new_app.py + app_template/ · write_env.py · deploy.sh
```

Dependencies flow one way and the build enforces it (`make lint`):

```
your apps  →  example  →  sso  →  authentication  →  users  →  workflows  →  audit
           →  rbac | notifications | attachments | configuration  →  core
```

## The API in one minute

Everything is GraphQL at `POST /graphql` (GraphiQL in development) except three
things that need real HTTP semantics: sign-in tokens (`/oauth2/…`), files
(`/files/…`) and health (`/healthz`, `/readyz`). The whole API is also mounted under
`/<API_MOUNT_PREFIX>/` for gateways.

Every operation answers the same envelope:

```json
{ "status": true, "code": 9001, "message": "Operation successful", "data": {…}, "dataList": [] }
```

Paginated operations add `content` and `page`. Codes and their messages (English
and Swahili) live in each app's `status_codes.json`; `9000` is not authorised, `9001`
success, `9004` an unexpected failure — whose message carries a **request id** you can
find in the endpoint log, never the exception text.

```graphql
mutation { register(input: { email: "asha@example.org", password: "…", firstName: "Asha", lastName: "M" }) { code message } }
mutation { validateRegistrationOtp(email: "asha@example.org", otp: "123456") { code } }
# POST /oauth2/access_token  grant_type=password&username=…&password=…&client_id=…  → tokens + user
query  { meQuery { data { attributes { email } authorities roles { name } mfaRequired } } }
```

The full schema is in [docs/schema.graphql](docs/schema.graphql) (regenerated by
`make schema`; a stale snapshot fails the build) and the guide is
[docs/API.md](docs/API.md).

## Security posture

Designed so the safe choice is the default and the unsafe one has to be typed:

- **Production refuses to start** with a development secret, `DEBUG=True`, `*` hosts,
  no encryption key, a default database password, localhost CORS, error details on,
  the SMS log provider, or a simulated directory — every problem listed at once.
- Unexpected errors answer a generic message and a request id; the traceback goes to
  the endpoint log for a developer with the right permission.
- Every DRF endpoint needs a first-party token unless it is explicitly public
  (a guard test lists the exceptions); identity tokens from the SSO module are never API keys.
- Account gates (suspended, blocked, deactivated, unverified) are re-checked on
  **every** request, not only at sign-in; flipping one revokes live sessions.
- Lockout by account + throttling by address; OTP attempts counted in the cache so a
  rolled-back transaction cannot reset them; TOTP steps burnt once used; reset and
  invite tokens stored hashed; recovery codes hashed.
- Files: policy per purpose, content sniffed against the extension, images re-encoded,
  `nosniff` on every download, private files answer 404 (never 403) to strangers.
- Secrets at rest (SMTP, LDAP, SMS, MFA) encrypted with Fernet and rotated with one
  command; change-trail diffs mask anything that looks like a secret — in snake_case
  and camelCase.
- GraphQL depth, complexity, alias, root-field, batch and page-size limits; CSP and
  Permissions-Policy headers; `X-Request-ID` echoed on every response.

Details and the threat model are in [docs/SECURITY.md](docs/SECURITY.md).

## Working on it

- **Tests**: `make test` runs against in-memory SQLite by default (set
  `TEST_DATABASE_URL` for PostgreSQL, as CI does). Fixtures in `conftest.py` give you
  `make_user`, `admin`, `grant`, `gql` (runs an operation through the real schema and
  middleware), `sign_in`, `api`, `mailbox`.
- **Quality gates** (`make ci`): ruff, import-linter layering, mypy with django-stubs,
  Django checks, migrations written, schema and workflow-catalogue snapshots current,
  85% coverage, dependency audit. Pre-commit runs the fast ones.
- **Generated files** are never hand-edited: `docs/schema.graphql` (`make schema`),
  `docs/WORKFLOW_CATALOGUE.md` (`make workflow-docs`), `requirements/*.txt` (`make deps`).
- **Deploying**: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — one-time host setup, then `scripts/deploy.sh <ref>`.
- **Adding a module**: `make app name=…` then read
  [docs/ADDING_AN_APP.md](docs/ADDING_AN_APP.md) — the files every app can ship and
  what the platform does with each.
- **Pulling starter improvements** into a project that was created from it:
  [docs/UPDATING_FROM_STARTER.md](docs/UPDATING_FROM_STARTER.md).

## Deployment

Bare metal: systemd runs gunicorn, the Celery worker and beat as an unprivileged
user; nginx terminates TLS in front. `deploy/` holds the unit files and the nginx
site, `scripts/deploy.sh` is the release procedure (install hashed requirements →
refuse unsafe settings → collectstatic → migrate → seed → restart → health check or
roll back), and the GitLab pipeline runs it over SSH after `lint → test → checks`.
Docker is used only for the local services (`make up`). Step by step:
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

---

*Licence: use it for your own projects freely; it is a starting point, not a framework.*
