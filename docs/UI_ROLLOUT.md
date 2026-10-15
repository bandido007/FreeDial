# All-role UI rollout

The approved [UI design](UI_DESIGN.md) now covers the existing agent, supervisor, and management workspaces. This follows the approved [static mockups](mockups/README.md) and the [M3 agent implementation](M3_AGENT_UI.md).

## Pages and roles

| Workspace | Implemented surfaces |
| --- | --- |
| Agent | All seven page states, phone recovery, call controls, transfers, notes and wrap-up, as documented for M3 |
| Supervisor | Team filter, queue snapshot and targets, agent state timers, attention ordering, monitor/whisper/barge, state-change dialog including sign-out, scoped history, phone reconnection |
| Management | Sign-in, navigation, searchable record list beside existing forms, users, roles, accounts, agents, skills, teams, queues, rule sets, entry points, AI agents, system settings, confirmations and existing read-only tables |
| Reporting | Period and custom date controls, queue/agent/AI tables, existing PDF/Excel/CSV downloads |

The workspaces share the approved font, colour tokens, visible focus and persistent light/dark preference. Role permissions and supervisor team scope still govern every operation; the browser hides unavailable actions and the server enforces authorization. Agent and supervisor SIP commands still go through the API and brain. Existing Django forms retain their validation, CSRF, inlines, save operations and confirmation flows. The full table remains available from editable record lists for filters and bulk actions.

The rule-set editor previews the current, unsaved steps, checks the language floor, and tests agent skills at a chosen wait time, optionally including queue membership. Agent preview data requires the agent-view permission. The timeline remains available to queue editors without that permission.

System settings show all declared settings, including defaults with no saved override. Values use their declared type and constraints and save through the existing configuration service. Restore default removes an override. Opening a setting does not save anything.

English labels ship now. Swahili remains disabled pending the review required by the design.

## Attention settings

Run `python manage.py migrate` on other installations. Migration `agents.0005` adds `NotReadyReason.max_seconds` (default 0, no limit). Management → Agents → Not ready reasons configures each limit. Management → System settings → Long call threshold defaults to 600 seconds; 0 disables it. Changes are read when the supervisor desk next loads or synchronizes. Timers use recorded state-transition times; missing timestamps display a dash.

## Screenshots and checks

The production browser tests capture 1366×768 and 1920×1080 in light and dark, with the demo centre from [ROUTING_DESIGN.md](ROUTING_DESIGN.md). Long management forms also have full-page captures. These use rendered production templates and scripts with deterministic browser transports; real telephony is checked separately against the lab.

```bash
.venv/bin/python -m pytest apps/agentdesk/tests/test_browser.py apps/supervisor/tests/test_browser.py apps/core/tests/test_admin_browser.py --no-cov
.venv/bin/python scripts/ui_gallery.py
# Open test-artifacts/index.html locally

make lint BIN=.venv/bin
make typecheck BIN=.venv/bin
make check BIN=.venv/bin PY=.venv/bin/python
.venv/bin/python -m pytest
.venv/bin/python lab/agentpage.py --no-transfers --no-ai --use-running-stack
```

Screenshots are generated in ignored `test-artifacts/` and retained by CI. The [local gallery](../test-artifacts/index.html) filters by workspace, theme and viewport. Automated browser checks cover overflow, script errors, font loading, WCAG axe scans, role actions, team filtering, history and state dialogs, and rule-step boundaries. These checks supplement the visual review; they do not establish complete WCAG conformance.

Validation on 15 October 2026: the real lab passed all 28 checks, including two-way agent audio, hold/resume/wrap-up, supervisor monitor/whisper/barge audio isolation, recordings and reports. Django checks, migration/schema checks, Ruff, architecture contracts and mypy passed.

## Review (15 October 2026)

Reviewed against UI_DESIGN.md and the approved mockups, then fixed:

- Code was written as very long lines (up to 8,873 characters in `admin.css`). CSS and JS are now
  formatted by Prettier (`make format-web`, 110 columns); the Django templates were rewritten by hand.
  Prettier is not used on templates: it splits `{% %}` tags across lines, which Django cannot parse.
- Agent page: the state bar's second line gives context (queue and language, "Waiting for the next
  call", when Not Ready began) instead of repeating the state; the phone shows "Phone connected"
  (the account stays in a tooltip and `data-sip-id`); a consult names the colleague ("Consulting
  Neema; caller on hold"); the wrap-up search box appears only for more than 10 codes (a select
  already jumps as you type).
- Supervisor page: the queue strip counts "Ringing" (AlgoDial's state) instead of Finesse's "Reserved";
  plain phone status as on the agent page.
- Management: Django's forms.css loads after ours on change forms, so "SAVE" was still upper case,
  rule rows still showed tiny "< 30s: claims >= 7" labels and skill names were cut off; the rules are
  now anchored on `#content`.
- Wrap-up notes are kept on the call's interaction (agent part), shown on the call's admin page, not
  only in the event log. The new column has a database default, so a writer still running older code
  keeps working during a restart.

Validation: `make ci` (lint, types, 9 import contracts, 741 tests including the three browser suites);
the M3 lab check against a running stack passed 22 checks.

## UX principles applied (15 October 2026)

Following `docs/UX_PRINCIPLES.md` (and the updated UI_DESIGN.md: Hang up is Shift+E):

- **The state button says what it will do.** Not Ready: a filled "Go Ready" that acts at once (R).
  Ready: "Go Not Ready", which opens the reasons as one-click buttons. During a call: "Not Ready
  after this call" / "Ready after this call". This replaces the generic "Change state" menu, so an
  agent never has to guess how to change state.
- **Hang up is Shift+E**; a lone "e" does nothing (section 4).
- **AI feedback** (section 5): "Summary wrong" and "Wrong queue" beside the AI summary.
- **A wrap-up code can be corrected for 2 minutes** after it was given (section 4), from the call zone.
- **Section 6 signals**: the brain emits `offer.answered`, `offer.missed`, `wrapup.started`,
  `wrapup.done`, `wrapup.code_changed`; the page sends `ux.offer_shown`, `ux.ai_summary_shown`,
  `ux.ai_summary_wrong`, `ux.ai_queue_wrong`, `ux.action` (press-to-confirm time for each button);
  the admin sends `ux.admin_validation_error` (field names only).
- **Swahili** on the agent and supervisor pages, switched in place (no reload, a call goes on) and
  remembered. Draft labels for Tostao's review: `docs/SWAHILI_REVIEW.md`. Tests check that every
  English label has a Swahili one with the same `{placeholders}`, and that every state still fits at
  1366x768 and passes axe in Swahili.
