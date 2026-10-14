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
| Dialplan entry | Extension `800` in context `[lab]`: `Stasis(algodial,inbound,${EXTEN},${CHANNEL(endpoint)})` |
| CEL | `/var/log/asterisk/cel-custom/cel_trace.csv` (advanced `cel_custom` mapping; `apps=` must list apps by name, `all` does not work in 22.11) |
| Test phones | Account `1001` = Zoiper on the laptop (127.0.0.1:5062), `1002` = Zoiper on a phone on the LAN |
| Existing prototype | `~/AlgoDial/brain.py` (aiohttp ARI client), `.venv`, `ari.env` (not in git) |
| Network | Laptop IP changes between networks. Anything on 127.0.0.1 is unaffected |

Before using any new port, check it is free: `sudo ss -ltnp | grep ':PORT'`.

## Target stack

- **web**: Django (admin, REST API, auth, agent and supervisor pages) plus a WebSocket layer for live events.
- **brain**: a separate asyncio Python service. It holds the ARI WebSocket and REST client and owns all call and agent-state decisions.
- **Redis**: live state (agent states, calls in progress) and pub/sub from brain to web.
- **PostgreSQL**: configuration, PJSIP realtime tables, call records, event log.
- Later: **media/AI worker** for the AI agent (ARI externalMedia over WebSocket, STT, LLM, TTS).

If the boilerplate in `~/scc` uses a different stack, say exactly where it differs and recommend before changing anything.

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
10. **Config lives in the database**, is edited in admin pages, and the brain picks it up without a restart.

## How to work

- One milestone at a time from `docs/POC_PLAN.md`. Write the tests for its "Done when" first, then the code.
- For anything touching `/etc/asterisk`: show the diff, explain it, apply it, reload the right module, and commit in `/etc/asterisk`.
- After each milestone: run all tests, give a short summary, and list the commands Tostao should run to see it working.
- Reference documents: AlgoDial PoC Benchmark (the decisions behind every feature) and the Asterisk Lab Guide.
