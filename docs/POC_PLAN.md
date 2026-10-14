# AlgoDial PoC plan

The PoC is a small contact centre that works properly end to end: users and roles,
browser phones, agents with states, queues with skills, transfers, supervisor
monitor/whisper/barge, stereo recording, and an AI agent that hands calls to humans.
Every feature decision is explained in the "AlgoDial PoC Benchmark" document.

## Step 0: study the boilerplate (no code yet)

Read `~/scc` and report back:

1. Stack and versions (framework, Python version, database, task runner, front end).
2. Folder layout and where settings, apps, tests, Docker files and env files live.
3. Auth, users and permissions as already built.
4. What to reuse as-is, what to adapt, and what is specific to that project and must not be copied.
5. A proposed folder layout for AlgoDial (web, brain, shared models, docs, deploy).

Wait for approval before creating the AlgoDial repo.

## Data model

| Model | Key fields |
| --- | --- |
| User | login, name, role, team, active |
| Role | name, permissions (named strings such as `call.monitor`) |
| Team | name, supervisors, agents |
| SipAccount | id (= PJSIP endpoint/auth/aor id), kind (browser or softphone), username, password, codecs |
| Agent | user, skills (skill + level 1 to 10), queues (queue + priority); an AI agent has no user and no account |
| AgentSession | agent, sip_account, login_at, logout_at |
| AgentStateEvent | agent, availability (Ready / Not Ready + reason / Logged out), activity (Idle, Offered, Talking, On hold, Wrap-up, Not responding), at |
| Skill, SkillRuleSet, SkillRule | rule: wait-time condition + skill condition, in order |
| Queue | strategy, ring_timeout, retry_delay, max_wait, max_callers, service_level (seconds, percent), wrapup_mode, wrapup_seconds, recording, moh, overflow destinations, priority, skill_rule_set |
| NotReadyReason, WrapupCode | name, settings |
| EntryPoint | number, destination (queue or AI agent), schedule, after-hours destination, language, default skills, recording |
| Call | linkedid, entry point, started, ended, result |
| Leg | uniqueid, call, role (customer, agent, ai, supervisor, transfer target), SIP Call-ID, timings |
| Interaction | call, parts (AI, queue wait, agent, transfer), wrap-up code, recording path, transcript, AI summary |
| Event | at, kind, call, leg, agent, data (JSON). Append-only. Reports are built from this |

## Milestones

Each milestone ends with tests passing and a short demo. Write the tests first.

### M0: Skeleton
Repo from the approved layout; PostgreSQL and Redis (check ports first); settings from env; test runner; `make` (or equivalent) targets for run, test, lint.
**Done when:** the test suite runs green in CI-style locally and both services start with one command.

### M1: Users, roles, teams, SIP accounts
Admin pages for users, roles, teams. SIP accounts are written as **PJSIP realtime** rows in PostgreSQL (use the Alembic scripts in `~/asterisk-22/contrib/ast-db-manage` for the tables; configure `extconfig.conf` and `sorcery.conf`; check which realtime driver is loaded with `module show like res_config`).
**Done when:** creating an account in the admin page lets Zoiper register with it, with no Asterisk reload; deleting it stops registration.

### M2: Brain v1
Turn `lab/brain.py` into the brain service: per-call event queues, reconcile on (re)connect, unknown args hang up, `originator` on every originate, total cleanup, every event written to the Event table and published to Redis. Add the `STASISSTATUS` fallback to extension 800.
**Done when:** unit tests run the brain against a fake ARI (recorded event sequences); on the real lab, the 9 call tests below pass and `core show channels` / `bridge show all` are empty after each.

### M3: Agent web page with browser phone
A web page (served by Django, plain JS, signed in with the starter's OAuth token). No desktop
app: everything runs in the browser, like Wazo, VICIdial, Genesys and Cisco Finesse.
- Login: email + password (+ two-factor), then pick or get a SIP account for this session.
- Browser phone: SIP over WSS from the page (endpoint with webrtc=yes, TLS on Asterisk's
  HTTP server). Mic permission needs HTTPS.
- Agent controls: Ready / Not Ready with reason, call offer popup, answer, hold, hang up,
  wrap-up with codes.
- Live updates: one WebSocket per page, checked with the same OAuth token.
Zoiper stays only for test users.
**Done when:** an agent using only a browser logs in, goes Ready, takes a call from 800 end
to end, wraps up, and every state change appears in the Event table.

### M4: Queues, strategies, skills
Brain queue with strategies longest_idle, ring_all, round_robin, linear, fewest_calls, random; ring timeout, retry, max wait, max callers, overflow; skill rule sets that widen with wait time; service level stats.
**Done when:** tests prove each strategy's choice on scripted agent states, and a skill rule widens at the right second.

### M5: Transfers and conference
Blind (agent, queue, number, AI agent), consult with complete / cancel, 3-way conference, drop participant. A Transfer record with status.
**Done when:** each flow works from the agent page, and CEL shows one linkedid for the whole call.

### M6: Supervisor
Dashboard of live calls, agents and queues for the supervisor's teams. Monitor (`snoop spy=both whisper=none`), whisper (`whisper=out` on the agent channel), barge (join the bridge), force agent state.
**Done when:** a supervisor in the browser can switch monitor, whisper and barge on one call, and only the right people hear them.

### M7: Stereo recording
`MixMonitor(${UNIQUEID}.raw,D)` before `Stasis()`; customer on the left, everything they hear on the right; convert to WAV after hangup; playback page with permission `recording.listen`.
**Done when:** a recording plays with the customer only on the left channel.

### M8: AI agent
Speech-to-text and text-to-speech providers chosen by a bake-off on 20 real Swahili and English phrases. ARI externalMedia over WebSocket; pipeline STT, LLM, TTS; handoff rules (caller asks for a person, LLM `handoff` tool, two failed turns, sensitive topic) that set the call's skills and summary and send it to a queue.
**Done when:** the demo call works: AI answers 800, handles one question, hands off on request, the agent sees the summary, and the interaction shows both parts.

### M9: Reports
From the Event table: calls per queue, service level, abandon rate, average handle time, agent time in state, AI containment and handoff reasons.

## Call tests for the brain (M2 and later)

| # | Action | Expect |
| --- | --- | --- |
| 1 | Normal call, customer hangs up | All legs gone, bridge deleted |
| 2 | Normal call, agent hangs up | Same |
| 3 | Customer hangs up while agent rings | Agent phone stops ringing at once |
| 4 | Agent rejects | Next agent or overflow, per queue settings |
| 5 | Agent does not answer | Agent becomes Not responding; call goes to the next agent |
| 6 | Agent's phone offline | Clean failure, next agent |
| 7 | Call with unknown args | Logged and hung up |
| 8 | Brain killed mid-call, restarted | Reconcile finds the call and either adopts or cleans it |
| 9 | Brain stopped, customer calls 800 | Fallback `Queue()` answers |
