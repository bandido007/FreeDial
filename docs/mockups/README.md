# Static UI approval review

Status: approved on 15 October 2026. These static references remain separate from the M3 production implementation in `apps/agentdesk/`.

Implements [UI_DESIGN.md section 8.1](../UI_DESIGN.md#8-how-to-build-and-check-it) using the demo contact centre in [ROUTING_DESIGN.md](../ROUTING_DESIGN.md#3-demo-contact-centre-seed-data).

## Review

[Light overview](screenshots/overview-light.jpg) and [dark overview](screenshots/overview-dark.jpg) show all eight pages at both sizes. Open the original PNGs for readable text and controls:

| Page | 1366×768 light | 1366×768 dark | 1920×1080 light | 1920×1080 dark |
| --- | --- | --- | --- | --- |
| Not ready | [PNG](screenshots/not-ready-light-1366x768.png) | [PNG](screenshots/not-ready-dark-1366x768.png) | [PNG](screenshots/not-ready-light-1920x1080.png) | [PNG](screenshots/not-ready-dark-1920x1080.png) |
| Ready | [PNG](screenshots/ready-light-1366x768.png) | [PNG](screenshots/ready-dark-1366x768.png) | [PNG](screenshots/ready-light-1920x1080.png) | [PNG](screenshots/ready-dark-1920x1080.png) |
| Incoming offer | [PNG](screenshots/incoming-light-1366x768.png) | [PNG](screenshots/incoming-dark-1366x768.png) | [PNG](screenshots/incoming-light-1920x1080.png) | [PNG](screenshots/incoming-dark-1920x1080.png) |
| On a call | [PNG](screenshots/talking-light-1366x768.png) | [PNG](screenshots/talking-dark-1366x768.png) | [PNG](screenshots/talking-light-1920x1080.png) | [PNG](screenshots/talking-dark-1920x1080.png) |
| Transfer started | [PNG](screenshots/transfer-light-1366x768.png) | [PNG](screenshots/transfer-dark-1366x768.png) | [PNG](screenshots/transfer-light-1920x1080.png) | [PNG](screenshots/transfer-dark-1920x1080.png) |
| Wrap-up | [PNG](screenshots/wrap-up-light-1366x768.png) | [PNG](screenshots/wrap-up-dark-1366x768.png) | [PNG](screenshots/wrap-up-light-1920x1080.png) | [PNG](screenshots/wrap-up-dark-1920x1080.png) |
| Phone problem | [PNG](screenshots/phone-problem-light-1366x768.png) | [PNG](screenshots/phone-problem-dark-1366x768.png) | [PNG](screenshots/phone-problem-light-1920x1080.png) | [PNG](screenshots/phone-problem-dark-1920x1080.png) |
| Supervisor | [PNG](screenshots/supervisor-light-1366x768.png) | [PNG](screenshots/supervisor-dark-1366x768.png) | [PNG](screenshots/supervisor-light-1920x1080.png) | [PNG](screenshots/supervisor-dark-1920x1080.png) |

Additional screenshots cover a missed offer, blocked microphone and supervisor monitoring in both sizes and themes.

For the browser gallery, run from the repository root:

```bash
python3 -m http.server 8765 --bind 127.0.0.1 --directory docs/mockups
```

Then visit <http://127.0.0.1:8765/>. Static pages use `?state=talking&theme=dark`, for example. The gallery links every required view. Serve over HTTP because the small ES module needs an HTTP origin.

## Scope and data

- Juma is on Front Desk and serves Membership, Claims and General. Neema is the consult transfer target, speaks Swahili and handles Claims.
- Daudi supervises Claims & Providers: Halima, Peter, Zawadi and Mussa. Per-queue agent counts reflect these agents' actual queue memberships and can overlap.
- Queue service targets and ring timeouts come from the routing design. Escalations waits 110 seconds, below its 120-second overflow limit.
- Activity, caller number, notes, wrap-up codes, break limits and long-call thresholds are illustrative. The routing spec does not define a Claims wrap-up time limit; elapsed wrap-up time is shown instead of a countdown.
- Static controls demonstrate placement. Only theme switching, navigation, inputs and the queue-panel disclosure work. No calls, permissions, notifications, audio, APIs or sockets are wired.
- English mockups are ready for review. Swahili is visibly unavailable pending Tostao's label review.
- Atkinson Hyperlegible Next is self-hosted with its OFL license. No runtime CDN or external assets.

## Reproduce screenshots

With Playwright, Chrome/Chromium and Pillow available:

```bash
.venv/bin/python docs/mockups/capture.py
```

The script starts and stops its own local server, captures 32 viewport-sized PNGs plus 12 supplemental views, generates two overview sheets and writes `screenshots/manifest.json`. It checks font loading, JavaScript page errors and viewport overflow. These checks do not replace the later M3 axe/keyboard workflow checks in section 8.3.
