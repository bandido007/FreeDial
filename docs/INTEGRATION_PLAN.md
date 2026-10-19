# Integrating AlgoDial with other systems: plan

Status: **draft for Tostao's decisions** (section 7), 19 October 2026. Nothing here is built yet.

## 1. What other systems want from AlgoDial

Three kinds of integration, each needing something different:

| Kind | Example | What they need from AlgoDial |
| --- | --- | --- |
| **A. Embed the call centre in their screens** | ERCP (Dharura): the call-centre agent works in ERCP's incident screen | An agent panel they can put in their page; their page learns of each call (caller number, call id) and can dial, attach their incident id, see the state |
| **B. Video meetings** | Jitsi, BigBlueButton (BBB) | Move a call into a video meeting, or bring a phone caller's voice into a meeting |
| **C. Numbers and reports** | A ministry dashboard, a BI tool, ERCP's PMO reports | Read calls, queues, agents and reports from their side, and be told when something happens |

Under all three sits one question: **how does an outside system prove who it is and what it may do.**

## 2. ERCP, the first real case

ERCP (Emergency Response Coordination Platform, e-Government Authority; documents in `ercp/`) has
its own incident form, Resource Allocation System (RAS), maps and agencies (Police, Hospital, Fire,
TANESCO). Its **call-centre part** is what AlgoDial does. Requirement by requirement:

| ERCP requirement | AlgoDial today | Integration work |
| --- | --- | --- |
| DHARURA-CALL-001 incoming calls: pick up, hold, transfer, agent status, record, calls in queue | Done (Milestones 3, 4, 5, 7) | Show these inside ERCP's screen (the widget, section 5.3) |
| CALL-001 incident form beside the call, auto-filled phone number, incident time, incident id | AlgoDial knows the caller's number, the call's start time and its id | The form stays **ERCP's**. AlgoDial tells ERCP's page about the call (number, time, call id); ERCP makes the incident id and **attaches it to the call** (section 5.4), so the recording and the reports link to the incident |
| CALL-001 auto-filled region, district, ward | Not possible from a phone number alone | Needs a location source (mobile operators through TCRA, or the caller's answer). Outside AlgoDial |
| DHARURA-CHAT-002 Telegram chat beside the form | Not built: AlgoDial has no chat channel | Decision D5 |
| DHARURA-CALL-003 conference with up to 5 parties (SDC agents), live participants table, add / remove, notes, recording after | 3-way conference, notes, recording (Milestones 5, 7) | **Conference of up to 5** with a participants list; SDC agents are either AlgoDial agents or outside numbers through a trunk (Milestone 4b) |
| DHARURA-RAS-004 resource dispatch, SDC on-duty agent | ERCP's own system | None: ERCP shows "dispatched" in its own screen, where the widget also is |
| DHARURA-CALL-005 outgoing follow-up calls, recorded | Not built: agents can only transfer, not dial | **Agent outbound calls** (click-to-dial), recorded (section 5.4) |

So for ERCP, AlgoDial should be **embedded inside ERCP**, not the other way round: ERCP owns
incidents, AlgoDial owns calls, and the call id plus incident id join them.

## 3. Principles (from products that do this well)

1. **API first, one source of truth.** Every integration uses the same services AlgoDial's own pages
   use. Nothing an outside system does may break rule 2 (only the brain changes call and agent state):
   it asks, through the same commands.
2. **Embed in an iframe, talk through `postMessage`, check the origin.** Amazon Connect Streams loads its
   agent panel (CCP) in an iframe with `initCCP(container, options)`, and every domain that embeds it
   must be allow-listed. Genesys Cloud's Embeddable Framework takes `clickToDial` messages by
   `postMessage` and checks the sender's origin. Salesforce Open CTI offers `screenPop` and click-to-dial.
   Same shape for AlgoDial: the host page never touches AlgoDial's internals, only a small, versioned
   message contract.
3. **Sign in inside the frame with a popup.** Browsers block third-party cookies, so the embedded panel
   signs in through a popup (Amazon Connect's `loginPopup`) and keeps its own token. Later, the host's
   single sign-on can hand over a short-lived token.
4. **Tell, don't make them ask: signed webhooks.** Stripe and the Standard Webhooks spec:
   - each delivery carries an **id**, a **timestamp** and an **HMAC-SHA256 signature** over id, timestamp
     and body;
   - receivers reject stale timestamps (5 minutes) and drop duplicates by id;
   - failed deliveries are retried with back-off;
   - every delivery is logged and can be replayed.

   AlgoDial's events already carry an `event_id`, so retries are naturally safe.
5. **Machines sign in as machines.** OAuth 2.0 client credentials: each outside system gets a client id
   and secret, holds a **role** (so the same `can_*` permissions apply), and every call is audited. AlgoDial
   already runs Django OAuth Toolkit, which supports this grant.
6. **Least data.** An integration gets only the scopes it needs. Recordings go out only through short-lived
   signed links, and only with `can_listen_recordings`. Caller numbers are personal data under Tanzania's
   Personal Data Protection Act 2022, so every read is audited.
7. **Versioned contracts.** Additive changes within a version; a breaking change is a new version
   (already AlgoDial's rule for `contracts/`).

## 4. What must exist first: AlgoDial reachable over HTTPS

Today the browser phone talks to Asterisk at `ws://127.0.0.1:8091`, so it works only on the server itself.
An iframe inside another system (served over HTTPS) needs:
- AlgoDial served over **HTTPS** under a real name;
- Asterisk's WebSocket as **WSS** (TLS) reachable from agents' computers, with a TURN/STUN setup if
  networks need it.

This is the deployment step (`docs/DEPLOYMENT.md`) and comes before the widget.

## 5. The design, part by part

### 5.1 Integrations in the admin (one place to manage every outside system)

A new app `apps/integrations`. An **Integration** records:
- its name and owner;
- an **OAuth client** (client id and secret; the secret is shown once);
- the **role** it holds;
- **allowed origins** (where its pages may embed the widget);
- its **webhook endpoints** with their **secrets** and the **event types** each wants;
- active or not.

Every request an integration makes is audited (the starter's audit app) and rate-limited.

### 5.2 Events out: webhooks

A new process, `run_webhooks`, in `make dev` like the writer:
- reads `algodial:events` through its own consumer group;
- for each subscription that wants the event, POSTs it signed the Standard Webhooks way (`webhook-id` = the event's `event_id`, `webhook-timestamp`, `webhook-signature`);
- retries with back-off for 24 hours;
- logs every delivery;
- has a **Replay** button in admin.

The public events are the contract's own names (`call.started`, `call.connected`, `call.ended`, `agent.state`, `transfer.completed`, `ai.ended`, `callback.requested`…). They are documented with their fields, and only what that integration's role may see is included.

### 5.3 The embeddable agent widget and its JavaScript SDK

- **`/embed/agent/`:** a compact agent panel: state, the incoming call with its context, answer, hold, transfer, conference, keypad, dial, wrap-up. It's built from the agent page, with the same translations, shortcuts and accessibility.
- **`algodial-embed.js`** for the host page:
  - `AlgoDial.embed({ url, container })`;
  - events: `call.offered`, `call.connected`, `call.ended`, `agent.state`, `wrapup.done`;
  - methods: `dial(number)`, `attach(callId, data)`, `setReady()`, `setNotReady(reason)`, `hangUp()`.
- **Messages** travel by `postMessage` only. Both sides check the origin, and the host's origin must be in the Integration's allowed origins.
- **Embedding is limited.** AlgoDial sends `Content-Security-Policy: frame-ancestors` listing those origins, so no other site can frame it.
- **The iframe** needs `allow="microphone; autoplay"` for the browser phone, or the agent uses a desk phone (already built).
- **Sign-in:** a popup (principle 3).

### 5.4 Call data and agent outbound calls (needed by the widget and ERCP)

- **Attached data.** An outside system attaches its own keys to a call, e.g. `{"incident_id": "DH-2026-00123"}`. This is a new contract command `call.attach`, handled by the brain (rule 2). The data is then:
  - kept on the Call record;
  - shown on the agent page and the call's admin page;
  - included in events, webhooks and reports.
- **Agent outbound calls (click-to-dial).** An agent, or the widget's `dial()`, calls a number:
  - internal numbers directly;
  - outside numbers through the outbound routes and trunks of Milestone 4b, with the same permission (`can_dial_external`) and the same failover.

  Outbound calls are recorded like inbound ones. Today recording starts in the dialplan before `Stasis()`; calls the brain originates need it started over ARI.
- **Conference of up to 5:** extends Milestone 5's 3-way conference, with a live participants list and add / remove.

### 5.5 Numbers and reports for other systems

The read API, for kind C:
- **calls**, a page at a time with a cursor (`since` / `after`), so a BI tool fetches each call once;
- **queues now** (waiting, longest wait, service level);
- **agents now** (state);
- **reports**: the existing `getReport` / `exportReport`, including the section 6 measures;
- **recordings**: a short-lived signed link per call, only with `can_listen_recordings`.

Pull for history, webhooks (5.2) for "tell me now". A nightly file export to the ministry's storage can come later if asked.

### 5.6 Video: Jitsi and BigBlueButton

Three steps, each useful on its own:
1. **Escalate to video.**
   - **Setup:** a Video provider in admin, either a BBB URL plus shared secret, or a Jitsi domain plus app id and secret.
   - **On a call:** the agent presses **Video**. AlgoDial creates the meeting (BBB `create` with its checksum; Jitsi a room plus a signed JWT) and gives the agent and the caller their join links.
   - **Links and records:** the caller's link goes by SMS, which needs an SMS gateway, or through the host system. The call record keeps the meeting id.
2. **The phone caller into the meeting's audio.** Bridge the call to the meeting over SIP:
   - Jitsi through **Jigasi**, whose SIP address names the room;
   - BBB through its own FreeSWITCH, dialling the meeting's **`voiceBridge`** PIN.

   AlgoDial treats the meeting server as a **trunk** (Milestone 4b), so this is a transfer to a SIP destination with the same failover and recording.
3. **AlgoDial inside the meeting tool** (later): a panel inside BBB through its plugin SDK (as in your `bbb-ai-meeting-plugin`), or next to a Jitsi room through its IFrame API.

Note from Jitsi's own deployments: a conference with a phone participant is not end-to-end encrypted.

## 6. Plan: milestones in order

Each follows the house rule: tests for "Done when" first, then the code, then the lab check.

| Milestone | What | Done when |
| --- | --- | --- |
| **M10 Deployment over HTTPS/WSS** (prerequisite) | AlgoDial on HTTPS with a real name; Asterisk WSS reachable from agents' computers | An agent on another computer signs in and takes a call with the browser phone |
| **M11 Integrations and machine sign-in** | `apps/integrations`: clients (client credentials), roles, allowed origins, audit, rate limits; the integration API documented | An outside script with its client id and secret reads today's report and the calls list, is refused outside its role, and each request is in the audit log |
| **M12 Webhooks** | `run_webhooks`; signed deliveries; retries; delivery log and replay in admin | A test receiver gets `call.started` and `call.ended` within 2 s and verifies the signature; a receiver that fails gets the retries, and Replay resends one |
| **M13 Call data and agent outbound calls** | `call.attach`; click-to-dial internal and outside (Milestone 4b routes), recorded; conference up to 5 with a participants list | From the agent page an agent dials a number through a trunk (SIPp in the lab), attaches `incident_id`, and the call, its recording and its events carry it; a 5-party conference adds and drops a party |
| **M14 Embeddable widget and SDK** | `/embed/agent/`, `algodial-embed.js`, origin checks, CSP `frame-ancestors`, popup sign-in | A sample host page (an ERCP-like incident screen) embeds the widget: the incoming call pops the caller's number into the form, the page attaches the incident id and dials out; a page from a non-allowed origin cannot frame it; keyboard, Swahili and 1366x768 hold |
| **M15 Data for other systems** | Calls with a cursor, queues and agents now, recordings by signed link | A BI-style script pulls yesterday's calls page by page with no duplicates and no gaps, and a recording link stops working after it expires |
| **M16 Video** | Video provider in admin; escalate to video; caller's audio into the meeting through Jigasi or BBB dial-in as a trunk | On the lab, an agent moves a call into a meeting and the phone caller is heard in it |
| **M17 ERCP pilot** | ERCP embeds the widget in its incident screen; conferences with SDC agents; follow-up calls | ERCP's DHARURA-CALL-001, 003 and 005 pass in a joint test with the ERCP team |

M11–M13 are useful to every integration. M14 and M17 are ERCP's path. M15 serves dashboards. M16 is independent and can move earlier if a customer needs it first.

## 7. Decisions for Tostao

| # | Question | Options | Recommendation |
| --- | --- | --- | --- |
| D1 | What API do outside systems use? | (a) A small REST API, `/api/v1/…`, with an OpenAPI description, built on the same services; GraphQL stays for AlgoDial's own pages. (b) Only the existing GraphQL. | **(a)**: what Amazon Connect, Genesys and Twilio offer integrators; government teams and BI tools expect REST and can generate clients from OpenAPI. It is a thin layer over the same services, so no logic is duplicated. CLAUDE.md names GraphQL as the API, so this changes the architecture and is your call |
| D2 | How does the embedded widget sign in? | (a) A popup with AlgoDial's own sign-in. (b) The host's single sign-on hands over a token. | **(a) first**, as Amazon Connect does by default; add (b) when ERCP has an identity provider to trust |
| D3 | Which video tool first? | Jitsi (IFrame API, JWT, Jigasi for phones) or BBB (create/join API with checksum, FreeSWITCH dial-in, plugin SDK) | **The one ERCP and your customers already run.** If none, BBB, since you already build BBB plugins; Jitsi is the quicker of the two to embed |
| D4 | For ERCP: who owns the incident form? | (a) ERCP, with AlgoDial embedded in it. (b) AlgoDial hosts configurable forms. | **(a)**: matches ERCP's design (its form, RAS and maps are one flow), and keeps AlgoDial a call centre |
| D5 | ERCP's Telegram chat (CHAT-002) | (a) Out of AlgoDial for now; ERCP's chat stays where it is. (b) A chat channel in AlgoDial (omnichannel) | **(a) now**; chat is a product of its own (a new milestone after M17 if wanted) |

## Sources

- Amazon Connect Streams (embedding the CCP, allow-listed domains, login popup): <https://github.com/amazon-connect/amazon-connect-streams>
- Genesys Cloud Embeddable Framework (click-to-dial over `postMessage`, origin check): <https://developer.genesys.cloud/forum/t/sample-framework-js-file/20770>, <https://help.mypurecloud.com/?p=70449>
- Salesforce Open CTI `screenPop()`: <https://developer.salesforce.com/docs/atlas.en-us.api_cti.meta/api_cti/sforce_api_cti_screenpop_lex.htm>
- Webhook signatures, timestamps and idempotency (Stripe's scheme, Standard Webhooks): <https://dev.to/eventdock/webhook-signature-verification-compared-stripe-paddle-hubspot-slack-twilio-4jfk>, <https://webhookrelay.com/verify-standard-webhooks-signature.md>
- CloudEvents (vendor-neutral event format, CNCF): <https://cloudevents.io/>
- Jitsi IFrame API and JWT: <https://jitsi.org/iframe>; Jigasi room addressing: <https://github.com/jitsi-contrib/jigate>
- BigBlueButton API (`create`, `join`, checksum, `voiceBridge`): <https://docs.bigbluebutton.org/development/api/>; dial-in over a SIP trunk: <https://github.com/manishkatyan/bbb-twilio>
