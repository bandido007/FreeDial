# Reports (M9)

Admin → **Reports** (permission `can_view_reports`, held by the Contact Centre Manager role), or the
API queries `getReport` and `exportReport`. Pick a period: today, yesterday, the last 7 or 30 days,
or two dates (both included). Each table downloads as PDF, Excel or CSV.

Every number is computed from the **Event table**: the brain's own record of each decision, never
edited. The code is one pure function, `apps/reports/compute.py`, tested on a scripted day whose
figures were worked out by hand (`apps/reports/tests/test_compute.py`).

## Which calls

A call is in the report when it **started** inside the period. All its events count, even those
after the period ends (a call started at 23:59 and answered at 00:01 counts with its answer).
Agent time in state is cut to the period exactly.

## Calls per queue

A call is **offered** to every queue it waited in, once per stay. The brain says when a call starts
waiting (`call.queued`, with why: entry, overflow, routing, AI handoff, AI failed, transfer).
Talking to the AI is not waiting in a queue.

| Column | Meaning |
| --- | --- |
| Offered | Stays in this queue |
| Answered | An agent answered during the stay |
| Service level | Answered within the queue's target seconds, out of **all** offered (same as the supervisor page). "(met)" / "(missed)" against the target percent |
| Abandoned | The caller hung up while waiting. "short": within the first 5 s (still counted) |
| Abandon rate | Abandoned, out of offered |
| Overflowed | Left unanswered for another queue (overflow, routing, transfer) |
| Voicemail | Left to leave a message |
| Avg speed of answer | Average wait of the answered calls, from entering this queue |
| Longest wait | The longest anyone waited here, answered or not |
| Avg talk | Answer to the agent's leg ending; hold time included |
| Avg wrap-up | The agent's leg ending to their wrap-up |
| Avg handle time | Talk + wrap-up |

"All queues" adds the queues up. It shows no service level: each queue has its own target.

Handle time is the answering agent's. After a consult transfer, the colleague who took over is not
measured (a later refinement).

## Agent time in state

From `agent.state`: **Available** (Ready and idle), **Ringing**, **Talking**, **On hold**,
**Wrap-up**, **Not responding** (missed an offer), and **Not Ready** split by reason. Time logged
out is not counted. An agent's state at the start of the period is their last state before it.

## AI agents

| Column | Meaning |
| --- | --- |
| Calls | Calls the AI answered |
| Resolved by the AI | The AI ended the call itself (`ai.hangup`) |
| Containment | Resolved, out of the AI's calls |
| Handed to a person | Handoffs, by reason: asked for a person, the AI decided, not understood (two failed turns), sensitive topic |
| AI failed | The AI did not come up or dropped; the call went to the fallback queue |
| Caller hung up | While still talking to the AI |
| Avg time with the AI | From the AI answering to its part ending |

## How well AlgoDial works for its users (UX_PRINCIPLES.md section 6)

The HEART metrics, computed by `apps/reports/experience.py` from the signals in the Event table. Each
line says what it is based on ("2 offers answered"), so a metric resting on few events reads as such.

| Who | Measure | From |
| --- | --- | --- |
| Agent | Median seconds to answer; missed offers per 100 | `offer.answered` (seconds rung), `offer.missed` |
| Agent | Median wrap-up; corrections per 100 calls | `wrapup.done` (seconds), `wrapup.code_changed` |
| Agent | Transfer success rate | `transfer.completed` out of `transfer.started` (cancelled and failed shown) |
| Agent | Average ease (1 to 5) | The 2-weekly survey: not built, the line says so |
| Agent | Summary-wrong and wrong-queue rate per 100 AI handoffs | `ux.ai_summary_wrong`, `ux.ai_queue_wrong`, out of `ai.ended` with outcome handoff |
| Supervisor | Median minutes to action | A Not Ready spell past its reason's limit (admin → Not Ready reasons, Max seconds): minutes from going over to `agent.forced`; spells the agent ended themselves are counted apart |
| Admin | Minutes from creating an agent to their first call | Agents created in the period: `created_at` to their first `offer.answered` |
| Admin | Errors caught before saving | `ux.admin_validation_error`, by model |
| All | 95th percentile per button | `ux.action` ms, against the 1 s budget; presses not confirmed within 10 s counted |

## Older events

Calls from before M9 have no `call.queued`. Their queue is the one in `call.started`, from the
start of the call.
