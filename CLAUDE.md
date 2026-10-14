# AlgoDial

AlgoDial is a contact-centre platform in the spirit of VICIdial and Wazo, with AI agents
that hand calls to humans by rule. Asterisk is the media engine. AlgoDial's own "brain"
makes every call decision through ARI. The web app is the main tool for every user.

Owner: Tostao. Explain every change in plain English: what changed, where, and why.
Do not ask him quiz questions. When a choice is his to make, state the options and your
recommendation with evidence (docs, source code, who does it this way), then wait.

## The machine (lab facts, verified)

| Thing | Value |
| --- | --- |
| Asterisk | 22.11.0 LTS, built from source in `~/asterisk-22`, runs as user `asterisk` |
| Asterisk config | `/etc/asterisk`, a git repo. Commit every change there with a clear message |
| SIP (PJSIP) | UDP transport on **5062** (5060 belongs to an old jambonz stack) |
| ARI | `http://127.0.0.1:8091/ari`, user `algodial`, app name `algodial`. Port 8088 is taken by InfluxDB and 8090 by Docker |
| Dialplan entry | Pattern `_8XX` in context `[lab]` (after `apply.sh m4`; before it, only `800`): `Stasis(algodial,inbound,${EXTEN},${CHANNEL(endpoint)},${CHANNEL(linkedid)})`; if `STASISSTATUS` is `FAILED` (no brain) it goes to `Answer()` + `Queue(support)` (members 1001, 1002). Changes to `/etc/asterisk` go through `sudo deploy/asterisk/apply.sh <step>` |
| WebSocket SIP | `transport-ws` (pjsip.conf). It opens no port of its own: browsers connect to Asterisk's HTTP server `/ws` on 127.0.0.1:8091 (the `0.0.0.0:5060` that `pjsip show transports` prints is a placeholder) |
| Start order | Start Docker (`make up`) before Asterisk is restarted, or Asterisk logs `PostgreSQL RealTime: Failed to connect` once; it reconnects by itself on the next lookup |
| CEL | `/var/log/asterisk/cel-custom/cel_trace.csv` (advanced `cel_custom` mapping; `apps=` must list apps by name, `all` does not work in 22.11) |
| Test phones | Account `1001` = Zoiper on the laptop (127.0.0.1:5062), `1002` = Zoiper on a phone on the LAN |
| PJSIP realtime | `res_config_pgsql` → AlgoDial PostgreSQL (127.0.0.1:5442), role `asterisk` (read-only on `ps_endpoints`, `ps_auths`, `ps_aors`). Sorcery reads `pjsip.conf` first (1001, 1002, transports), then the database. Tables by Asterisk's Alembic (`make pjsip-schema`). Applied by `deploy/asterisk/m1-pjsip-realtime.sh`; commits in `/etc/asterisk` are made as root |
| SIP check | `lab/sipcheck.py <id> <password>` registers like a phone: `200 OK` = works, `401` = unknown account or wrong password |
| Call tests | `make calltests` (stop your brain first): the 9 call tests (all passing on 2026-10-14) plus 10 ring-all, 11 voicemail, 12-17 transfers (each checked in CEL for one linkedid) and 18 supervision (each phone sends its own tone; who hears the supervisor is measured), dialled on lab entry **899** (needs `apply.sh m4`), on the real Asterisk with scripted phones (`lab/sipphone.py`) as `lab-customer`, `lab-agent-a`, `lab-agent-b` (agents 9001, 9002, queue Support, entry 899). Records every decision to `brain/sim/recordings/lab.jsonl`, which `brain/tests/test_replay.py` replays |
| Agent page | `http://127.0.0.1:8000/agent/` with `make dev` (web with WebSockets, brain, writer, live relay). Browser phone = JsSIP over `ws://127.0.0.1:8091/ws` (Asterisk's HTTP server, so this machine only); each sign-in gets its own account `web<agent number>` with a fresh password, switched off at sign-out. Browser accounts are `ulaw,alaw`: this Asterisk has no `codec_opus`. `make agentcheck` drives it in headless Chrome (M3), then two agents in two browsers do every transfer by clicking (M5), then a supervisor in Chrome switches monitor / whisper / barge while the customer's phone measures who hears them (M6). Supervisor page: `http://127.0.0.1:8000/supervisor/` (`LAB_SUPERVISOR_*` in `.env`, team Lab; demo supervisors asha@ and daudi@) `make agent-login` sets up two logins, passwords in `.env`: agent 7001 (`LAB_AGENT_*`, for people) and 9003 (`LAB_CHECK_AGENT_*`, used by `make agentcheck`), plus the wrap-up codes and Not Ready reasons |
| Demo centre | `make seed-demo`: the helpline of `docs/ROUTING_DESIGN.md` section 3 (8 agents with users `<name>@example.org`, password `DEMO_PASSWORD` in `.env`; skills, rule sets, 5 queues; entry 800 General/Swahili, 801 Providers, 802 General/English). The brain's routing tests T1-T12 use the same data (`brain/sim/demo.py`) |
| Voicemail | Overflow action "voicemail": the brain plays `sound:vm-intro` and records the caller over ARI (`callback-<call>.wav` in Asterisk's stored recordings), then a callback task appears in admin → Calls → Callback tasks with a player |
| Simulator | `make sim` (`a="--skills"` for the Swahili rule set): a virtual busy hour through the real brain core on a virtual clock (`brain/sim/simulator.py`), every queue strategy compared. Call test 10 (`make calltests t=10`) is ring-all on the real Asterisk |
| What Asterisk sends (recorded) | Reject (486) = `ChannelDestroyed` cause 17; ring timeout = cause 19; hung up by the brain = 16; offline phone = the originate itself fails `500 Allocation failed`. A customer's hangup sends only `StasisEnd`. ARI channel JSON has no linkedid, so the dialplan passes it as an arg |
| Repo | `~/AlgoDial`, built from the team starter `~/Django Starter/django-5` (commit `263e849`) |
| Local services | `make up` (Docker): PostgreSQL **5442**, Redis **6392**, Mailpit **8025** (UI) / **1025** (SMTP), all on 127.0.0.1. Web on 8000 |
| Lab prototype | `~/AlgoDial/lab/` (`brain.py`, `events.py`, `celtail.py`); `ari.env` (not in git) |
| Network | Laptop IP changes between networks. Anything on 127.0.0.1 is unaffected |

Before using any new port, check it is free: `sudo ss -ltnp | grep ':PORT'`.

## Architecture: three parts with different rules

- **Management** (`config/`, `apps/`): the Django starter, used as it is. Admin, configuration,
  users, roles (`rbac`), audit, reports, and the **API (GraphQL)** with the starter's envelope.
  Sign-in is OAuth2 (`POST /oauth2/access_token`, bearer tokens, TOTP, lockout). Agent and
  supervisor pages are plain JS pages served by Django that sign in the same way.
- **Control** (`brain/`): its own design, not Django-shaped.
  - `brain/core/`: pure Python. Call and agent state machines and every decision. No I/O,
    no `asyncio` sockets, no Django, no `apps`. An import-linter contract enforces this.
  - `brain/adapters/`: the outside world. `ari` (aiohttp REST + WebSocket), `bus` (Redis Streams),
    `config` (loads configuration through the ORM).
  - `brain/service.py`: wires core to adapters. Started by `manage.py run_brain`.
  - The ORM is used **only when loading config** (at start and on a `config.changed` command),
    never while handling call events.
  - `brain/sim/`: a fake ARI and scenarios with many virtual agents and calls.
- **Live** (`apps/realtime/`): a Channels WebSocket that checks the same OAuth token, fed by
  brain events, pushing to browsers.
- **Storage**: PostgreSQL for configuration, PJSIP realtime tables, call records and the Event
  log. Redis for streams, live state and the Channels layer.
- Later: a **media/AI worker** for the AI agent (ARI externalMedia over WebSocket, STT, LLM, TTS).

## Messaging

- **Commands web → brain** and **events brain → writer** travel on **Redis Streams**
  (`algodial:commands`, `algodial:events`), read through consumer groups. Delivery is
  at-least-once, so every handler is idempotent.
- **Pub/Sub or the Channels layer only for browser push.** A browser that misses a message
  re-reads the state when it reconnects; nothing that must not be lost goes this way.
- `contracts/` holds the **versioned** command and event schemas shared by web and brain. Every
  command has a `command_id`, every event an `event_id`. Within a version, changes are additive
  only; a breaking change is a new version.
- `apps/calls` has the **writer**: it reads the event stream and writes `Event`, `Call` and `Leg`.
- PJSIP realtime tables are created by Asterisk's Alembic scripts (`contrib/ast-db-manage`) and
  mapped in Django with `managed = False`. Django never migrates them.

## Management conventions (from the starter)

- API → services (writes, rules) / selectors (reads) → models. Resolvers stay thin; services
  raise `ApplicationError` subclasses with a status code. Details: `docs/ARCHITECTURE.md`,
  `docs/ADDING_AN_APP.md`.
- Models subclass `apps.core.models.BaseModel` (UUIDv7 `uid`, timestamps, soft delete, audited).
- Users sign in with their **email**. A user can hold several roles; access is the union of
  their permissions. An agent's number lives on `Agent`, not on `User`.
- Permissions are declared in each app's `permissions.py` and named `can_*`, for example
  `can_monitor_calls`, `can_whisper_calls`, `can_barge_calls`, `can_force_agent_state`,
  `can_listen_recordings`, `can_edit_queues`.
- `requirements/*.txt` are compiled with hashes from `*.in` (`make deps`); never edit them by hand.
  `docs/schema.graphql` is generated (`make schema`).

## Rules that must never be broken

1. **Asterisk is the truth for live calls.** On every (re)connect to ARI, the brain lists `/channels` and `/bridges` and reconciles its memory with them.
2. **One writer per piece of state.** Only the brain changes agent activity state and call state. The web asks the brain, it never writes them itself.
3. **Every `StasisStart` ends in a decision.** Unknown or missing `args` mean: log it and hang up. A channel is never left silent in Stasis.
4. **Agent legs are originated with `originator=<caller channel id>`**, so every leg of one call shares one `linkedid` (proved in the lab with CEL).
5. **The dialplan has a fallback**: if `STASISSTATUS` is `FAILED` (brain down), send the caller to a plain `Queue()`.
6. **Log the join keys on every `StasisStart`**: `channel.id` (uniqueid), `protocol_id` (SIP Call-ID), `args`, and the linkedid. Ids are always strings (some are UUIDs).
7. **Events per call are handled in order; different calls run in parallel** (one asyncio queue and task per call). Nothing slow (HTTP to an LLM, database writes) may block other calls.
8. **Cleanup is total.** When a call ends, every leg is hung up and every bridge AlgoDial created is deleted, even if one step fails.
9. **No secrets in git.** Passwords and API keys live in env files that are git-ignored.
10. **Config lives in the database**, is edited in admin pages, and the brain picks it up without a restart (the web sends a `config.changed` command).
11. **`brain/core` stays pure**: no I/O, and it imports neither `django` nor `apps`.
12. **Every command carries a `command_id`**, and handling the same command or event twice changes nothing the second time.

## How to work

- One milestone at a time from `docs/POC_PLAN.md`. Write the tests for its "Done when" first, then the code.
- For anything touching `/etc/asterisk`: show the diff, explain it, apply it, reload the right module, and commit in `/etc/asterisk`.
- After each milestone: run all tests, give a short summary, and list the commands Tostao should run to see it working.
- Commands: `make up` (services) · `make run` (web) · `make brain` · `make test` · `make lint` · `make ci` (everything).
- Reference documents: AlgoDial PoC Benchmark (the decisions behind every feature) and the Asterisk Lab Guide.
