# M3 agent workspace

The static references in [mockups/](mockups/README.md) were approved on 15 October 2026. The Django page at `/agent/` now uses that layout with the existing OAuth login, browser SIP phone and agent WebSocket.

## Behavior

- The state bar, call, context, queues and personal statistics stay on one screen. Light/dark mode follows the system until the agent switches it. Atkinson Hyperlegible Next is self-hosted.
- Ready requires a registered phone, microphone access and the live connection. Not Ready requires a reason. A lost phone pauses availability with the reason “Phone not connected”; reconnecting leaves the agent Not Ready until they choose Ready.
- Incoming offers show caller, queue, language, AI summary and the queue's ring timeout. Answer uses Enter, hold/resume H, mute M, transfer T, keypad K, hang up E, and the state menu R. Letter shortcuts are inactive in editable fields. Browser notifications are enabled by the agent from the state menu.
- Existing blind/consult transfer and conference controls are retained. Mute and keypad act on the browser's SIP session; call state and transfers remain brain-owned.
- Draft notes belong to one agent and call, survive page reloads in that tab, and travel with `finishMyWrapup`. The brain records them in `agent.wrapped_up`, which the writer archives in the Event table. A timed-out draft is available from “Unsaved notes from last call”; it is not described as saved to the server.
- Wrap-up shows a countdown when the actual queue has a time limit. Codes can be searched and must be chosen when active codes are configured. Finishing chooses Ready or Not Ready in the same brain command, before a waiting call can be offered.
- The queue panel shows only the agent's memberships; “My day” reads only that agent's call legs and state events, including a Not Ready interval crossing midnight. Live call changes send an invalidation through the existing WebSocket; the subsequent authenticated query returns counts and the agent's own call, without other callers' details.
- English labels come from `apps/agentdesk/translations/en.json`. Swahili remains unavailable pending Tostao's label review.

At the M3 checkpoint the supervisor retained its previous design. The subsequent [all-role UI rollout](UI_ROLLOUT.md) applies the approved design to the supervisor and management pages. M3 itself requires no database migration; the later rollout adds configurable Not Ready limits.

## Run and verify

Restart `make dev` to load the updated brain and live relay, then open <http://127.0.0.1:8000/agent/>. Browser microphone access on another host requires HTTPS/WSS as in the existing deployment setup.

```bash
# On a fresh machine without Chrome/Chromium, install the test browser once
.venv/bin/playwright install --with-deps chromium

# Full suite, including the production page's browser checks
make test

# Browser layouts, axe and keyboard/recovery flows
.venv/bin/python -m pytest apps/agentdesk/tests/test_browser.py --no-cov

# M3 browser call through an already-running local lab, without stopping its services
.venv/bin/python lab/agentpage.py --no-transfers --no-supervision --no-ai --use-running-stack
```

The browser tests save all seven agent states at 1366×768 and 1920×1080 in both themes under `test-artifacts/agentdesk/`. They check viewport overflow, font loading, script errors, WCAG axe violations, keyboard call controls, wrap-up validation and microphone recovery. CI installs Chromium and retains these screenshots as artifacts. axe-core 4.10.3 is pinned under the test directory and is not loaded by the product.

The live M3 lab run on 15 October 2026 passed 22 checks: OAuth, SIP registration, the live socket, Ready, offer, answer, two-way audio, hold/resume, wrap-up, Not Ready, sign-out, persisted state transitions and wrap-up code, plus the existing recording/report smoke checks. It used test entry 899, leaving demo entry 800's AI configuration intact. That run used the already-running brain; the new atomic wrap-up and notes payload are additionally covered by the core/API tests and require the normal process restart to become active in that stack.

Validation on 15 October 2026: the full regression run passed 719 tests with one existing skip (the separate frontend checkout is absent) and 86.89% coverage, above the 85% threshold. The final focused agent/API/WebSocket/core/replay run passed 49 tests. Subsequent browser checks also cover isolation from a delayed snapshot belonging to a previous sign-in. Django, schema/migration, Ruff, architecture and mypy checks passed.
