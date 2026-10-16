# AlgoDial UI design

The spec for every AlgoDial web page: the agent page (M3), the supervisor page (M6) and the
admin pages. Read it before writing any HTML, CSS or JS. If something here conflicts with
habit or a component library default, this document wins.

## 1. Who uses it and what that demands

Agents sit on 8-hour shifts, mostly on the phone, on office laptops (many at 1366x768),
often on slow networks, in Swahili and English. Supervisors watch 10 to 30 agents at once.
Admins change settings a few times a week.

| What the benchmark shows | What it means for AlgoDial |
| --- | --- |
| Cisco Finesse puts the agent state control at the top, an incoming-call pop-up with caller ID and queue, call controls (hold, direct transfer, consult transfer, conference, dial pad), and tabs for queue waiting, team status and "My Statistics". Not Ready needs a reason code that the team can see. | State always visible at the top; the offer shows caller and queue; the same call actions; reasons are required |
| Genesys Cloud's agent workspace: interactions list on the left, the conversation window (contact name, elapsed time, channel, controls) in the middle, side panels on the right for Script, Profile, Journey, Notes, Wrap-up codes. Panels resize, collapse and move. | Three zones: what is happening, the call, the context |
| Finesse supervisor: team performance per agent (state, time in state, reason code), queue snapshot (waiting, longest wait, agents ready/talking/not ready/wrap-up/reserved), actions per agent: view history, change state, monitor, then barge, drop | Supervisor page is a team grid plus a queue strip, with actions on each agent row |
| Harvard Business Review (2022, via CX Today): digital workers switch apps up to 1,200 times a day and spend nearly 4 hours a week reorienting | Everything an agent needs for a call is on one screen; no second app, no tab hunting |
| Nielsen Norman Group heuristics: visible system status, real-world language, recognition over recall, shortcuts for experts, minimal design, plain error messages | Applied rule by rule below |
| WCAG 2.2: text contrast at least 4.5:1, control borders at least 3:1, colour never the only signal, everything keyboard-operable, visible focus, targets at least 24x24 px | Hard requirements, tested |

## 2. Principles

1. **The agent's state is never in doubt.** One state bar across the top of the page shows state (word + icon + colour), time in state, phone health and queue load. It is the one bold element of the design. Everything else stays quiet.
2. **One screen per call.** Caller, why they called (AI summary), call controls, notes and wrap-up all live on the agent page. Nothing requires another tab.
3. **The caller never repeats themselves.** When the AI hands a call over, the offer and the call panel show the AI's summary, the language, the topic and what the caller already did.
4. **Colour is a second signal, never the first.** Every state has a word and an icon. Colour helps scanning only.
5. **Calm by default, loud only for a new call.** The only motion and sound that happen without the user acting is an incoming offer. No pulsing badges, no animated charts, no confetti.
6. **Keyboard first for agents.** Every call action has a shortcut, shown on its button.
7. **Plain words, Swahili and English.** Every label comes from a translation file from day one. The agent chooses the language.
8. **Respect, not surveillance.** The agent's own numbers are shown to help them ("calls today", "average talk time"), never as a ranking against colleagues.

## 3. Design tokens

Colours (all pairs checked: text 4.5:1 or more, control borders 3:1 or more).

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--paper` | #F4F6F5 | #141C1E | Page background |
| `--surface` | #FFFFFF | #1C2629 | Panels |
| `--ink` | #1C2B2D | #E6ECEA | Main text (13.5:1 / 14.5:1) |
| `--muted` | #566467 | #A3B0AE | Secondary text (6.2:1 / 6.9:1) |
| `--line` | #D5DCDA | #2C383B | Panel dividers only (decorative) |
| `--control-border` | #7A8688 | #6B7A7C | Input and button borders (3.75:1 / 3.46:1) |
| `--accent` | #2E6E73 | #7CC3C8 | Primary buttons, focus ring, links |

State colours (text on white or on the state colour both pass 4.5:1):

| State | Word (en / sw draft) | Icon idea | Light | Dark |
| --- | --- | --- | --- | --- |
| Ready | Ready / Tayari | check circle | #1E7047 | #5CC08A |
| Offered (ringing) | Incoming call / Simu inaingia | ringing phone | #B04A12 | #F08A4B |
| Talking | On a call / Kwenye simu | phone | #1F5A9E | #6FA8E8 |
| On hold | On hold / Imeshikiliwa | pause | #8A5A00 | #E0B04A |
| Wrap-up | Wrap-up / Kumalizia | clipboard | #6A4C9C | #B79BE6 |
| Not Ready | Not ready: reason / Sipo tayari: sababu | coffee cup / minus circle | #9A3B3B | #E58A8A |
| Offline / phone down | Phone not connected / Simu haijaunganishwa | plug off | #5E6668 | #9AA4A6 |

Swahili strings are drafts; Tostao reviews every Swahili label before release.

Type:
- One family: **Atkinson Hyperlegible Next** (designed for legibility; free on Google Fonts), self-hosted (no CDN at runtime). Fallback `system-ui, sans-serif`.
- Scale: 13 (meta), 15 (body), 18 (panel titles), 24 (caller name), 32 (state bar timer). Line height 1.45 for body.
- All timers and counts use `font-variant-numeric: tabular-nums` so they do not jump while counting.
- Sentence case everywhere. No ALL CAPS labels, no letter-spaced eyebrow labels.

Space and shape:
- 4 px grid: 4, 8, 12, 16, 24, 32.
- Radius: 6 px for controls, 10 px for panels. No shadows except the incoming-call panel.
- Minimum click target 32x32 px (above the WCAG 24 px minimum); call controls 44 px tall.
- Icons: one inline SVG set, 20 px, `currentColor`. No emoji as icons.

Light and dark both ship; default follows the system, and the agent can switch.

Logo ("Decision Dial", chosen from three concepts in `docs/AlgoDial Logo.html`, 15 Oct 2026):
- The symbol is a ring of rules with an opening at 45°. A needle runs from the centre hub to a bead in that opening: the one right agent, chosen inside the rules. It uses both meanings of "dial".
- Colour: the symbol is `--accent` (#2E6E73; dark #7CC3C8) and the word is `--ink`, in Atkinson Hyperlegible Next Bold. Only the state bar uses colour to carry meaning.
- In the pages, the symbol is inline SVG (`templates/algodial/logo_symbol.html`, classes `.ui-logo*` in `ui.css` and `agentdesk/app.css`), so it follows the theme switch. It is 28 px in headers and 40 px on sign-in.
- Spacing: the gap between symbol and word is x, the bead's diameter (28% of the symbol). Keep x of clear space on every side.
- Minimum size: never smaller than 24 px on screen or 8 mm in print. The lockup is never narrower than 110 px.
- Favicon: `static/algodial/favicon.svg`, a separate 16 px drawing. Its strokes are snapped to whole pixels, its opening is wider and it has no hub. It switches to the dark colour by itself.
- Files for slides and print are in `static/algodial/brand/`:
  - `symbol.svg` and `symbol-dark.svg`
  - `symbol-mono.svg`, which takes the text colour of wherever it is placed
  - `lockup.svg`
- Before print, convert the lockup's text to outlines and run a trademark search.

## 4. Agent page

Target sizes: works at 1366x768 without horizontal scrolling; uses extra width at 1920.

### Layout

```
+----------------------------------------------------------------------------------+
| STATE BAR  [icon] On a call  04:12   Claims: 3 waiting, longest 1:40   Phone ok  |
|            [ Ready v ]                                         Juma  [EN|SW] [x] |
+---------------------------+-------------------------------+----------------------+
| THE CALL                  | CONTEXT                       | MY QUEUES            |
| +255 7xx xxx 123          | Why they called (AI)          | Claims      3  1:40  |
| Swahili  Claims  wait 0:38|  "Member asks why claim of    | Membership  0        |
|                           |   12 Sep was rejected. Has    | General     1  0:20  |
| [Hold  H] [Mute  M]       |   member number ready."       |                      |
| [Transfer  T] [Keypad  K] | Notes                         | MY DAY               |
| [Hang up  Shift+E]        | [ text area               ]   | Calls 23  Talk 3:41  |
|                           |                               | Not ready 18 min     |
+---------------------------+-------------------------------+----------------------+
```

- **State bar** (always visible, full width, coloured by state): state word and icon, time in state, the state menu, queue load summary, phone health, agent name, language switch, sign out. Its background is the state colour; text is white (light theme) or dark ink (dark theme).
- **The call** (left, about 35%): caller number (and name if known), language, queue, wait time before answer, then the call controls as large buttons with their shortcut letters.
- **Context** (middle): AI summary first, then notes. During wrap-up this zone becomes the wrap-up form.
- **My queues and my day** (right, collapsible): waiting count and longest wait per queue the agent serves; the agent's own numbers for today.

### States of the page

| Page state | What the agent sees |
| --- | --- |
| Signed in, Not Ready | State bar in Not Ready colour with the reason; a clear "Go Ready" button; call zone shows "No call" and the phone health check |
| Ready, idle | State bar in Ready colour with time ready; call zone says "Waiting for the next call" and shows queue load |
| **Incoming offer** | A panel slides down under the state bar (the one shadowed element): caller, queue, language, AI summary (2 to 3 lines), a ring-timeout bar counting down, **Answer (Enter)** and **Decline**. Ring sound plays. If the tab is hidden, a browser notification fires. |
| On a call | Call zone shows live call timer and controls; context shows AI summary and notes |
| Transfer started | Call zone splits: caller (on hold) and target (ringing/talking), with Complete, Cancel, Conference |
| Wrap-up | State bar in Wrap-up colour with a countdown when the queue is time-boxed; context zone shows: wrap-up code (searchable list, required if the queue says so), notes, and **Finish and go Ready** / **Finish and stay Not Ready** |
| Phone problem | State bar turns Offline colour and says exactly what is wrong and what to do (see messages below). Calls are not offered until it is fixed |

### Keyboard shortcuts

Shown on the buttons. Active only when focus is not in a text field, except Answer.

| Action | Key |
| --- | --- |
| Answer the offer | Enter |
| Hold / resume | H |
| Mute / unmute | M |
| Transfer | T |
| Keypad | K |
| Hang up | Shift+E (two keys, so it cannot happen by accident; see UX_PRINCIPLES.md) |
| Go Ready / Not Ready menu | R |

### Messages (plain, specific, with the fix)

| Situation | Message |
| --- | --- |
| Microphone blocked | "Your browser is blocking the microphone. Allow it in the address bar, then press Reconnect." |
| Phone not registered | "Your phone is not connected to the call server. Press Reconnect. If it fails twice, tell your supervisor." |
| Missed offer | "You missed a call, so you were set to Not responding. Press Go Ready when you are back." |
| Wrap-up code required | "Choose a wrap-up code to finish this call." |

## 5. Supervisor page

```
+----------------------------------------------------------------------------------+
| Team: Claims & Providers v      Today: service level 84%   Waiting 5   Longest 2:10|
+----------------------------------------------------------------------------------+
| QUEUES   Claims 3 (1:40)  Providers 1 (0:12)  Escalations 1 (2:10, over target) |
+----------------------------------------------------------------------------------+
| Needs attention first                                                            |
| Agent     State              Time   Call                     Actions             |
| Zawadi    [ic] Not ready:     24:10  -                       Change state  ...   |
|           Break (over 15)                                                        |
| Peter     [ic] On a call      11:52  Claims, English          Monitor Whisper Barge|
| Halima    [ic] Ready          02:03  -                                            |
+----------------------------------------------------------------------------------+
```

- Rows are sorted by **needs attention**: Not Ready over the reason's limit, long calls over a threshold, Not responding, then the rest by state.
- Each row: name, state (icon + word + colour), time in state, reason, current call (queue, language, duration), actions.
- Actions follow permissions: Monitor, Whisper, Barge, Change state, Sign out, View history. While monitoring, a bar at the top shows "Monitoring Peter" with Switch to whisper, Switch to barge, Stop.
- The queue strip shows waiting count and longest wait per queue; a queue over its service-level target shows the word "over target", not only a colour.
- Numbers update live over the WebSocket; no full-page refresh.
- **How my team is doing** (the panel's second tab, so the screen still fits without scrolling): the team's UX_PRINCIPLES section 6 measures for Today or the Last 7 days, following the team filter. Six cards (calls answered and missed per 100 offers; median time to answer; median wrap-up and codes corrected; transfers completed; AI summaries flagged wrong; long breaks acted on, with the median minutes to act), then one line per agent so the supervisor sees who needs help. Refreshed every minute; numbers come from the API, words from the translation files.

## 6. Admin pages

- One pattern for every object (users, accounts, agents, skills, queues, rule sets, entry points): a searchable list on the left, the form on the right.
- Forms validate as the admin types and explain rules in place ("Every step must include the caller's language at level 4 or higher").
- The skill rule set editor shows the steps as a timeline (0 s, 30 s, 90 s, forever) with the requirements of each step, and a "Test with agent" box that shows which seeded agents would match at a chosen wait time.
- Destructive actions ask for confirmation and say what will happen ("Delete queue Claims? 5 agents lose this queue.").

## 7. Rules for building it (to keep it from looking generated)

- No card grids for everything; zones are separated by space and one divider, not boxes inside boxes.
- No gradients, no glassmorphism, no decorative illustrations, no hover animations on every element.
- No middle-dot meta strings ("A · B · C"), no "→" on buttons, no emoji.
- Buttons are named by their action, and the same action keeps the same name everywhere ("Hang up" button, "Call ended" message).
- Every state change the agent causes gets immediate feedback in the state bar.
- Respect `prefers-reduced-motion`: the offer panel appears without sliding.
- No heavy front-end framework for the PoC: plain HTML, CSS custom properties and small ES modules; the SIP library is the only large script. Total first load under 300 KB excluding the SIP library and font.

## 8. How to build and check it

1. **Static mockups first.** Before wiring any data, build the agent page in each page state (section 4) and the supervisor page as static HTML with the demo centre's data (ROUTING_DESIGN.md). Show screenshots at 1366x768 and 1920x1080, light and dark. Wait for approval.
2. Then wire to GraphQL and the WebSocket, keeping the approved markup.
3. **Automated checks** in CI: Playwright screenshots of each page state; axe-core accessibility scan with zero violations; keyboard-only run through "go Ready, answer, hold, transfer, hang up, wrap-up".
4. Tostao reviews every Swahili string.

## Sources

- [Cisco Finesse agent user guide (CCSU)](https://www.ccsu.edu/it/telecommunications/files/CCSU_Cisco_Finesse%20Agent__End_User_Guide_12-vs%203_updated.pdf)
- [Cisco Finesse supervisor user guide (CCSU)](https://www.ccsu.edu/it/telecommunications/files/CCSU_Cisco_Finesse%20Supervisor_Desktop_End_User_Guides12-vs%204_updated.pdf)
- [Genesys Cloud: navigate the agent workspace](https://help.genesys.cloud/700)
- [CX Today: "Toggle Tax" (cites Harvard Business Review 2022)](https://cxtoday.com/toggle-tax-the-silent-agent-engagement-killer-here)
- [Nielsen Norman Group: 10 usability heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/)
- [WCAG 2.2](https://www.w3.org/TR/WCAG22/) and [Understanding 2.5.8 Target Size (Minimum)](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)
