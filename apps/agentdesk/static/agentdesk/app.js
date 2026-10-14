/* AlgoDial agent page: sign in, browser phone (JsSIP over WebSocket), live state, call controls.
 *
 * Every button asks the API, the API asks the brain, and the brain's answer comes back
 * on the live WebSocket (/ws/agent) as events. The page never decides call state itself.
 */
(() => {
  "use strict";

  const config = JSON.parse(document.getElementById("desk-config").textContent);
  const $ = (id) => document.getElementById(id);

  const store = {
    get(key) { try { return JSON.parse(sessionStorage.getItem(key)); } catch { return null; } },
    set(key, value) { try { sessionStorage.setItem(key, JSON.stringify(value)); } catch { /* private mode */ } },
    drop(key) { try { sessionStorage.removeItem(key); } catch { /* private mode */ } },
  };

  const ACTIVITY = {
    idle: "Idle", offered: "Ringing", talking: "On a call", on_hold: "Customer on hold",
    wrapup: "Wrap-up", not_responding: "Not responding (missed a call)",
  };
  const AVAILABILITY = { logged_out: "Logged out", ready: "Ready", not_ready: "Not Ready" };

  let tokens = store.get("algodial.tokens");
  let desk = null;      // myAgentDesk
  let ua = null;        // JsSIP user agent
  let session = null;   // the current call on the browser phone
  let live = null;      // the live WebSocket
  let liveRetry = 1000;
  let state = null;     // the agent's state as the brain last said
  let offer = null;     // the last call.offered event for this agent
  let transfer = null;  // { id, kind, state: ringing | consulting | conference } while this agent transfers
  let since = Date.now();
  let refreshTimer = null;
  let signingOut = false;

  class DeskError extends Error {
    constructor(code, message) { super(message); this.code = code; }
  }

  // ── what happened ────────────────────────────────────────────────────────
  function note(text) {
    const item = document.createElement("li");
    const time = document.createElement("time");
    time.textContent = new Date().toLocaleTimeString();
    item.append(time, document.createTextNode(text));
    $("log").prepend(item);
    while ($("log").children.length > 60) $("log").lastChild.remove();
  }
  function notice(text) { $("notice").textContent = text || ""; }

  // ── sign in and tokens ───────────────────────────────────────────────────
  function keepTokens(reply) {
    tokens = {
      access: reply.access_token,
      refresh: reply.refresh_token,
      expiresAt: Date.now() + (reply.expires_in || 3600) * 1000,
    };
    store.set("algodial.tokens", tokens);
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(refresh, Math.max(30000, tokens.expiresAt - Date.now() - 60000));
  }

  async function tokenRequest(fields) {
    const body = new URLSearchParams({ client_id: config.clientId, ...fields });
    const reply = await fetch(config.tokenUrl, { method: "POST", body, credentials: "omit" });
    return reply.json();
  }

  async function signIn(event) {
    event.preventDefault();
    $("signin-error").textContent = "";
    const fields = { grant_type: "password", username: $("email").value.trim(), password: $("password").value };
    if ($("otp").value.trim()) fields.otp = $("otp").value.trim();
    const reply = await tokenRequest(fields);
    if (!reply.success) {
      if (reply.code === 8014) $("otp-row").hidden = false;  // a two-factor code is needed
      $("signin-error").textContent = reply.message || reply.error_description || "Sign-in failed.";
      return;
    }
    keepTokens(reply);
    $("password").value = "";
    $("otp").value = "";
    await openDesk();
  }

  async function refresh() {
    if (!tokens || !tokens.refresh) return false;
    const reply = await tokenRequest({ grant_type: "refresh_token", refresh_token: tokens.refresh });
    if (!reply.success) return false;
    keepTokens(reply);
    return true;
  }

  async function api(query, variables = {}) {
    if (!tokens) throw new DeskError(0, "Not signed in.");
    if (Date.now() > tokens.expiresAt - 30000 && !(await refresh())) {
      showSignIn("Your session ended. Please sign in again.");
      throw new DeskError(0, "Session ended.");
    }
    const reply = await fetch(config.graphqlUrl, {
      method: "POST",
      credentials: "omit",  // the token is the only identity: an admin cookie on this host must not count
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${tokens.access}` },
      body: JSON.stringify({ query, variables }),
    });
    const body = await reply.json();
    if (body.errors && body.errors.length) throw new DeskError(0, body.errors[0].message);
    const answer = Object.values(body.data)[0];
    if (!answer.status) throw new DeskError(answer.code, answer.message);
    return answer.data;
  }

  // ── the desk ─────────────────────────────────────────────────────────────
  async function openDesk() {
    try {
      desk = await api(`query { myAgentDesk { status code message data {
        uid number name notReadyReasons wrapupCodes
        state { availability activity reason call wrapupCall endpoint }
        transferTargets { queues agents { uid number name availability activity } } } } }`);
    } catch (error) {
      showSignIn(error.message);
      return;
    }
    $("signin").hidden = true;
    $("desk").hidden = false;
    $("agent-name").textContent = desk.name;
    $("agent-number").textContent = desk.number;
    fill($("reason"), desk.notReadyReasons, "Not Ready");
    fill($("wrapup-code"), desk.wrapupCodes, "No code");
    fillTargets(desk.transferTargets);
    applyState(desk.state);
    connectLive();
    await startPhone();
  }

  function fill(select, names, empty) {
    select.replaceChildren();
    if (!names.length) select.append(new Option(empty, ""));
    for (const name of names) select.append(new Option(name, name));
  }

  function fillTargets(targets) {
    const agents = $("transfer-agent");
    agents.replaceChildren();
    for (const other of targets.agents) {
      const busy = other.availability === "logged_out" || other.activity !== "idle";
      const option = new Option(`${other.number} ${other.name} (${busy ? other.activity.replace("_", " ") : other.availability.replace("_", " ")})`, other.uid);
      option.disabled = other.availability === "logged_out";
      agents.append(option);
    }
    fill($("transfer-queue"), targets.queues, "No queue");
  }

  async function refreshTargets() {
    try {
      const data = await api("query { myAgentDesk { status code message data { transferTargets { queues agents { uid number name availability activity } } } } }");
      fillTargets(data.transferTargets);
    } catch { /* keep the old list */ }
  }

  function showSignIn(message) {
    stopEverything();
    $("desk").hidden = true;
    $("signin").hidden = false;
    $("signin-error").textContent = message || "";
  }

  function applyState(next) {
    const changed = !state || state.availability !== next.availability || state.activity !== next.activity;
    state = { ...next, wrapupCall: next.wrapupCall ?? next.wrapup_call };
    if (changed) since = Date.now();
    const badge = $("availability");
    badge.textContent = AVAILABILITY[state.availability] || state.availability;
    badge.className = `badge ${state.availability}`;
    const reason = state.availability === "not_ready" && state.reason ? ` (${state.reason})` : "";
    $("activity").textContent = (ACTIVITY[state.activity] || state.activity) + reason;
    $("ready").disabled = state.availability === "ready" && state.activity !== "not_responding";
    $("not-ready").disabled = state.availability === "logged_out";
    $("wrapup").hidden = state.activity !== "wrapup";
    renderCall();
  }

  setInterval(() => {
    const seconds = Math.floor((Date.now() - since) / 1000);
    $("since").textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }, 1000);

  async function command(query, variables) {
    notice("");
    try {
      await api(query, variables);
    } catch (error) {
      notice(error.message);
    }
  }

  // ── live updates ─────────────────────────────────────────────────────────
  function connectLive() {
    if (live || !tokens) return;
    const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${config.liveUrl}`;
    live = new WebSocket(url);
    live.onopen = () => live.send(JSON.stringify({ type: "auth", token: tokens.access }));
    live.onmessage = (message) => {
      const data = JSON.parse(message.data);
      if (data.type === "hello") {
        liveRetry = 1000;
        chip("live-chip", "Live: on", "on");
        applyState(data.state);  // a reconnect re-reads the state: nothing missed matters
      } else if (data.type === "event") {
        onEvent(data.event);
      }
    };
    live.onclose = async (event) => {
      live = null;
      chip("live-chip", "Live: off", "bad");
      if (signingOut || !tokens) return;
      if (event.code === 4401 && !(await refresh())) {
        showSignIn("Your session ended. Please sign in again.");
        return;
      }
      setTimeout(connectLive, liveRetry);
      liveRetry = Math.min(liveRetry * 2, 15000);
    };
  }

  function onEvent(event) {
    const data = event.data;
    switch (event.type) {
      case "agent.state":
        applyState(data);
        break;
      case "call.offered":
        offer = data;
        note(`Call offered from ${data.caller_name || data.caller || "unknown"} (${data.queue || "queue"})`);
        renderCall();
        break;
      case "call.offer_failed":
        note(`Offer ${data.reason.replace("_", " ")}`);
        break;
      case "call.connected":
        note("Connected to the customer");
        break;
      case "call.held":
        note("Customer on hold");
        break;
      case "call.resumed":
        note("Call resumed");
        break;
      case "call.ended":
        note(`Call ended: ${data.result} (${data.reason})`);
        transfer = null;
        break;
      case "transfer.started":
        if (data.agent === desk.uid) {
          transfer = { id: data.transfer, kind: data.kind, state: "ringing" };
          note(`${data.kind === "blind" ? "Transferring" : "Consulting"} ${data.target_kind} ${data.target}`);
        } else if (data.target_agent === desk.uid) {
          offer = { caller: "Transfer", caller_name: "Transferred call", queue: data.kind === "consult" ? "a colleague wants to consult" : "transferred to you" };
        }
        break;
      case "transfer.answered":
        if (transfer && transfer.id === data.transfer && transfer.kind === "consult") transfer.state = "consulting";
        break;
      case "transfer.completed":
      case "transfer.cancelled":
      case "transfer.failed":
        if (transfer && transfer.id === data.transfer) {
          note(`Transfer ${event.type.split(".")[1]}${data.reason ? ` (${data.reason.replace("_", " ")})` : ""}`);
          transfer = null;
        }
        break;
      case "conference.started":
        if (data.agent === desk.uid) transfer = { id: data.transfer, kind: "consult", state: "conference" };
        note("Conference started");
        break;
      case "call.party_left":
        if (transfer && transfer.state === "conference") transfer = null;
        note("Someone left the conference");
        break;
      case "agent.wrapped_up":
        note(data.by === "timeout" ? "Wrap-up time ran out" : `Wrapped up: ${data.code || "no code"}`);
        break;
      case "command.rejected":
        notice(`Not done: ${data.reason}`);
        break;
      default:
        break;
    }
    renderCall();
  }

  // ── the browser phone ────────────────────────────────────────────────────
  function chip(id, text, kind) {
    const element = $(id);
    element.textContent = text;
    element.className = `chip ${kind || ""}`;
  }

  async function startPhone() {
    if (ua) return;
    let phone;
    try {
      phone = await api(`mutation { startAgentSession { status code message data {
        sipId password wsUrl domain endpoint commandId } } }`);
    } catch (error) {
      notice(`Phone not started: ${error.message}`);
      return;
    }
    JsSIP.debug.disable("JsSIP:*");
    ua = new JsSIP.UA({
      sockets: [new JsSIP.WebSocketInterface(phone.wsUrl)],
      uri: `sip:${phone.sipId}@${phone.domain}`,
      authorization_user: phone.sipId,
      password: phone.password,
      display_name: desk.name,
      register: true,
      register_expires: 120,
      session_timers: false,
      user_agent: "AlgoDial agent page",
    });
    ua.on("connecting", () => chip("phone-chip", "Phone: connecting"));
    ua.on("disconnected", () => chip("phone-chip", "Phone: no connection", "bad"));
    ua.on("registered", () => { chip("phone-chip", `Phone: ${phone.sipId}`, "on"); note("Browser phone registered"); });
    ua.on("unregistered", () => chip("phone-chip", "Phone: off"));
    ua.on("registrationFailed", (event) => chip("phone-chip", `Phone: ${event.cause}`, "bad"));
    ua.on("newRTCSession", ({ session: incoming, originator }) => {
      if (originator !== "remote") return;
      if (session) {
        incoming.terminate({ status_code: 486, reason_phrase: "Busy Here" });
        return;
      }
      session = incoming;
      incoming.on("peerconnection", ({ peerconnection }) => {
        peerconnection.addEventListener("track", (track) => { $("remote-audio").srcObject = track.streams[0]; });
      });
      incoming.on("confirmed", () => { ring(false); renderCall(); });
      incoming.on("ended", () => endSession());
      incoming.on("failed", () => endSession());
      ring(true);
      renderCall();
    });
    ua.start();
  }

  function endSession() {
    ring(false);
    session = null;
    offer = null;
    $("remote-audio").srcObject = null;
    renderCall();
  }

  function renderCall() {
    const ringing = session && !session.isEstablished() && !session.isEnded();
    const talking = session && session.isEstablished();
    $("call").hidden = !ringing && !talking;
    if ($("call").hidden) return;
    const identity = session.remote_identity;
    const caller = (offer && (offer.caller_name || offer.caller)) || identity.display_name || identity.uri.user;
    $("call-title").textContent = ringing ? "Incoming call" : "On a call";
    $("caller").textContent = caller;
    $("caller-queue").textContent = offer && offer.queue ? `via ${offer.queue}` : "";
    const held = state && state.activity === "on_hold";
    $("answer").hidden = $("reject").hidden = !ringing;
    $("hangup").hidden = !talking;
    $("hold").hidden = !talking || held || Boolean(transfer);
    $("resume").hidden = !talking || !held || Boolean(transfer);
    $("transfer").hidden = !talking || held || Boolean(transfer);
    $("consult").hidden = !(transfer && transfer.kind === "consult" && transfer.state !== "conference");
    $("consult-status").textContent = transfer && transfer.state === "ringing" ? "Ringing…" : "Talking to them; the customer is on hold";
    $("consult-complete").disabled = $("consult-conference").disabled = !(transfer && transfer.state === "consulting");
    $("conference").hidden = !(transfer && transfer.state === "conference");
  }

  // A soft two-tone ring while a call is offered (Web Audio, no files).
  let ringer = null;
  function ring(on) {
    if (!on) {
      if (ringer) { clearInterval(ringer.timer); ringer.context.close(); ringer = null; }
      return;
    }
    if (ringer) return;
    const context = new AudioContext();
    const beep = () => {
      for (const [frequency, start] of [[440, 0], [480, 0]]) {
        const oscillator = context.createOscillator();
        const gain = context.createGain();
        oscillator.frequency.value = frequency;
        gain.gain.value = 0.05;
        oscillator.connect(gain).connect(context.destination);
        oscillator.start(context.currentTime + start);
        oscillator.stop(context.currentTime + 1.2);
      }
    };
    beep();
    ringer = { context, timer: setInterval(beep, 3000) };
  }

  // ── buttons ──────────────────────────────────────────────────────────────
  $("signin-form").addEventListener("submit", signIn);
  $("ready").addEventListener("click", () =>
    command("mutation { setMyAvailability(ready: true) { status code message } }"));
  $("not-ready").addEventListener("click", () =>
    command("mutation R($reason: String) { setMyAvailability(ready: false, reason: $reason) { status code message } }",
      { reason: $("reason").value }));
  $("answer").addEventListener("click", () => {
    if (!session) return;
    ring(false);
    session.answer({ mediaConstraints: { audio: true, video: false }, pcConfig: { iceServers: [] } });
  });
  $("reject").addEventListener("click", () => session && session.terminate({ status_code: 486, reason_phrase: "Busy Here" }));
  $("hangup").addEventListener("click", () => session && session.terminate());
  $("hold").addEventListener("click", () => command("mutation { holdMyCall(hold: true) { status code message } }"));
  $("resume").addEventListener("click", () => command("mutation { holdMyCall(hold: false) { status code message } }"));
  function showTargetField() {
    const kind = $("transfer-target-kind").value;
    $("transfer-agent").hidden = kind !== "agent";
    $("transfer-queue").hidden = kind !== "queue";
    $("transfer-number").hidden = kind !== "number";
    if (kind === "agent") refreshTargets();
  }
  $("transfer-target-kind").addEventListener("change", showTargetField);
  $("transfer-go").addEventListener("click", () => {
    const targetKind = $("transfer-target-kind").value;
    const target = { agent: $("transfer-agent").value, queue: $("transfer-queue").value, number: $("transfer-number").value.trim() }[targetKind];
    command(
      "mutation T($kind: String!, $targetKind: String!, $target: String!) { transferMyCall(kind: $kind, targetKind: $targetKind, target: $target) { status code message } }",
      { kind: $("transfer-kind").value, targetKind, target },
    );
  });
  const step = (name) => () => command("mutation S($step: String!) { myTransferStep(step: $step) { status code message } }", { step: name });
  $("consult-complete").addEventListener("click", step("complete"));
  $("consult-conference").addEventListener("click", step("conference"));
  $("consult-cancel").addEventListener("click", step("cancel"));
  $("conference-drop").addEventListener("click", step("drop_other"));
  $("conference-leave").addEventListener("click", step("leave"));

  $("wrapup-done").addEventListener("click", () =>
    command("mutation W($code: String!) { finishMyWrapup(code: $code) { status code message } }",
      { code: $("wrapup-code").value }));
  $("signout").addEventListener("click", signOut);

  async function signOut() {
    if (session) {
      notice("Hang up before signing out.");
      return;
    }
    signingOut = true;
    try {
      await api("mutation { endAgentSession { status code message } }");
    } catch (error) {
      notice(error.message);
      signingOut = false;
      return;
    }
    if (tokens) {
      const body = new URLSearchParams({ token: tokens.access, client_id: config.clientId });
      fetch(config.revokeUrl, { method: "POST", body, credentials: "omit" }).catch(() => {});
    }
    tokens = null;
    store.drop("algodial.tokens");
    showSignIn("Signed out.");
    signingOut = false;
  }

  function stopEverything() {
    clearTimeout(refreshTimer);
    ring(false);
    if (ua) { ua.stop(); ua = null; }
    if (live) { live.onclose = null; live.close(); live = null; }
    chip("phone-chip", "Phone: off");
    chip("live-chip", "Live: off");
    session = null;
  }

  window.addEventListener("beforeunload", () => { if (ua) ua.stop(); });
  // For lab/agentpage.py: read-only access to the current call (WebRTC stats).
  window.agentDesk = Object.freeze({
    get session() { return session; },
    get state() { return state; },
    get transfer() { return transfer; },
  });

  if (tokens) {
    openDesk();
  } else {
    showSignIn("");
  }
})();
