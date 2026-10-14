# Getting started

From a clone of the starter to your first business feature, step by step.

## 1. Requirements

- Python 3.12 (`python3.12 --version`)
- PostgreSQL 16 and Redis 7 — or Docker, and `make up` starts both plus Mailpit
- `make`, `git`
- Optional: `libldap2-dev libsasl2-dev` only if you install the LDAP extra

## 2. Create your project

```bash
git clone <starter-repo> acme-backend && cd acme-backend
python3 scripts/init_project.py --name "Acme Portal" --slug acme --reset-git
```

`init_project.py` renames the project, removes the example app (keep it with
`--keep-example` while you learn the conventions), optionally removes modules you
will never need (`--without sso`, `--without workflows`), writes a `.env` with
fresh keys, refreshes the generated files, starts a new git history and deletes
itself. Run it with `--dry-run` first to see what it touches.

## 3. Install and run

```bash
make init          # venv + dependencies + .env + migrate + seed
make up            # PostgreSQL, Redis, Mailpit (skip if you run them yourself)
make run           # API on http://127.0.0.1:8000
```

`make seed` wrote the permission catalogue, the notification templates, the
shipped workflow designs, the first-party OAuth2 application and — when
`DEFAULT_ADMIN_PASSWORD` is set in `.env` (init sets one) — the first
administrator. E-mails sent in development land in Mailpit at
<http://localhost:8025>.

Try it in GraphiQL at <http://127.0.0.1:8000/graphql>:

```graphql
query { getPublicSettings { data { projectName values } } }
```

Sign in (any HTTP client):

```
POST /oauth2/access_token
Content-Type: application/x-www-form-urlencoded

grant_type=password&username=admin@example.org&password=<DEFAULT_ADMIN_PASSWORD>&client_id=<OAUTH_CLIENT_ID>
```

`OAUTH_CLIENT_ID` is printed by `make seed` (or set it in `.env` before seeding to
choose it). Send the `access_token` as `Authorization: Bearer …` and:

```graphql
query { meQuery { data { attributes { email } authorities roles { name } } } }
```

## 4. Your first app

```bash
make app name=invoices
make migrations app=invoices && make migrate
make seed            # the app's permissions
make schema          # docs/schema.graphql now has your operations
make test
```

The generated `apps/invoices/` is a working CRUD module with the project's
layering, permissions, status codes and tests. Replace its `Invoice` model with
your real one and follow the file comments; [ADDING_AN_APP.md](ADDING_AN_APP.md)
explains every file an app can ship.

## 5. Day-to-day

| Task | Command |
|------|---------|
| Run the API / worker / beat | `make run` · `make worker` · `make beat` |
| Tests, fast | `make test-fast` (stop at first failure, no coverage) |
| Everything CI runs | `make ci` |
| Format + fix | `make format` |
| New migration | `make migrations app=<label>` |
| Refresh generated files | `make schema` · `make workflow-docs` |
| Add / upgrade a dependency | edit `requirements/*.in`, then `make deps` (or `make deps-upgrade`) |
| Fresh keys | `make secret-key` · `make encryption-key` · `make oidc-key` |
| Rotate stored secrets after a key change | `python manage.py rotate_secrets` |
| Django shell | `make shell` |

## 6. Configuration

Everything is read from the environment (`.env` locally). `.env.example` documents
each variable and marks the ones production requires. Some policies are also
editable at runtime by administrators on the *System settings* screen; the
precedence is **saved setting → environment → default** (see `apps/core/hooks.py`).

Settings modules: `config.settings.development` (default), `.testing` (pytest),
`.production` (refuses to start with development defaults — see
[SECURITY.md](SECURITY.md)).

## 7. Deploying

Bare metal, three systemd services behind nginx. The one-time host setup and the
release procedure (`scripts/deploy.sh <tag|branch|sha>`, also what CI runs over
SSH) are in [DEPLOYMENT.md](DEPLOYMENT.md). `make deploy-check` proves a
production `.env` is complete before you ship it — the server refuses to start
with a development default and lists every missing or unsafe value.

Production needs, at minimum: `SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS`,
`DATABASE_URL` (real password), `REDIS_URL` / `CELERY_BROKER_URL`,
`ENCRYPTION_KEYS`, `CORS_ALLOWED_ORIGINS`, `FRONTEND_DOMAIN`, SMTP settings,
`AXES_ENABLED=True`, `LDAP_SIMULATE_DIRECTORY=False`, `SMS_ALLOW_LOG_PROVIDER=False`.

## 8. Where things are explained

- [ARCHITECTURE.md](ARCHITECTURE.md) — the layering, the request path, the
  registries, the platform hooks
- [ADDING_AN_APP.md](ADDING_AN_APP.md) — the contract of a business app
- [API.md](API.md) — envelope, codes, pagination, every operation by module
- [SECURITY.md](SECURITY.md) — what is protected and how
- [DEPLOYMENT.md](DEPLOYMENT.md) — host setup, releases, operating
- [UPDATING_FROM_STARTER.md](UPDATING_FROM_STARTER.md) — pulling starter
  improvements into a project later
- `WORKFLOW_CATALOGUE.md` and `schema.graphql` — generated; do not edit
