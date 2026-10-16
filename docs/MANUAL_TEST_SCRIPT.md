# Manual test script

For Tostao, and for the real agents and supervisors in UX_PRINCIPLES.md section 7. The automated
tests (`make ci`, `make calltests`, `make agentcheck`) already prove the system works; this script
checks that **people** can use it. Note the time each task takes, every mistake, and every comment.

## Before you start

```
make up          # PostgreSQL, Redis, Mailpit
make dev         # web, brain, writer, live relay, recordings, AI worker
```

- Agent page: http://127.0.0.1:8000/agent/ (agent 8003: `agent8003@example.org`, `LAB_AGENT_8003_PASSWORD`
  in `.env`; agent 7001: `agent1@example.org`, `LAB_AGENT_PASSWORD`; demo agents: `<name>@example.org`,
  `DEMO_PASSWORD`).
- Supervisor page: http://127.0.0.1:8000/supervisor/ (`asha@example.org` or `daudi@example.org`, `DEMO_PASSWORD`).
- Caller: Zoiper (1001 on this laptop, 1002 on the phone). **800** goes straight to queue Support
  (agents 7001, 8003, 9003); **803** (Swahili) and **802** (English) are answered by Jambo AI first.

## Agent tasks (time each one)

| # | Task | What should happen |
| --- | --- | --- |
| A1 | Sign in and go Ready | The state bar's button says **Go Ready** (white, filled). One click, or R. The bar turns green |
| A2 | Take a call from 800, put it on hold, resume | Answer with Enter. H holds ("On hold", caller hears music), H again resumes |
| A3 | Mute, send a digit | M mutes; K opens the keypad |
| A4 | Transfer to Claims (consult a colleague, then complete) | T opens transfer. The state bar says "Consulting <name>; caller on hold". Complete hands the caller over |
| A5 | Hang up and wrap up with a code | **Shift+E** hangs up (E alone does nothing). Choose a code; Finish and go Ready |
| A6 | Change that wrap-up code | Below the call zone: "Last call's wrap-up code", changeable for 2 minutes |
| A7 | Go Not Ready for lunch | Button says **Go Not Ready**; it opens the reasons; one click on Lunch |
| A8 | During a call, choose Not Ready after this call | The button says so; after wrap-up no new call is offered |
| A9 | Call 803, ask a question, then ask for a person | Jambo AI answers, then hands over. Your incoming call shows "Why they called (AI)" and the reason. Try **Summary wrong** / **Wrong queue** |
| A10 | Switch to Swahili (SW) during a call | Every label changes at once; the call goes on. Note any wrong or unclear word in SWAHILI_REVIEW.md |
| A11 | (Desk phone) In admin set agent 8003's Phone to 1001, sign in as 8003, call 800 from 1002 | Zoiper 1001 rings; the page says "Pick up phone 1001 to answer"; Hold, Transfer and wrap-up work from the page |

## Failure tasks

| # | Do this | What should happen |
| --- | --- | --- |
| F1 | Block the microphone in the browser (address bar) | State bar: "Phone not connected", a message saying how to fix it; Reconnect works after allowing it |
| F2 | Turn Wi-Fi off for 10 s during a call, then on | "Connection lost. Your call continues…", then it clears by itself |
| F3 | Consult a colleague who does not answer | "<name> did not answer. You are back with the caller." |
| F4 | Let an offer ring out | You become Not responding; the message says to press Go Ready |
| F5 | Stop the brain (`make dev`, Ctrl-C only the brain, or stop all), call 800 | The caller still reaches the plain Queue() fallback |

## Supervisor tasks

| # | Task | What should happen |
| --- | --- | --- |
| S1 | Find the agent who has been on break too long and act | They are at the top ("Over 15-minute limit"); Change state works |
| S2 | Monitor a call, then whisper, then barge, then stop | The bar says what each mode means; only the agent hears a whisper |
| S3 | Read a queue that is over target | The strip says "Over target", not only in colour |

## Check afterwards

- Admin -> Setup guide: every item Done or Not used; confirm the hands-on tests there.

- Admin -> Calls -> the calls you made: the stereo recording plays (customer on the left), the
  interaction shows the AI part (with transcript) and your part (code, notes).
- Admin -> Reports -> Today: your calls, your time in each state, the AI's handoff reasons.
- Admin -> Calls -> Events, filter type `ux.` / `offer.` / `wrapup.`: the measurements of what you
  just did (UX_PRINCIPLES.md section 6).

## Known gaps (not built yet)

- "Reconnecting to the call server" / "Calls are going to the backup queue" when the brain restarts
  or is down: the page cannot tell yet (the brain has no heartbeat the page can read).
- When the AI could not understand the caller, the agent sees the reason but not the AI transcript
  (it is on the call's admin page).
- The section 6 measurements are recorded but there is no dashboard for them yet.
- Two-question ease survey every 2 weeks (section 6): not built.
