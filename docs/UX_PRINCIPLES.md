# AlgoDial UX principles

`UI_DESIGN.md` says how pages look. This document says how they must **behave**: speed,
errors, AI, measurement and testing. A feature is not done until it meets both.

## 1. Where the principles come from

| Source | Principle | AlgoDial rule |
| --- | --- | --- |
| GOV.UK design principles | Start with user needs; understand context; do the hard work to make it simple; iterate; this is for everyone | Watch real agents work before designing a screen; build for a noisy floor, old laptops, slow networks and two languages |
| Google HEART (Rodden, Hutchinson, Fu, CHI 2010) | Measure Happiness, Engagement, Adoption, Retention, Task success; go from Goals to Signals to Metrics | Every page has goals, signals in the Event table and metrics on a dashboard (section 6) |
| Google RAIL and NN/g response-time limits | Respond within 100 ms; under 1 s keeps flow; over 1 s show progress; over 10 s users give up | Speed budgets for every action (section 3) |
| NN/g 10 heuristics | Visible status, real-world words, user control, consistency, error prevention, recognition over recall, shortcuts, minimal design, clear errors, help | Applied throughout; see the checklist (section 8) |
| NN/g on confirmation dialogs | Prefer undo; too many confirmations train people to click through | Confirm only what cannot be undone (section 4) |
| Google People + AI Guidebook | Help users calibrate trust; say when data is missing; partial explanations; avoid misleading confidence numbers | Rules for the AI summary and handoff (section 5) |
| Microsoft Inclusive Design | Recognise exclusion; learn from diversity; solve for one, extend to many | Keyboard-only, low-vision, noisy-room and low-bandwidth use are tested, not assumed |
| NN/g on testing with 5 users | 5 users find about 85% of problems; run several small rounds | 3 rounds of 5, split by user group (section 7) |

## 2. The eight AlgoDial UX rules

1. **Nothing happens silently.** Every action shows a result within 100 ms: done, in progress, or failed with a fix.
2. **The system remembers, the person does not.** Caller details, AI summary, previous state and reason codes are shown, never typed again.
3. **Make the wrong action hard and the right action easy.** Dangerous actions are separated, need a modifier key, or can be undone.
4. **One way to do each thing.** The same action has the same name, place and shortcut on every page.
5. **The AI is a colleague who can be wrong.** Its output is labelled, explained and correctable.
6. **Failure is a designed state, not an accident.** Every failure from section 4 has a screen.
7. **Measure, then change.** No UX change without a metric it should move.
8. **Test with the people who will use it.** Agents and supervisors from a real contact-centre floor, not developers.

## 3. Speed budgets

An agent's click travels browser, API, Redis, brain, Asterisk and back as an event. The page must not wait for that trip to show something.

| Moment | Budget | What the page does |
| --- | --- | --- |
| Agent presses any button | 100 ms | Button shows a pending state at once (optimistic), for example Hold shows "Holding..." |
| Brain confirms the action (event arrives) | 1 s | Pending state becomes the real state. Past 1 s, the pending text stays and a small progress cue appears |
| Action not confirmed | 10 s | Undo the optimistic change and show what failed and what to do. Never leave a button stuck |
| Brain offers a call to the agent | Offer panel visible within 500 ms of the brain's decision | Measured: `offer_shown_at` minus `offer_sent_at` |
| First page load after sign-in | Under 3 s on a slow 3G-class connection | Small pages, self-hosted assets, no framework |
| Supervisor grid update after an agent state change | 1 s | WebSocket push, not polling |

## 4. Error prevention, undo and failure states

**Dangerous actions:**

| Action | Protection |
| --- | --- |
| Hang up | Button set apart from Hold and Mute; shortcut is **Shift+E** (two keys), never a single key |
| Complete a transfer | Shows who the caller is going to before completing |
| Go Not Ready by mistake | One click back to Ready; no penalty, both events logged |
| Wrap-up submitted with the wrong code | Editable for 2 minutes after submit |
| Admin deletes a queue, skill or account | Confirmation that says the impact ("5 agents lose this queue"); soft delete with restore |
| Admin edits a rule set | Saved as a new version; previous version restorable |

**Failure states that must have a designed screen:**

| Failure | What the user sees | What the system does |
| --- | --- | --- |
| Browser loses the network mid-call | "Connection lost. Your call continues if your phone is still connected. Reconnecting..." | The SIP media may survive; the page reconnects the WebSocket and reloads state from the brain |
| Browser phone unregisters | State bar in Offline colour with the fix | Brain stops offering calls to this agent |
| Brain restarts | Agents see "Reconnecting to the call server" for a few seconds | Brain reconciles with Asterisk (rule 1 in `CLAUDE.md`) and republishes states |
| Brain is down | Agents see "Calls are going to the backup queue" | Dialplan fallback to `Queue()` |
| Transfer target does not answer | "Peter did not answer. You are back with the caller." | Consult cancelled automatically, agent rejoined |
| AI cannot understand the caller | Agent gets the call with "AI could not understand the request" and the transcript so far | Handoff rule fires; nothing is hidden |

## 5. AI in the interface

- **Label it.** The summary says "AI summary", with the time it covers.
- **Show what it is based on.** A link to the transcript of the AI part of the call.
- **Say when it is unsure.** No percentages. Use words: "The caller's member number was not captured" or "Topic unclear: chose General".
- **Let the agent correct it.** Buttons "Summary wrong" and "Wrong queue" record a correction event. These become training and evaluation data for the AI.
- **Never act silently.** If the AI changes the call's language, topic or queue, that change is shown in the call's history with "by AI".

## 6. Measuring it (HEART: goals, signals, metrics)

Engagement and retention matter less for a tool people use at work every day, so AlgoDial focuses on task success and happiness, as the HEART paper allows (pick the dimensions that fit the product).

| User | Goal | Signal (from the Event table unless noted) | Metric |
| --- | --- | --- | --- |
| Agent | Answer offers quickly and never miss one | `offer_shown`, `offer_answered`, `offer_missed` | Median seconds to answer; missed offers per 100 |
| Agent | Finish calls without fighting the tool | `wrapup_started`, `wrapup_done`, `wrapup_code_changed` | Median wrap-up seconds; corrections per 100 calls |
| Agent | Transfers work first time | `transfer_started`, `transfer_completed`, `transfer_cancelled`, `transfer_failed` | Success rate |
| Agent | The tool feels easy | 2-question survey every 2 weeks (ease 1 to 5, one annoyance) | Average ease; top 3 annoyances |
| Agent | Callers do not repeat themselves | `ai_summary_shown`, `ai_summary_wrong` flags | Summary-wrong rate per 100 AI handoffs |
| Supervisor | Problems are noticed and acted on | Time an agent is over a not-ready limit before a supervisor action | Median minutes to action |
| Admin | Setup is quick and safe | Time from "create agent" to the agent's first call; validation errors shown | Minutes; errors caught before save |
| All | Speed budgets hold | Timestamps on each step in section 3 | 95th percentile per moment |

## 7. Research and testing plan

1. **Before designing (understand context):** observe agents and a supervisor on a real contact-centre floor for at least 2 hours each; write down every tool they switch to, every time they ask a colleague, and every time a caller repeats themselves. These become the problems the design must remove.
2. **Static mockups:** 5-minute "where would you click to..." tests with 3 agents.
3. **Working PoC, 3 rounds:** 5 participants per round (3 agents + 2 supervisors), fix, repeat. Same task script each round so improvements show:
    - Go Ready, take a call, put it on hold, resume, transfer to Claims, wrap up with a code.
    - Recover from "phone not connected".
    - Supervisor: find the agent who has been on break too long and act on it; monitor a call, then whisper.
4. Record task time, errors and comments for each task. Fix the worst problems first.

## 8. UX definition of done (per feature)

- [ ] Every action gives feedback within 100 ms and confirmation or failure within 10 s
- [ ] Every failure path has a designed message with a fix
- [ ] Dangerous actions follow section 4
- [ ] Works with keyboard only; shortcuts listed on buttons
- [ ] Works at 1366x768 and on a throttled slow network in Playwright
- [ ] All text in the translation files; Swahili reviewed
- [ ] Events needed for its section 6 metrics are emitted
- [ ] Tried by at least one real agent or supervisor before it is called finished

## Sources

- [GOV.UK government design principles](https://www.gov.uk/guidance/government-design-principles)
- [Rodden, Hutchinson, Fu: Measuring the user experience on a large scale (HEART), CHI 2010](https://static.googleusercontent.com/media/research.google.com/en//pubs/archive/36299.pdf)
- [Google web.dev: RAIL performance model](https://web.dev/articles/rail)
- [NN/g: Response times, the 3 important limits](https://www.nngroup.com/articles/response-times-3-important-limits/)
- [NN/g: 10 usability heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/)
- [NN/g: Confirmation dialogs](https://www.nngroup.com/articles/confirmation-dialog/)
- [NN/g: Why you only need to test with 5 users](https://www.nngroup.com/articles/why-you-only-need-to-test-with-5-users/)
- [Google People + AI Guidebook: explainability and trust](https://pair.withgoogle.com/chapter/explainability-trust/)
- [Microsoft Inclusive Design](https://inclusive.microsoft.design/)
