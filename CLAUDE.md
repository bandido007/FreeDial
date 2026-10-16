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
| Restarting Asterisk | Always `sudo deploy/asterisk/restart.sh`, never `systemctl restart asterisk`: the LSB init script's stop returns before Asterisk exits, so start finds it "already running" and nothing is left running (happened twice on 16 Oct 2026: every phone "not connected"). The script waits for the old process, starts, and checks 5062 and 8091 |
| Start order | Start Docker (`make up`) before Asterisk is restarted, or Asterisk logs `PostgreSQL RealTime: Failed to connect` once; it reconnects by itself on the next lookup |
| CEL | `/var/log/asterisk/cel-custom/cel_trace.csv` (advanced `cel_custom` mapping; `apps=` must list apps by name, `all` does not work in 22.11) |
| Test phones | Account `1001` = Zoiper on the laptop (127.0.0.1:5062), `1002` = Zoiper on a phone on the LAN. Both are SIP accounts in AlgoDial's database (`make agent-login`, passwords `LAB_PHONE_100x_PASSWORD` in `.env`), out of `pjsip.conf` after `sudo deploy/asterisk/phones-in-database.sh`. They call **800** = queue Support (no AI) |
| PJSIP realtime | `res_config_pgsql` → AlgoDial PostgreSQL (127.0.0.1:5442), role `asterisk` (read-only on `ps_endpoints`, `ps_auths`, `ps_aors`). Sorcery reads `pjsip.conf` first (only the transports once `phones-in-database.sh` has run), then the database. Tables by Asterisk's Alembic (`make pjsip-schema`). Applied by `deploy/asterisk/m1-pjsip-realtime.sh`; commits in `/etc/asterisk` are made as root |
| SIP check | `lab/sipcheck.py <id> <password>` registers like a phone: `200 OK` = works, `401` = unknown account or wrong password |
| Call tests | `make calltests` (stop your brain first): tests 1-20, all passing on 2026-10-15: 1-9 basic calls, 10 ring-all, 11 voicemail, 12-17 transfers (each checked in CEL for one linkedid) and 18 supervision (each phone sends its own tone; who hears whom is measured); 19 stereo recording (needs `apply.sh m7`; left = customer, right = what they hear, measured by tone); 20 the AI agent on lab entry **897** (real OpenAI: the customer speaks recorded sentences from `lab/voices/`, the test starts `run_ai` itself). Dialled on lab entry **899** (needs `apply.sh m4`), on the real Asterisk with scripted phones (`lab/sipphone.py`) as `lab-customer`, `lab-agent-a`, `lab-agent-b` (agents 9001, 9002, queue Support, entry 899). A full run records every decision afresh to `brain/sim/recordings/lab.jsonl`, which `brain/tests/test_replay.py` replays (re-run all 20 after changing an event); `make calltests t="18"` records to `lab/calltests-partial.jsonl` instead |
| Agent page | `http://127.0.0.1:8000/agent/` with `make dev` (web with WebSockets, brain, writer, live relay). Browser phone = JsSIP over `ws://127.0.0.1:8091/ws` (Asterisk's HTTP server, so this machine only); each sign-in gets its own account `web<agent number>` with a fresh password, switched off at sign-out. Browser accounts are `ulaw,alaw`: this Asterisk has no `codec_opus`. `make agentcheck` drives it in headless Chrome (M3), then two agents in two browsers do every transfer by clicking (M5), then a supervisor in Chrome switches monitor / whisper / barge while the customer's phone measures who hears them (M6); it plays the M3 call's stereo recording from the admin (M7) and has the lab AI hand a caller to the browser agent with its summary (M8). Then today's report must count the run (M9). 43 checks, all passing on 2026-10-15. Supervisor page: `http://127.0.0.1:8000/supervisor/` (`LAB_SUPERVISOR_*` in `.env`, team Lab; demo supervisors asha@ and daudi@) `make agent-login` sets up three logins in queue Support, passwords in `.env`: agents 7001 (`LAB_AGENT_*`) and 8003 (`agent8003@example.org`, `LAB_AGENT_8003_*`) for people, 9003 (`LAB_CHECK_AGENT_*`) used by `make agentcheck`; entry 800 → Support; phones 1001/1002 as SIP accounts; plus the wrap-up codes and Not Ready reasons |
| Desk phones | Admin → Agents → **Phone** (optional, SOFTPHONE SIP accounts only; empty = browser phone `web<number>`). At sign-in the brain is told `PJSIP/<phone>`; the agent page asks no microphone, shows "Pick up phone 1001", and re-checks the phone through ARI `GET /endpoints/PJSIP/<id>` (`myPhoneState`) every 10 s until online, then every 30 s. Asterisk reports a database phone it has not looked up since starting as offline until its next qualify (up to 30 s; measured), hence the re-checks. One signed-in agent per phone. SIP ids `web…` are reserved for browser phones. `make deskphonecheck` proves it on the real lab (11 checks, passing on 2026-10-16) |
| Carriers (M4b) | Admin → Telephony → **Trunks** (`apps/telephony/trunks.py`, presets in `apps/telephony/carriers/*.json`) write `ps_endpoints` (context `from-carrier`, permit/deny), `ps_aors` (contact, qualify), `ps_endpoint_id_ips`, `ps_auths`, `ps_registrations` under `trunk-<name>` (SIP ids `trunk-…` reserved); registrations are re-read via ARI module reload. **DIDs** are entry points in E.164; `queues_carrier_numbers` holds every form a carrier may send, read by `[from-carrier]` (`REALTIME_FIELD`), so unknown numbers are hung up even with the brain down. **Outbound routes**: E.164 prefixes allowed (premium only if named), trunks in order, permission `can_dial_external` checked by the web. Pure rules in `contracts/carriers.py` (shared by web and brain); the brain dials `PJSIP/<number>@trunk-<name>`, counts channels in and out, and fails over only on carrier causes (`CARRIER_FAILURES`). **Measured** with SIPp (16 Oct 2026): 503 → 34, 502 → 27, no response or carrier down → 18 (after 32 s) fail over; 486 → 17, 404 → 1, 480 → 19, 603 → 21 do not. Asterisk qualifies (and reads registrations of) a trunk added while it runs only after `res_pjsip` reloads: a trunk save reloads it over ARI (no calls or phone registrations are dropped). Health: ARI endpoint state (reachable) + AMI `PJSIPShowRegistrationsOutbound` (`AMI_*` in `.env`), shown in admin → Trunks and the Setup guide. `make carriercheck`: SIPp plays two carriers on **127.0.0.2/127.0.0.3:5090** (5070 on 127.0.0.2 belongs to another program), 19 checks passing on 2026-10-16. Number plan: System settings → Phone numbers. Asterisk side: `sudo deploy/asterisk/m4b-carriers.sh` (sorcery/extconfig realtime, `[from-carrier]`, AMI on 127.0.0.1:5038, user `AMI_USER` with write=reporting) |
| Demo centre | `make seed-demo`: the helpline of `docs/ROUTING_DESIGN.md` section 3 (8 agents with users `<name>@example.org`, password `DEMO_PASSWORD` in `.env`; skills, rule sets, 5 queues; entry 801 Providers, 802 General/English, 803 General/Swahili; AI agent **Jambo AI** answers 802 and 803 first. 800 is the lab's Support line, not the demo's). The brain's routing tests T1-T12 use the same data (`brain/sim/demo.py`) |
| Voicemail | Overflow action "voicemail": the brain plays `sound:vm-intro` and records the caller over ARI (`callback-<call>.wav` in Asterisk's stored recordings), then a callback task appears in admin → Calls → Callback tasks with a player |
| Recording (M7) | `apply.sh m7`: `MixMonitor(${ASTSPOOLDIR}/recording/calls/${UNIQUEID}.raw,D)` before `Stasis()` on every 8xx call (8 kHz stereo: left = customer, right = everything they hear). After the call `run_recordings` (in `make dev`) fetches it over ARI as stored recording `calls/<linkedid>`, keeps a WAV in private storage (`media/recordings/`), deletes Asterisk's copy. Played in admin → Calls → a call (permission `can_listen_recordings`) |
| AI agent (M8) | `run_ai` (in `make dev`, needs `OPENAI_API_KEY` in `.env`): one session per call. The brain opens the AI's leg with ARI externalMedia (WebSocket, `connection_type=server`, slin24, JSON control); the worker connects to `ws://127.0.0.1:8091/media/<connection id>` (Asterisk's HTTP server, no new port). Models chosen by `make ai-bakeoff` (`docs/AI_BAKEOFF.md`): `gpt-4o-transcribe`, `gpt-4o`, `gpt-4o-mini-tts` (falls back to `tts-1` when slow to start). Settings in admin → AI agents. Handoff (`ai.handoff`) puts the call in its queue with the summary on the agent's offer; the call page shows the interaction parts (AI with transcript, then the agent) |
| Reports (M9) | Admin → Reports (`can_view_reports`, Contact Centre Manager role), API `getReport` / `exportReport`: calls per queue (service level, abandon rate, speed of answer, handle time), agent time in state, AI containment and handoff reasons, and the UX_PRINCIPLES section 6 measurements (`apps/reports/experience.py`), for a period, as PDF / Excel / CSV. Computed from the Event table by `apps/reports/compute.py`; definitions in `docs/REPORTS.md`. The brain emits `call.queued` each time a call starts waiting in a queue |
| UI | Spec `docs/UI_DESIGN.md`, approved mockups `docs/mockups/` (15 Oct 2026), rollout and review `docs/UI_ROLLOUT.md`. Shared tokens and font in `static/algodial/`. `make format-web` formats CSS/JS with Prettier; never run it on Django templates (it splits `{% %}` tags across lines). Browser tests (`apps/*/tests/test_browser.py`, `apps/core/tests/test_admin_browser.py`) save screenshots in `test-artifacts/`; `scripts/ui_gallery.py` builds `test-artifacts/index.html`. `lab/agentpage.py --use-running-stack` checks against a running `make dev` without stopping it |
| Setup guide | Admin → Setup guide (`apps/setup`, permission `can_view_setup_guide`; `can_confirm_setup_tests` for the hands-on tests): a checklist read live from the configuration, Redis (are brain, writer, AI worker running) and the call records of the last 30 days, then the user manual for managers (`apps/setup/templates/setup/guide.html`). Change the manual there when an admin page changes |
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
- **UX signals** (`ux.*`, UX_PRINCIPLES.md section 6) go from the web straight onto `algodial:events`:
  measurement only, never call or agent state. The web stamps the signed-in agent; the page's own
  UUID is the `event_id`, so a retry is stored once. The brain emits its own signals
  (`offer.*`, `wrapup.*`, `transfer.*`).
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
- A feature is done only when it meets the UX definition of done (`docs/UX_PRINCIPLES.md` section 8).
  How pages look: `docs/UI_DESIGN.md`; how they behave (speed, errors, AI, measurement): `docs/UX_PRINCIPLES.md`.
- For anything touching `/etc/asterisk`: show the diff, explain it, apply it, reload the right module, and commit in `/etc/asterisk`.
- After each milestone: run all tests, give a short summary, and list the commands Tostao should run to see it working.
- A new column gets a database default (`db_default`), so a process still running older code keeps working while `make dev` is restarted.
- Commands: `make up` (services) · `make run` (web) · `make brain` · `make test` · `make lint` · `make ci` (everything).
- Reference documents: AlgoDial PoC Benchmark (the decisions behind every feature) and the Asterisk Lab Guide.
