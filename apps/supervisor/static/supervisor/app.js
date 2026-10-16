/* AlgoDial supervisor page: your teams live (agents, calls, queues), monitor / whisper / barge, force agent state.
 *
 * Like the agent page: the buttons ask the API, the API asks the brain, the answers arrive on the live
 * WebSocket. The browser phone rings once when you start listening and stays on while you switch mode.
 */
(() => {
  "use strict";

  const config = JSON.parse(document.getElementById("desk-config").textContent);
  const $ = (id) => document.getElementById(id);
  // Labels come from translations/<language>.json, switched in place (never a reload: a supervised
  // call goes on). The choice is shared with the agent page on this browser.
  const LANGUAGES = config.messages;
  let language = "en";
  try {
    const saved = localStorage.getItem("algodial.language");
    if (LANGUAGES[saved]) language = saved;
  } catch {
    /* no storage: English */
  }
  const t = (key, values = {}) =>
    Object.entries(values).reduce(
      (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
      LANGUAGES[language]?.[key] || LANGUAGES.en[key] || key,
    );
  const ui = window.AlgoDialUI;
  function translatePage() {
    document.documentElement.lang = language;
    document.querySelectorAll("[data-i18n]").forEach((node) => {
      node.textContent = t(node.dataset.i18n);
    });
    for (const attribute of ["aria-label", "title"]) {
      const keep = `i18n${attribute === "title" ? "Title" : "Aria"}`;
      document.querySelectorAll(`[${attribute}]`).forEach((node) => {
        if (node.dataset[keep] === undefined) node.dataset[keep] = node.getAttribute(attribute);
        if (node.dataset[keep]) node.setAttribute(attribute, t(node.dataset[keep]));
      });
    }
    document.querySelectorAll("[data-language]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.language === language));
      button.classList.toggle("selected", button.dataset.language === language);
    });
  }
  translatePage();
  document.querySelectorAll("[data-language]").forEach((button) =>
    button.addEventListener("click", () => {
      language = button.dataset.language;
      try {
        localStorage.setItem("algodial.language", language);
      } catch {
        /* the choice lasts this page only */
      }
      translatePage();
      if (typeof render === "function") render();
    }),
  );
  let phoneReady = false,
    liveReady = false,
    selectedTeam = "",
    actionAgent = null,
    retryTimer = null,
    deskEpoch = 0,
    refreshPending = null,
    revision = 0;
  const age = (since) => (since ? Math.max(0, Date.now() / 1000 - since) : 0);
  function timeNode(since) {
    const node = document.createElement("time");
    if (since) node.dataset.since = since;
    node.textContent = since ? ui.duration(age(since)) : "—";
    return node;
  }

  const store = {
    get(key) {
      try {
        return JSON.parse(sessionStorage.getItem(key));
      } catch {
        return null;
      }
    },
    set(key, value) {
      try {
        sessionStorage.setItem(key, JSON.stringify(value));
      } catch {
        /* private mode */
      }
    },
    drop(key) {
      try {
        sessionStorage.removeItem(key);
      } catch {
        /* private mode */
      }
    },
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
  let listening = null; // { call, mode } as the brain confirmed it
  let wanted = null; // the call we asked to listen to: its phone call is answered automatically
  const agents = new Map();
  const calls = new Map();
  let signingOut = false;
  let refreshTimer = null;
  let refreshFlight = null;

  class DeskError extends Error {
    constructor(code, message) {
      super(message);
      this.code = code;
    }
  }

  function notice(text) {
    $("notice").textContent = text || "";
    $("notice").hidden = !text;
  }
  function chip(id, key, kind) {
    // Keeps the English key, so a language switch relabels the chip.
    $(id).dataset.i18n = key;
    $(id).textContent = t(key);
    $(id).className = `chip ${kind || ""}`;
  }
  function clock(since) {
    if (!since) return "";
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - since));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
  }
  function button(text, onClick, kind = "secondary") {
    const element = document.createElement("button");
    element.textContent = t(text);
    element.className = kind;
    element.addEventListener("click", onClick);
    return element;
  }
  function cell(row, content) {
    const td = document.createElement("td");
    if (content instanceof Node) td.append(content);
    else td.textContent = content ?? "";
    row.append(td);
    return td;
  }

  // ── sign in and API ──────────────────────────────────────────────────────
  function keepTokens(reply) {
    tokens = {
      access: reply.access_token,
      refresh: reply.refresh_token,
      expiresAt: Date.now() + (reply.expires_in || 3600) * 1000,
    };
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
    if (refreshFlight) return refreshFlight;
    const previous = tokens,
      epoch = deskEpoch;
    refreshFlight = (async () => {
      try {
        const reply = await tokenRequest({ grant_type: "refresh_token", refresh_token: previous.refresh });
        if (!reply.success || tokens !== previous || epoch !== deskEpoch) return false;
        keepTokens(reply);
        return true;
      } catch {
        return false;
      } finally {
        refreshFlight = null;
      }
    })();
    return refreshFlight;
  }
  async function api(query, variables = {}) {
    if (!tokens) throw new DeskError(0, "Not signed in.");
    const epoch = deskEpoch;
    if (Date.now() > tokens.expiresAt - 30000 && !(await refresh())) {
      if (epoch !== deskEpoch) throw new DeskError(0, "Session changed.");
      showSignIn("Your session ended. Please sign in again.");
      throw new DeskError(0, "Session ended.");
    }
    if (epoch !== deskEpoch || !tokens) throw new DeskError(0, "Session changed.");
    const access = tokens.access;
    const reply = await fetch(config.graphqlUrl, {
      method: "POST",
      credentials: "omit",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${access}` },
      body: JSON.stringify({ query, variables }),
    });
    const body = await reply.json();
    if (epoch !== deskEpoch || !tokens) throw new DeskError(0, "Session changed.");
    if (body.errors && body.errors.length) throw new DeskError(0, body.errors[0].message);
    const answer = Object.values(body.data)[0];
    if (!answer.status) throw new DeskError(answer.code, answer.message);
    return answer.data ?? answer.dataList;
  }
  async function command(query, variables) {
    notice("");
    $("update-status").textContent = t("Waiting for confirmation…");
    try {
      await api(query, variables);
      return true;
    } catch (error) {
      notice(error.message);
      $("update-status").textContent = "";
      return false;
    }
  }

  async function signIn(event) {
    event.preventDefault();
    const submit = $("signin-form").querySelector("button[type=submit]");
    if (submit.disabled) return;
    submit.disabled = true;
    $("signin-error").textContent = "";
    const fields = {
      grant_type: "password",
      username: $("email").value.trim(),
      password: $("password").value,
    };
    if ($("otp").value.trim()) fields.otp = $("otp").value.trim();
    try {
      const reply = await tokenRequest(fields);
      if (!reply.success) {
        if (reply.code === 8014) $("otp-row").hidden = false;
        $("signin-error").textContent = reply.message || reply.error_description || "Sign-in failed.";
        return;
      }
      keepTokens(reply);
      $("password").value = "";
      await openDesk();
    } catch {
      $("signin-error").textContent = t("Could not connect. Please try again.");
    } finally {
      submit.disabled = false;
    }
  }

  // ── the desk ─────────────────────────────────────────────────────────────
  const DESK = `query { mySupervisorDesk { status code message data {
    user name can canManage teams reasonLimits longCallSeconds notReadyReasons calls
    agents { uid number name availability activity reason call idleSince stateSince queues }
    queues { name waiting longestWait serviceLevelPercent targetPercent targetSeconds offered answered } } } }`;

  async function openDesk() {
    const epoch = deskEpoch;
    try {
      desk = await api(DESK);
    } catch (error) {
      if (epoch === deskEpoch) showSignIn(error.message);
      return;
    }
    if (epoch !== deskEpoch) return;
    $("signin").hidden = true;
    $("desk").hidden = false;
    $("me").textContent = desk.name;
    $("management-link").hidden = !desk.canManage;
    const filter = $("team-filter");
    filter.replaceChildren(new Option(t("All my teams"), ""));
    for (const team of desk.teams || []) filter.append(new Option(team.name, team.uid));
    filter.value = selectedTeam;
    $("agent-reason").replaceChildren(new Option(t("Choose a reason"), ""));
    for (const reason of desk.notReadyReasons || []) $("agent-reason").append(new Option(reason, reason));
    take(desk);
    connectLive();
    await startPhone();
  }

  function take(state) {
    desk = {
      ...desk,
      ...state,
      agents: undefined,
      calls: undefined,
      reasonLimits: state.reasonLimits ?? state.reason_limits,
      longCallSeconds: state.longCallSeconds ?? state.long_call_seconds,
      canManage: state.canManage ?? state.can_manage,
      notReadyReasons: state.notReadyReasons ?? state.not_ready_reasons,
    };
    agents.clear();
    for (const agent of state.agents)
      agents.set(agent.uid, { ...agent, since: agent.state_since ?? agent.stateSince ?? null });
    calls.clear();
    for (const call of state.calls) calls.set(call.call, call);
    render();
  }

  function render() {
    if (!desk) return;
    renderQueues();
    renderCalls();
    renderAgents();
    renderListening();
    renderMeasures();
  }

  // ── "How my team is doing" (UX_PRINCIPLES.md section 6, for your own teams) ──
  const MEASURES = `query M($period: String, $team: String) { myTeamMeasures(period: $period, team: $team) {
    status code message data { until team { answered missed medianAnswerSeconds wrapups medianWrapupSeconds
      corrections transfers transfersCompleted summariesShown summariesWrong overLimit actedOn endedByAgent
      medianMinutesToAction }
    agents { uid number name answered missed medianAnswerSeconds medianWrapupSeconds transfers transfersCompleted } } } }`;
  let view = "live",
    measurePeriod = "today",
    measures = null;
  const seconds = (value) => (value == null ? "—" : t("{n} s", { n: value.toFixed(1) }));
  function showView(next) {
    view = next;
    for (const [tab, panel, name] of [
      ["tab-live", "live-view", "live"],
      ["tab-measures", "measures", "measures"],
    ]) {
      $(tab).setAttribute("aria-selected", String(view === name));
      $(tab).tabIndex = view === name ? 0 : -1;
      $(panel).hidden = view !== name;
    }
    $("team-summary").hidden = view !== "live"; // "4 agents / sorted by attention" describes the live view
    if (view === "measures") loadMeasures();
  }
  async function loadMeasures() {
    if (!tokens || view !== "measures") return;
    try {
      measures = await api(MEASURES, { period: measurePeriod, team: selectedTeam || null });
      $("measures-note").textContent = t("Updated {time}", {
        time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }),
      });
    } catch {
      $("measures-note").textContent = t("Could not load the measures. They load again in a minute.");
    }
    renderMeasures();
  }
  function card(label, value, detail) {
    const box = document.createElement("div");
    box.className = "measure-card";
    const name = document.createElement("span"),
      big = document.createElement("strong"),
      small = document.createElement("small");
    name.textContent = t(label);
    big.textContent = value;
    big.className = "ui-number";
    small.textContent = detail || "";
    box.append(name, big, small);
    return box;
  }
  function renderMeasures() {
    if (!measures) return;
    const team = measures.team;
    const offers = team.answered + team.missed;
    $("measure-cards").replaceChildren(
      card(
        "Calls answered",
        String(team.answered),
        t("{missed} missed ({rate} per 100 offers)", {
          missed: team.missed,
          rate: offers ? Math.round((100 * team.missed) / offers) : 0,
        }),
      ),
      card("Median time to answer", seconds(team.medianAnswerSeconds), ""),
      card(
        "Median wrap-up",
        seconds(team.medianWrapupSeconds),
        t(team.corrections === 1 ? "1 code corrected" : "{count} codes corrected", {
          count: team.corrections,
        }),
      ),
      card(
        "Transfers completed",
        t("{done} of {all}", { done: team.transfersCompleted, all: team.transfers }),
        "",
      ),
      card(
        "AI summaries flagged wrong",
        t("{done} of {all}", { done: team.summariesWrong, all: team.summariesShown }),
        "",
      ),
      card(
        "Long breaks acted on",
        t("{done} of {all}", { done: team.actedOn, all: team.overLimit }),
        team.medianMinutesToAction == null
          ? ""
          : t("Median {minutes} min to act", { minutes: team.medianMinutesToAction.toFixed(1) }),
      ),
    );
    const rows = measures.agents.map((agent) => {
      const row = document.createElement("tr");
      cell(row, `${agent.name} ${agent.number}`);
      cell(row, String(agent.answered));
      cell(row, String(agent.missed));
      cell(row, seconds(agent.medianAnswerSeconds));
      cell(row, seconds(agent.medianWrapupSeconds));
      cell(row, t("{done} of {all}", { done: agent.transfersCompleted, all: agent.transfers }));
      return row;
    });
    $("measure-agents").replaceChildren(...rows);
  }
  $("tab-live").addEventListener("click", () => showView("live"));
  $("tab-measures").addEventListener("click", () => showView("measures"));
  for (const tab of ["tab-live", "tab-measures"])
    $(tab).addEventListener("keydown", (event) => {
      if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
      const next = view === "live" ? "measures" : "live";
      showView(next);
      $(next === "live" ? "tab-live" : "tab-measures").focus();
    });
  document.querySelectorAll("[data-period]").forEach((choice) =>
    choice.addEventListener("click", () => {
      measurePeriod = choice.dataset.period;
      document
        .querySelectorAll("[data-period]")
        .forEach((other) => other.setAttribute("aria-pressed", String(other === choice)));
      loadMeasures();
    }),
  );
  setInterval(loadMeasures, 60000);

  function activeAgents() {
    const team = (desk.teams || []).find((team) => team.uid === selectedTeam);
    return [...agents.values()].filter((agent) => !team || team.agents.includes(agent.uid));
  }
  function activeQueues() {
    const team = (desk.teams || []).find((team) => team.uid === selectedTeam);
    return (desk.queues || []).filter((queue) => !team || team.queues.includes(queue.name));
  }
  function activeCalls() {
    const names = new Set(activeQueues().map((queue) => queue.name)),
      uids = new Set(activeAgents().map((agent) => agent.uid));
    return [...calls.values()].filter(
      (call) =>
        !selectedTeam ||
        names.has(call.queue) ||
        uids.has(call.agent) ||
        (call.ringing || []).some((uid) => uids.has(uid)),
    );
  }
  function renderQueues() {
    const body = $("queues");
    body.replaceChildren();
    let total = 0,
      longest = 0,
      offered = 0,
      within = 0;
    for (const queue of activeQueues()) {
      const waiting = activeCalls().filter(
        (call) => call.queue === queue.name && ["waiting", "offering"].includes(call.state),
      );
      const oldest = waiting.length
        ? Math.min(...waiting.map((call) => call.queue_entered_at || call.waiting_since || Date.now() / 1000))
        : null;
      const seconds = age(oldest),
        target = queue.targetSeconds ?? queue.target_seconds;
      total += waiting.length;
      longest = Math.max(longest, seconds);
      const level = queue.serviceLevelPercent ?? queue.service_level_percent,
        count = queue.offered || 0;
      offered += count;
      within += ((level || 0) * count) / 100;
      const section = document.createElement("section"),
        heading = document.createElement("h2"),
        metrics = document.createElement("p"),
        targetLine = document.createElement("p"),
        counts = document.createElement("p");
      heading.textContent = queue.name;
      metrics.className = "queue-metrics";
      const load = document.createElement("span"),
        wait = document.createElement("span");
      load.textContent = t("{count} waiting", { count: waiting.length });
      wait.append(document.createTextNode(t("Longest ")), timeNode(oldest));
      metrics.append(load, wait);
      targetLine.className = "queue-target";
      if (waiting.length && seconds > target) {
        targetLine.classList.add("over");
        targetLine.append(ui.icon("warning"));
        targetLine.append(
          document.createTextNode(t("Over target / answer within {seconds} s", { seconds: target })),
        );
      } else
        targetLine.textContent = t("Target: {percent}% answered within {seconds} s", {
          percent: queue.targetPercent ?? queue.target_percent,
          seconds: target,
        });
      counts.className = "queue-agents";
      const eligible = activeAgents().filter((agent) => (agent.queues || []).includes(queue.name));
      const countState = (availability, activity) =>
        eligible.filter(
          (agent) =>
            (!availability || agent.availability === availability) &&
            (!activity || agent.activity === activity),
        ).length;
      counts.textContent = t(
        "Ready {ready} / Ringing {ringing} / Talking {talking} / Not ready {notReady} / Wrap-up {wrapup}",
        {
          ready: countState("ready", "idle"),
          talking: countState(null, "talking") + countState(null, "on_hold"),
          notReady: eligible.filter(
            (agent) =>
              agent.availability === "not_ready" && ["idle", "not_responding"].includes(agent.activity),
          ).length,
          wrapup: countState(null, "wrapup"),
          ringing: countState(null, "offered"), // Finesse's "reserved": a call is ringing for them
        },
      );
      section.append(heading, metrics, targetLine, counts);
      body.append(section);
    }
    $("service-level").textContent = offered ? `${Math.round((100 * within) / offered)}%` : "—";
    $("waiting-count").textContent = total;
    $("longest-wait").textContent = ui.duration(longest);
  }

  function agentName(uid) {
    const agent = agents.get(uid);
    return agent ? `${agent.number} ${agent.name}` : "";
  }

  function renderCalls() {
    const body = $("calls");
    body.replaceChildren();
    for (const call of activeCalls()) {
      const row = document.createElement("tr");
      cell(row, `${call.caller_name || call.caller}${call.language ? ` (${call.language})` : ""}`);
      cell(row, call.queue);
      const extra = call.held
        ? " / on hold"
        : call.transfer
          ? ` / ${call.transfer.kind} transfer`
          : call.conference
            ? " / conference"
            : "";
      cell(row, (call.state === "ai" ? "with the AI" : call.state) + extra);
      cell(row, call.agent ? agentName(call.agent) : (call.ringing || []).map(agentName).join(", "));
      cell(row, clock(call.state === "connected" ? call.connected_at : call.waiting_since));
      const actions = document.createElement("div");
      actions.className = "row";
      if (call.state === "connected" && call.agent) {
        for (const mode of ["monitor", "whisper", "barge"]) {
          if (desk.can.includes(mode))
            actions.append(button(mode[0].toUpperCase() + mode.slice(1), () => supervise(call.call, mode)));
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

  function attention(agent) {
    const limit = (desk.reasonLimits || desk.reason_limits || {})[agent.reason] || 0;
    const elapsed = age(agent.since),
      call = calls.get(agent.call);
    if (
      agent.availability === "not_ready" &&
      ["idle", "not_responding"].includes(agent.activity) &&
      limit &&
      elapsed > limit
    )
      return { rank: 0, reason: t("Over {minutes}-minute limit", { minutes: Math.round(limit / 60) }) };
    if (
      ["talking", "on_hold"].includes(agent.activity) &&
      (desk.longCallSeconds || desk.long_call_seconds) &&
      age(call?.connected_at) > (desk.longCallSeconds || desk.long_call_seconds)
    )
      return {
        rank: 1,
        reason: t("Over {minutes}-minute call threshold", {
          minutes: Math.round((desk.longCallSeconds || desk.long_call_seconds) / 60),
        }),
      };
    if (agent.activity === "not_responding") return { rank: 2, reason: t("Missed a call") };
    return { rank: 3, reason: "" };
  }
  function renderAgents() {
    const body = $("agents");
    const focused = body.contains(document.activeElement)
      ? {
          agent: document.activeElement.closest("tr")?.dataset.agent,
          action: document.activeElement.textContent,
        }
      : null;
    body.replaceChildren();
    const rows = activeAgents().sort(
      (a, b) =>
        attention(a).rank - attention(b).rank ||
        a.activity.localeCompare(b.activity) ||
        a.name.localeCompare(b.name),
    );
    $("team-summary").textContent = t("{count} agents / sorted by attention needed", { count: rows.length });
    $("no-agents").hidden = Boolean(rows.length);
    for (const agent of rows) {
      const row = document.createElement("tr");
      row.dataset.agent = agent.uid;
      cell(row, agent.name);
      const stateCell = document.createElement("div"),
        label = document.createElement("span");
      let kind =
        agent.availability === "logged_out"
          ? "logged_out"
          : agent.activity === "idle"
            ? agent.availability
            : agent.activity;
      const words = {
        ready: "Ready",
        not_ready: "Not ready",
        logged_out: "Logged out",
        offered: "Incoming call",
        talking: "On a call",
        on_hold: "On hold",
        wrapup: "Wrap-up",
        not_responding: "Not responding",
      };
      label.className = `state-label state-${kind}`;
      label.append(
        ui.icon(
          {
            ready: "check",
            not_ready: "minus",
            logged_out: "plug",
            offered: "ring",
            talking: "phone",
            on_hold: "pause",
            wrapup: "clipboard",
            not_responding: "minus",
          }[kind],
        ),
        document.createTextNode(
          t(words[kind] || kind) +
            (agent.reason && agent.availability === "not_ready" ? `: ${agent.reason}` : ""),
        ),
      );
      stateCell.append(label);
      const important = attention(agent);
      row.dataset.attention = important.rank;
      if (important.reason) {
        const reason = document.createElement("small");
        reason.textContent = important.reason;
        stateCell.append(reason);
      }
      cell(row, stateCell);
      cell(row, timeNode(agent.availability === "logged_out" ? null : agent.since));
      const call = calls.get(agent.call),
        current = document.createElement("div");
      if (call) {
        current.append(
          document.createTextNode(
            `${call.queue || t("Direct call")} / ${call.language || t("Language unknown")}`,
          ),
        );
        if (call.connected_at) {
          const duration = document.createElement("small");
          duration.append(document.createTextNode(t("Duration ")), timeNode(call.connected_at));
          current.append(duration);
        }
      } else current.textContent = "—";
      cell(row, current);
      const actions = document.createElement("div");
      actions.className = "row-actions";
      if (call?.state === "connected")
        for (const mode of ["monitor", "whisper", "barge"])
          if (desk.can.includes(mode)) {
            const action = button({ monitor: "Monitor", whisper: "Whisper", barge: "Barge" }[mode], () =>
              supervise(call.call, mode),
            );
            action.disabled = !phoneReady || !liveReady;
            actions.append(action);
          }
      if (desk.can.includes("force") && agent.availability !== "logged_out")
        actions.append(button("Change state", () => openAgentAction(agent)));
      actions.append(button("View history", () => showHistory(agent)));

      cell(row, actions);
      body.append(row);
    }
    if (focused) {
      const row = [...body.rows].find((row) => row.dataset.agent === focused.agent);
      const actions = [...(row?.querySelectorAll("button") || [])];
      const next =
        actions.find((action) => action.textContent === focused.action && !action.disabled) ||
        actions.find((action) => !action.disabled);
      next?.focus({ preventScroll: true });
    }
  }
  function openAgentAction(agent, state = "not_ready") {
    actionAgent = agent;
    $("agent-action-title").textContent = t("Change {name}’s state", { name: agent.name });
    $("agent-next-state").value = state;
    $("agent-action-error").textContent = "";
    toggleReason();
    $("agent-action").showModal();
  }
  function toggleReason() {
    const hidden = $("agent-next-state").value !== "not_ready";
    $("agent-reason").hidden = $("action-reason-label").hidden = hidden;
  }
  async function showHistory(agent) {
    $("history-title").textContent = t("{name}’s history", { name: agent.name });
    $("agent-history").replaceChildren();
    $("history-dialog").showModal();
    const epoch = deskEpoch;
    try {
      const rows = await api(
        "query H($agent: String!) { supervisedAgentHistory(agent: $agent) { status code message dataList { at type description } } }",
        { agent: agent.uid },
      );
      if (epoch !== deskEpoch) return;
      for (const item of rows || []) {
        const li = document.createElement("li"),
          time = document.createElement("time");
        time.textContent = new Date(item.at).toLocaleString();
        li.append(time, document.createTextNode(item.description));
        $("agent-history").append(li);
      }
      if (!rows?.length) {
        const li = document.createElement("li");
        li.textContent = t("No recorded events yet.");
        $("agent-history").append(li);
      }
    } catch (error) {
      notice(error.message);
      $("history-dialog").close();
    }
  }

  function renderListening() {
    $("listening").hidden = !listening;
    if (!listening) return;
    const call = calls.get(listening.call);
    $("listening-caller").textContent = call
      ? t("Monitoring {name}", { name: agents.get(call.agent)?.name || t("a call") })
      : t("Supervising a call");
    $("listening-mode-help").textContent = t(MODE_HELP[listening.mode]);
    for (const mode of ["monitor", "whisper", "barge"]) {
      const element = $(`mode-${mode}`);
      element.hidden = !desk.can.includes(mode);
      element.classList.toggle("active", listening.mode === mode);
    }
  }

  setInterval(() => {
    if (!desk) return;
    document.querySelectorAll("time[data-since]").forEach((node) => {
      node.textContent = ui.duration(age(Number(node.dataset.since)));
    });
    renderQueues();
    if (
      activeAgents().some(
        (agent) =>
          String(attention(agent).rank) !==
          document.querySelector(`[data-agent="${agent.uid}"]`)?.dataset.attention,
      )
    )
      renderAgents();
  }, 1000);
  $("team-filter").addEventListener("change", () => {
    selectedTeam = $("team-filter").value;
    render();
    loadMeasures();
  });
  $("agent-next-state").addEventListener("change", toggleReason);
  $("agent-action-cancel").addEventListener("click", () => $("agent-action").close());
  $("history-close").addEventListener("click", () => $("history-dialog").close());
  $("agent-action-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const next = $("agent-next-state").value,
      reason = $("agent-reason").value;
    if (next === "not_ready" && !reason) {
      $("agent-action-error").textContent = t("Choose a reason before going Not Ready.");
      $("agent-reason").focus();
      return;
    }
    if (await force(actionAgent.uid, next, reason)) $("agent-action").close();
  });

  function supervise(call, mode) {
    if (!phoneReady || !liveReady) {
      notice(t("Reconnect your phone and live updates before supervising a call."));
      return;
    }
    wanted = call;
    command(
      "mutation S($call: String!, $mode: String!) { superviseCall(call: $call, mode: $mode) { status code message } }",
      { call, mode },
    );
  }
  function force(agent, state, reason = "") {
    return command(
      "mutation F($agent: String!, $state: String!, $reason: String) { forceAgentState(agent: $agent, state: $state, reason: $reason) { status code message } }",
      { agent, state, reason },
    );
  }

  // ── live ─────────────────────────────────────────────────────────────────
  function connectLive() {
    if (live || !tokens) return;
    live = new WebSocket(
      `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${config.liveUrl}`,
    );
    live.onopen = () => live.send(JSON.stringify({ type: "auth", token: tokens.access }));
    live.onmessage = (message) => {
      const data = JSON.parse(message.data);
      if (data.type === "hello") {
        liveRetry = 1000;
        liveReady = true;
        chip("live-chip", "Live: on", "on");
        take(data.desk); // a reconnect re-reads everything
      } else if (data.type === "call") {
        revision++;
        if (data.snapshot) calls.set(data.call, data.snapshot);
        else calls.delete(data.call);
        render();
      } else if (data.type === "event") {
        revision++;
        onEvent(data.event);
      }
    };
    live.onclose = async (event) => {
      live = null;
      liveReady = false;
      render();
      chip("live-chip", "Live: off", "bad");
      if (signingOut || !tokens) return;
      if (event.code === 4401 && !(await refresh())) {
        showSignIn("Your session ended. Please sign in again.");
        return;
      }
      retryTimer = setTimeout(connectLive, liveRetry);
      liveRetry = Math.min(liveRetry * 2, 15000);
    };
  }

  function onEvent(event) {
    $("update-status").textContent = "";
    const data = event.data;
    if (event.type === "agent.state" && agents.has(data.agent)) {
      const before = agents.get(data.agent);
      const changed =
        before.availability !== data.availability ||
        before.activity !== data.activity ||
        before.reason !== data.reason;
      agents.set(data.agent, {
        ...before,
        ...data,
        since: changed ? Date.parse(event.at) / 1000 || Date.now() / 1000 : before.since,
      });
    } else if (event.type === "supervise.started" || event.type === "supervise.changed") {
      if (data.supervisor === desk.user) listening = { call: data.call, mode: data.mode };
    } else if (event.type === "supervise.stopped") {
      if (data.supervisor === desk.user) {
        listening = null;
        wanted = null;
      }
    } else if (event.type === "command.rejected") {
      notice(`Not done: ${data.reason}`);
    }
    render();
    if (["call.ended", "agent.state", "agent.wrapped_up"].includes(event.type)) refreshDesk();
  }
  async function refreshDesk() {
    if (refreshPending || !tokens) return;
    const epoch = deskEpoch,
      version = revision;
    refreshPending = (async () => {
      try {
        const data = await api(DESK);
        if (epoch === deskEpoch && version === revision && tokens) take(data);
      } catch {
        /* live state stays visible; the socket resynchronizes on reconnect */
      } finally {
        refreshPending = null;
      }
    })();
    await refreshPending;
  }

  // ── the browser phone ────────────────────────────────────────────────────
  async function startPhone() {
    if (ua) return;
    let phone;
    try {
      phone = await api(
        "mutation { startSupervisorSession { status code message data { sipId password wsUrl domain } } }",
      );
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
    ua.on("registered", () => {
      phoneReady = true;
      chip("phone-chip", "Phone connected", "on"); // plain words; the account is for support
      $("phone-chip").dataset.sipId = phone.sipId;
      $("phone-chip").title = `Phone: ${phone.sipId}`;
      $("reconnect").hidden = true;
      render();
    });
    ua.on("registrationFailed", () => phoneFailed());
    ua.on("disconnected", () => phoneFailed());
    ua.on("newRTCSession", ({ session: incoming, originator }) => {
      if (originator !== "remote") return;
      if (session || !wanted) {
        // only the supervision we asked for is answered
        incoming.terminate({ status_code: 486, reason_phrase: "Busy Here" });
        return;
      }
      session = incoming;
      incoming.on("peerconnection", ({ peerconnection }) => {
        peerconnection.addEventListener("track", (track) => {
          $("remote-audio").srcObject = track.streams[0];
        });
      });
      const gone = () => {
        session = null;
        $("remote-audio").srcObject = null;
      };
      incoming.on("ended", gone);
      incoming.on("failed", gone);
      incoming.answer({ mediaConstraints: { audio: true, video: false }, pcConfig: { iceServers: [] } });
    });
    ua.start();
  }

  function phoneFailed() {
    phoneReady = false;
    chip("phone-chip", "Phone not connected", "bad");
    $("reconnect").hidden = false;
    render();
  }
  $("reconnect").addEventListener("click", async () => {
    if (ua) {
      ua.stop();
      ua = null;
    }
    await startPhone();
  });

  // ── buttons ──────────────────────────────────────────────────────────────
  $("signin-form").addEventListener("submit", signIn);
  for (const mode of ["monitor", "whisper", "barge"]) {
    $(`mode-${mode}`).addEventListener("click", () => listening && supervise(listening.call, mode));
  }
  $("stop").addEventListener("click", () => command("mutation { stopSupervising { status code message } }"));
  $("signout").addEventListener("click", async () => {
    signingOut = true;
    try {
      await api("mutation { endSupervisorSession { status code message } }");
    } catch {
      /* signing out anyway */
    }
    if (tokens)
      fetch(config.revokeUrl, {
        method: "POST",
        body: new URLSearchParams({ token: tokens.access, client_id: config.clientId }),
        credentials: "omit",
      }).catch(() => {});
    tokens = null;
    store.drop("algodial.supervisor.tokens");
    showSignIn("Signed out.");
    signingOut = false;
  });

  function showSignIn(message) {
    tokens = null;
    store.drop("algodial.supervisor.tokens");
    deskEpoch++;
    clearTimeout(refreshTimer);
    clearTimeout(retryTimer);
    phoneReady = liveReady = false;
    listening = wanted = null;
    desk = null;
    agents.clear();
    calls.clear();
    $("agent-action").close();
    $("history-dialog").close();
    if (ua) {
      ua.stop();
      ua = null;
    }
    if (live) {
      live.onclose = null;
      live.close();
      live = null;
    }
    $("desk").hidden = true;
    $("signin").hidden = false;
    $("signin-error").textContent = message || "";
  }

  window.addEventListener("beforeunload", () => {
    if (ua) ua.stop();
  });
  window.supervisorDesk = Object.freeze({
    get listening() {
      return listening;
    },
    get session() {
      return session;
    },
    get calls() {
      return [...calls.values()];
    },
    get agents() {
      return [...agents.values()];
    },
  });

  if (tokens) openDesk();
  else showSignIn("");
})();
