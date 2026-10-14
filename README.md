# AlgoDial

A contact-centre platform: Asterisk carries the media, AlgoDial's **brain** makes every
call decision through ARI, and the web app is where people work. AI agents hand calls
to humans by rule.

- **How the project works and the rules that never break:** [CLAUDE.md](CLAUDE.md)
- **What gets built, milestone by milestone:** [docs/POC_PLAN.md](docs/POC_PLAN.md)

## Three parts

| Part | Where | What it is |
| --- | --- | --- |
| Management | `config/`, `apps/` | The team Django starter: users, roles, audit, settings, GraphQL API, admin |
| Control | `brain/` | The call-control service (`manage.py run_brain`); `brain/core` is pure Python |
| Live | `apps/realtime/` (M3) | WebSocket push to agent and supervisor pages |

`contracts/` holds the command and event envelopes that travel between them on Redis
Streams. `lab/` keeps the first prototypes for reference.

## Getting started

Requirements: Python 3.12, Docker, `make`, and Asterisk with ARI (see CLAUDE.md, "The machine").

```bash
make up            # PostgreSQL :5442, Redis :6392, Mailpit :8025, all on 127.0.0.1
make venv          # .venv with the pinned, hashed requirements
make env           # .env with fresh keys (never overwrites); then set ARI_PASSWORD
make migrate seed  # database tables, permission catalogue, first administrator
make ci            # lint, import contracts, types, checks, tests: must be green
make run           # web on http://127.0.0.1:8000 (/admin, /graphql)
make brain-check   # can the brain reach ARI and Redis?
make brain         # run the brain
```

`make help` lists every target.

## Built on the team starter

The management part comes from the team Django starter (commit `263e849`), set up with
`scripts/init_project.py --without workflows --without sso`. Its documents still apply:
[architecture](docs/ARCHITECTURE.md), [adding an app](docs/ADDING_AN_APP.md),
[API](docs/API.md), [security](docs/SECURITY.md), [deployment](docs/DEPLOYMENT.md).
AlgoDial's own lines in shared files sit below a `# ── project ──` line or carry a
`# project` marker, so starter updates merge cleanly.
