/* AlgoDial supervisor page: your teams live (agents, calls, queues), monitor / whisper / barge, force agent state.
 *
 * Like the agent page: the buttons ask the API, the API asks the brain, the answers arrive on the live
 * WebSocket. The browser phone rings once when you start listening and stays on while you switch mode.
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
  const MODE_HELP = {
    monitor: "You hear the customer and the agent. Nobody hears you.",
    whisper: "You hear both; only the agent hears you. The customer does not.",
    barge: "You are in the call: everyone hears everyone.",
  };

  let tokens = store.get("algodial.supervisor.tokens");
  let desk = null;
  let ua = null;
  let session = null;
  let live = null;
  let liveRetry = 1000;
  let listening = null;   // { call, mode } as the brain confirmed it
  let wanted = null;      // the call we asked to listen to: its phone call is answered automatically
  const agents = new Map();
  const calls = new Map();
  let signingOut = false;
  let refreshTimer = null;

  class DeskError extends Error {
    constructor(code, message) { super(message); this.code = code; }
  }

  function notice(text) { $("notice").textContent = text || ""; }
  function chip(id, text, kind) { $(id).textContent = text; $(id).className = `chip ${kind || ""}`; }
  function clock(since) {
    if (!since) return "";
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - since));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }
  function button(text, onClick, kind = "secondary") {
    const element = document.createElement("button");
    element.textContent = text;
    element.className = kind;
    element.addEventListener("click", onClick);
    return element;
  }
  function cell(row, content) {
    const td = document.createElement("td");
    if (content instanceof Node) td.append(content); else td.textContent = content ?? "";
    row.append(td);
    return td;
  }

  // ── sign in and API ──────────────────────────────────────────────────────
  function keepTokens(reply) {
    tokens = { access: reply.access_token, refresh: reply.refresh_token, expiresAt: Date.now() + (reply.expires_in || 3600) * 1000 };
    store.set("algodial.supervisor.tokens", tokens);
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(refresh, Math.max(30000, tokens.expiresAt - Date.now() - 60000));
  }
  async function tokenRequest(fields) {
    const body = new URLSearchParams({ client_id: config.clientId, ...fields });
    return (await fetch(config.tokenUrl, { method: "POST", body, credentials: "omit" })).json();
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
      credentials: "omit",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${tokens.access}` },
      body: JSON.stringify({ query, variables }),
    });
    const body = await reply.json();
    if (body.errors && body.errors.length) throw new DeskError(0, body.errors[0].message);
    const answer = Object.values(body.data)[0];
    if (!answer.status) throw new DeskError(answer.code, answer.message);
    return answer.data;
  }
  async function command(query, variables) {
    notice("");
    try { await api(query, variables); } catch (error) { notice(error.message); }
  }

  async function signIn(event) {
    event.preventDefault();
    $("signin-error").textContent = "";
    const fields = { grant_type: "password", username: $("email").value.trim(), password: $("password").value };
    if ($("otp").value.trim()) fields.otp = $("otp").value.trim();
    const reply = await tokenRequest(fields);
    if (!reply.success) {
      if (reply.code === 8014) $("otp-row").hidden = false;
      $("signin-error").textContent = reply.message || reply.error_description || "Sign-in failed.";
      return;
    }
    keepTokens(reply);
    $("password").value = "";
    await openDesk();
  }

  // ── the desk ─────────────────────────────────────────────────────────────
  const DESK = `query { mySupervisorDesk { status code message data {
    user name can notReadyReasons calls
    agents { uid number name availability activity reason call idleSince }
    queues { name waiting longestWait serviceLevelPercent targetPercent targetSeconds offered answered } } } }`;

  async function openDesk() {
    try {
      desk = await api(DESK);
    } catch (error) {
      showSignIn(error.message);
      return;
    }
    $("signin").hidden = true;
    $("desk").hidden = false;
    $("me").textContent = desk.name;
    take(desk);
    connectLive();
    await startPhone();
  }

  function take(state) {
    desk = { ...desk, ...state, agents: undefined, calls: undefined };
    agents.clear();
    for (const agent of state.agents) agents.set(agent.uid, { ...agent, since: agent.idle_since ?? agent.idleSince });
    calls.clear();
    for (const call of state.calls) calls.set(call.call, call);
    render();
  }

  function render() {
    renderQueues();
    renderCalls();
    renderAgents();
    renderListening();
  }

  function renderQueues() {
    const body = $("queues");
    body.replaceChildren();
    for (const queue of desk.queues || []) {
      const row = document.createElement("tr");
      const waiting = [...calls.values()].filter((c) => c.queue === queue.name && (c.state === "waiting" || c.state === "offering"));
      const oldest = waiting.length ? Math.min(...waiting.map((c) => c.waiting_since)) : null;
      cell(row, queue.name);
      cell(row, String(waiting.length));
      cell(row, oldest ? clock(oldest) : "");
      const level = queue.serviceLevelPercent ?? queue.service_level_percent;
      const target = queue.targetPercent ?? queue.target_percent;
      const today = cell(row, level == null ? "no calls yet" : `${Math.round(level)}% in ${queue.targetSeconds ?? queue.target_seconds}s (target ${target}%)`);
      if (level != null) today.className = level >= target ? "good" : "bad";
      body.append(row);
    }
  }

  function agentName(uid) {
    const agent = agents.get(uid);
    return agent ? `${agent.number} ${agent.name}` : "";
  }

  function renderCalls() {
    const body = $("calls");
    body.replaceChildren();
    for (const call of calls.values()) {
      const row = document.createElement("tr");
      cell(row, `${call.caller_name || call.caller}${call.language ? ` (${call.language})` : ""}`);
      cell(row, call.queue);
      const extra = call.held ? " · on hold" : call.transfer ? ` · ${call.transfer.kind} transfer` : call.conference ? " · conference" : "";
      cell(row, (call.state === "ai" ? "with the AI" : call.state) + extra);
      cell(row, call.agent ? agentName(call.agent) : (call.ringing || []).map(agentName).join(", "));
      cell(row, clock(call.state === "connected" ? call.connected_at : call.waiting_since));
      const actions = document.createElement("div");
      actions.className = "row";
      if (call.state === "connected" && call.agent) {
        for (const mode of ["monitor", "whisper", "barge"]) {
          if (desk.can.includes(mode)) actions.append(button(mode[0].toUpperCase() + mode.slice(1), () => supervise(call.call, mode)));
        }
      }
      for (const watcher of call.supervisors || []) {
        const tag = document.createElement("span");
        tag.className = "muted";
        tag.textContent = watcher.supervisor === desk.user ? `you: ${watcher.mode}` : watcher.mode;
        actions.append(tag);
      }
      cell(row, actions);
      body.append(row);
    }
  }

  function renderAgents() {
    const body = $("agents");
    body.replaceChildren();
    for (const agent of agents.values()) {
      const row = document.createElement("tr");
      cell(row, `${agent.number} ${agent.name}`);
      const state = cell(row, `${agent.availability.replace("_", " ")}${agent.activity !== "idle" ? ` · ${agent.activity.replace("_", " ")}` : ""}${agent.reason ? ` (${agent.reason})` : ""}`);
      state.className = `state-${agent.availability}`;
      cell(row, agent.availability === "logged_out" ? "" : clock(agent.since));
      const actions = document.createElement("div");
      actions.className = "row";
      if (desk.can.includes("force") && agent.availability !== "logged_out") {
        if (agent.availability !== "ready") actions.append(button("Ready", () => force(agent.uid, "ready")));
        const reason = document.createElement("select");
        reason.setAttribute("aria-label", "Not Ready reason");
        for (const name of desk.notReadyReasons || desk.not_ready_reasons || []) reason.append(new Option(name, name));
        actions.append(reason, button("Not Ready", () => force(agent.uid, "not_ready", reason.value)));
        actions.append(button("Log out", () => force(agent.uid, "logged_out"), "danger"));
      }
      cell(row, actions);
      body.append(row);
    }
  }

  function renderListening() {
    $("listening").hidden = !listening;
    if (!listening) return;
    const call = calls.get(listening.call);
    $("listening-caller").textContent = call ? `${call.caller_name || call.caller} and ${agentName(call.agent)}` : "a call";
    $("listening-mode-help").textContent = MODE_HELP[listening.mode];
    for (const mode of ["monitor", "whisper", "barge"]) {
      const element = $(`mode-${mode}`);
      element.hidden = !desk.can.includes(mode);
      element.classList.toggle("active", listening.mode === mode);
    }
  }

  setInterval(() => { if (desk) { renderCalls(); renderAgents(); renderQueues(); } }, 1000);

  function supervise(call, mode) {
    wanted = call;
    command("mutation S($call: String!, $mode: String!) { superviseCall(call: $call, mode: $mode) { status code message } }", { call, mode });
  }
  function force(agent, state, reason = "") {
    command("mutation F($agent: String!, $state: String!, $reason: String) { forceAgentState(agent: $agent, state: $state, reason: $reason) { status code message } }", { agent, state, reason });
  }

  // ── live ─────────────────────────────────────────────────────────────────
  function connectLive() {
    if (live || !tokens) return;
    live = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${config.liveUrl}`);
    live.onopen = () => live.send(JSON.stringify({ type: "auth", token: tokens.access }));
    live.onmessage = (message) => {
      const data = JSON.parse(message.data);
      if (data.type === "hello") {
        liveRetry = 1000;
        chip("live-chip", "Live: on", "on");
        take(data.desk);  // a reconnect re-reads everything
      } else if (data.type === "call") {
        if (data.snapshot) calls.set(data.call, data.snapshot); else calls.delete(data.call);
        render();
      } else if (data.type === "event") {
        onEvent(data.event);
      }
    };
    live.onclose = async (event) => {
      live = null;
      chip("live-chip", "Live: off", "bad");
      if (signingOut || !tokens) return;
      if (event.code === 4401 && !(await refresh())) { showSignIn("Your session ended. Please sign in again."); return; }
      setTimeout(connectLive, liveRetry);
      liveRetry = Math.min(liveRetry * 2, 15000);
    };
  }

  function onEvent(event) {
    const data = event.data;
    if (event.type === "agent.state" && agents.has(data.agent)) {
      const before = agents.get(data.agent);
      const changed = before.availability !== data.availability || before.activity !== data.activity;
      agents.set(data.agent, { ...before, ...data, since: changed ? Date.now() / 1000 : before.since });
    } else if (event.type === "supervise.started" || event.type === "supervise.changed") {
      if (data.supervisor === desk.user) listening = { call: data.call, mode: data.mode };
    } else if (event.type === "supervise.stopped") {
      if (data.supervisor === desk.user) { listening = null; wanted = null; }
    } else if (event.type === "command.rejected") {
      notice(`Not done: ${data.reason}`);
    }
    render();
  }

  // ── the browser phone ────────────────────────────────────────────────────
  async function startPhone() {
    if (ua) return;
    let phone;
    try {
      phone = await api("mutation { startSupervisorSession { status code message data { sipId password wsUrl domain } } }");
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
      user_agent: "AlgoDial supervisor page",
    });
    ua.on("registered", () => chip("phone-chip", `Phone: ${phone.sipId}`, "on"));
    ua.on("registrationFailed", (event) => chip("phone-chip", `Phone: ${event.cause}`, "bad"));
    ua.on("disconnected", () => chip("phone-chip", "Phone: no connection", "bad"));
    ua.on("newRTCSession", ({ session: incoming, originator }) => {
      if (originator !== "remote") return;
      if (session || !wanted) {  // only the supervision we asked for is answered
        incoming.terminate({ status_code: 486, reason_phrase: "Busy Here" });
        return;
      }
      session = incoming;
      incoming.on("peerconnection", ({ peerconnection }) => {
        peerconnection.addEventListener("track", (track) => { $("remote-audio").srcObject = track.streams[0]; });
      });
      const gone = () => { session = null; $("remote-audio").srcObject = null; };
      incoming.on("ended", gone);
      incoming.on("failed", gone);
      incoming.answer({ mediaConstraints: { audio: true, video: false }, pcConfig: { iceServers: [] } });
    });
    ua.start();
  }

  // ── buttons ──────────────────────────────────────────────────────────────
  $("signin-form").addEventListener("submit", signIn);
  for (const mode of ["monitor", "whisper", "barge"]) {
    $(`mode-${mode}`).addEventListener("click", () => listening && supervise(listening.call, mode));
  }
  $("stop").addEventListener("click", () => command("mutation { stopSupervising { status code message } }"));
  $("signout").addEventListener("click", async () => {
    signingOut = true;
    try { await api("mutation { endSupervisorSession { status code message } }"); } catch { /* signing out anyway */ }
    if (tokens) fetch(config.revokeUrl, { method: "POST", body: new URLSearchParams({ token: tokens.access, client_id: config.clientId }), credentials: "omit" }).catch(() => {});
    tokens = null;
    store.drop("algodial.supervisor.tokens");
    showSignIn("Signed out.");
    signingOut = false;
  });

  function showSignIn(message) {
    clearTimeout(refreshTimer);
    if (ua) { ua.stop(); ua = null; }
    if (live) { live.onclose = null; live.close(); live = null; }
    $("desk").hidden = true;
    $("signin").hidden = false;
    $("signin-error").textContent = message || "";
  }

  window.addEventListener("beforeunload", () => { if (ua) ua.stop(); });
  window.supervisorDesk = Object.freeze({
    get listening() { return listening; },
    get session() { return session; },
    get calls() { return [...calls.values()]; },
    get agents() { return [...agents.values()]; },
  });

  if (tokens) openDesk(); else showSignIn("");
})();
