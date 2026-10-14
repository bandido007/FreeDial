/* Approved agent workspace. The brain owns availability and call state;
 * SIP owns browser media. UI preferences and per-call draft notes are local. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const config = JSON.parse($("desk-config").textContent);
  const t = (key, values = {}) =>
    Object.entries(values).reduce(
      (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
      config.messages[key] || key,
    );
  document.querySelectorAll("[data-i18n]").forEach((node) => {
    node.textContent = t(node.dataset.i18n);
  });
  document.querySelectorAll("[data-placeholder]").forEach((node) => {
    node.placeholder = t(node.dataset.placeholder);
  });
  for (const attribute of ["aria-label", "title"])
    document
      .querySelectorAll(`[${attribute}]`)
      .forEach((node) => node.setAttribute(attribute, t(node.getAttribute(attribute))));
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
  let tokens = store.get("algodial.tokens"),
    desk = null,
    state = null;
  let ua = null,
    session = null,
    live = null,
    offer = null,
    transfer = null;
  let since = Date.now(),
    connectedAt = null,
    callEndedAt = null,
    offeredAt = null,
    draftCall = null;
  let phoneReady = false,
    micReady = false,
    phoneProblem = "",
    liveReady = false;
  let signingOut = false,
    stopping = false,
    liveRetry = 1000,
    refreshing = null,
    snapshotPromise = null;
  let refreshTimer = null,
    liveTimer = null,
    snapshotTimer = null,
    ringer = null;
  let wrapPending = false,
    lastConnected = null,
    stateVersion = 0,
    deskEpoch = 0;
  const stamp = (value) => (typeof value === "number" ? value * 1000 : Date.parse(value));
  const duration = (seconds) => {
    const n = Math.max(0, Math.floor(Number(seconds) || 0));
    return `${String(Math.floor(n / 60)).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}`;
  };
  const icon = (name) => {
    $("state-icon").setAttribute("href", `#i-${name}`);
    $("empty-icon").setAttribute("href", `#i-${name}`);
  };
  const AI_REASONS = {
    asked_for_person: "Caller asked for a person",
    ai_decided: "Handed over by the AI",
    failed_turns: "The AI did not understand",
    sensitive_topic: "Sensitive topic",
  };
  function targetName(current) {
    // An agent transfer carries the colleague's uid; the page already lists colleagues by name.
    const colleague = desk?.transferTargets?.agents?.find((agent) => agent.uid === current.target);
    return colleague ? colleague.name : current.target || t("the other party");
  }
  function clockTime(stamp) {
    return new Date(stamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  const SEARCH_FROM = 10; // wrap-up codes before a search box helps (a short list is quicker as a select)
  const STATE_NAMES = {
    offered: "Incoming call",
    talking: "On a call",
    on_hold: "On hold",
    wrapup: "Wrap-up",
    not_responding: "Not responding",
  };
  class DeskError extends Error {
    constructor(code, message) {
      super(message);
      this.code = code;
    }
  }
  function note(text) {
    const item = document.createElement("li"),
      time = document.createElement("time");
    time.textContent = new Date().toLocaleTimeString();
    item.append(time, document.createTextNode(text));
    $("log").prepend(item);
    while ($("log").children.length > 40) $("log").lastChild.remove();
  }
  function notice(text = "") {
    $("notice").textContent = text;
    $("notice").hidden = !text;
  }
  function feedback(text = "") {
    $("action-feedback").textContent = text;
  }
  function theme(value) {
    document.documentElement.dataset.theme = value;
    store.set("algodial.theme", value);
  }
  const systemTheme = matchMedia("(prefers-color-scheme: dark)");
  document.documentElement.dataset.theme =
    store.get("algodial.theme") || (systemTheme.matches ? "dark" : "light");
  systemTheme.addEventListener("change", (event) => {
    if (!store.get("algodial.theme"))
      document.documentElement.dataset.theme = event.matches ? "dark" : "light";
  });
  $("theme-toggle").addEventListener("click", () =>
    theme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"),
  );

  // OAuth uses bearer tokens only, including when an admin cookie is present.
  function keepTokens(reply) {
    tokens = {
      access: reply.access_token,
      refresh: reply.refresh_token,
      expiresAt: Date.now() + (reply.expires_in || 3600) * 1000,
    };
    store.set("algodial.tokens", tokens);
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(
      () => refresh().catch(() => false),
      Math.max(30000, tokens.expiresAt - Date.now() - 60000),
    );
  }
  async function tokenRequest(fields) {
    const reply = await fetch(config.tokenUrl, {
      method: "POST",
      body: new URLSearchParams({ client_id: config.clientId, ...fields }),
      credentials: "omit",
    });
    return reply.json();
  }
  async function refresh() {
    if (!tokens?.refresh) return false;
    if (refreshing) return refreshing;
    const refreshToken = tokens.refresh;
    refreshing = (async () => {
      try {
        const reply = await tokenRequest({ grant_type: "refresh_token", refresh_token: refreshToken });
        if (!reply.success || !tokens || tokens.refresh !== refreshToken || signingOut) return false;
        keepTokens(reply);
        return true;
      } catch {
        return false;
      } finally {
        refreshing = null;
      }
    })();
    return refreshing;
  }
  async function api(query, variables = {}) {
    if (!tokens) throw new DeskError(0, t("Not signed in."));
    if (Date.now() > tokens.expiresAt - 30000 && !(await refresh())) {
      tokens = null;
      store.drop("algodial.tokens");
      showSignIn(t("Your session ended. Please sign in again."));
      throw new DeskError(0, t("Session ended."));
    }
    const reply = await fetch(config.graphqlUrl, {
      method: "POST",
      credentials: "omit",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${tokens.access}` },
      body: JSON.stringify({ query, variables }),
    });
    const body = await reply.json();
    if (body.errors?.length) throw new DeskError(0, body.errors[0].message);
    const answer = Object.values(body.data || {})[0];
    if (!answer?.status)
      throw new DeskError(answer?.code || 0, answer?.message || t("Request failed. Try again."));
    return answer.data;
  }
  async function command(query, variables, message = "Waiting for confirmation…") {
    notice();
    feedback(t(message));
    try {
      await api(query, variables);
      return true;
    } catch (error) {
      notice(error.message);
      feedback();
      return false;
    }
  }
  $("signin-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    $("signin-error").textContent = "";
    const submit = event.submitter;
    if (submit) submit.disabled = true;
    try {
      const fields = {
        grant_type: "password",
        username: $("email").value.trim(),
        password: $("password").value,
      };
      if ($("otp").value.trim()) fields.otp = $("otp").value.trim();
      const reply = await tokenRequest(fields);
      if (!reply.success) {
        if (reply.code === 8014) {
          $("otp-row").hidden = false;
          $("otp").focus();
        }
        $("signin-error").textContent = reply.message || reply.error_description || t("Sign-in failed.");
        return;
      }
      keepTokens(reply);
      $("password").value = $("otp").value = "";
      await openDesk();
    } catch {
      $("signin-error").textContent = t("Cannot reach the server. Check your connection and try again.");
    } finally {
      if (submit) submit.disabled = false;
    }
  });
  const DESK_QUERY = `query { myAgentDesk { status code message data {
    uid number name notReadyReasons wrapupCodes stateSince
    state { availability activity reason call wrapupCall endpoint }
    queues { name waiting longestWait ringTimeout wrapupSeconds }
    myDay { calls averageTalkSeconds notReadySeconds }
    currentCall { call caller callerName queue language summary aiReason connectedAt endedAt offeredAt waitSeconds ringTimeout wrapupSeconds }
    transferTargets { queues agents { uid number name availability activity } }
  } } }`;
  function fill(select, names, empty) {
    select.replaceChildren(new Option(t(empty), ""));
    for (const name of names || []) select.append(new Option(name, name));
  }
  function fillTargets(targets) {
    $("transfer-agent").replaceChildren();
    for (const other of targets?.agents || []) {
      const option = new Option(
        `${other.number} ${other.name} / ${t(STATE_NAMES[other.activity] || (other.availability === "ready" ? "Ready" : "Not ready"))}`,
        other.uid,
      );
      option.disabled = other.availability === "logged_out";
      $("transfer-agent").append(option);
    }
    fill($("transfer-queue"), targets?.queues, "Choose a queue");
  }
  async function openDesk() {
    try {
      desk = await api(DESK_QUERY);
    } catch (error) {
      showSignIn(error.message);
      return;
    }
    $("signin").hidden = true;
    $("desk").hidden = false;
    $("agent-name").textContent = desk.name;
    $("agent-number").textContent = desk.number;
    fill($("reason"), desk.notReadyReasons, "Choose a reason");
    fill($("wrapup-reason"), desk.notReadyReasons, "Choose a reason");
    fill(
      $("wrapup-code"),
      desk.wrapupCodes,
      desk.wrapupCodes.length ? "Choose a wrap-up code" : "No code required",
    );
    $("wrapup-code").required = Boolean(desk.wrapupCodes.length);
    $("wrapup-search").hidden = desk.wrapupCodes.length <= SEARCH_FROM;
    showRecovery();
    fillTargets(desk.transferTargets);
    adoptSnapshot(desk);
    connectLive();
    await startPhone();
  }
  function adoptSnapshot(data) {
    desk = { ...desk, ...data };
    if (data.currentCall) {
      const c = data.currentCall;
      offer = { ...(offer?.call === c.call ? offer : {}), ...c };
      if (c.connectedAt) connectedAt = stamp(c.connectedAt);
      if (c.endedAt) callEndedAt = stamp(c.endedAt);
      if (c.offeredAt) offeredAt = stamp(c.offeredAt);
    }
    applyState(data.state, data.stateSince);
    renderQueues();
    const day = data.myDay || {};
    $("day-calls").textContent = day.calls ?? 0;
    $("day-talk").textContent = day.averageTalkSeconds == null ? "—" : duration(day.averageTalkSeconds);
    $("day-not-ready").textContent = duration(day.notReadySeconds);
  }
  async function refreshDesk() {
    if (!tokens || signingOut || !desk) return;
    if (snapshotPromise) return snapshotPromise;
    const version = stateVersion,
      epoch = deskEpoch;
    snapshotPromise = (async () => {
      try {
        const data = await api(DESK_QUERY);
        if (!tokens || signingOut || !desk || epoch !== deskEpoch) return;
        if (version !== stateVersion) {
          scheduleSnapshot();
          return;
        }
        adoptSnapshot(data);
        const selected = $("transfer-agent").value;
        fillTargets(data.transferTargets);
        if ([...$("transfer-agent").options].some((o) => o.value === selected))
          $("transfer-agent").value = selected;
      } catch (error) {
        if (tokens) notice(error.message);
      } finally {
        snapshotPromise = null;
      }
    })();
    return snapshotPromise;
  }
  function scheduleSnapshot() {
    if (!snapshotTimer)
      snapshotTimer = setTimeout(() => {
        snapshotTimer = null;
        refreshDesk();
      }, 300);
  }
  function applyState(next, at) {
    if (!next) return;
    const normalized = { ...next, wrapupCall: next.wrapupCall ?? next.wrapup_call };
    const changed =
      !state ||
      ["availability", "activity", "reason", "call", "wrapupCall"].some(
        (key) => state[key] !== normalized[key],
      );
    state = normalized;
    if (state.activity === "idle" && !state.call && !state.wrapupCall && !session) {
      offer = null;
      connectedAt = callEndedAt = offeredAt = null;
    }
    if (Number.isFinite(stamp(at))) since = stamp(at);
    else if (changed) since = Date.now();
    if (changed) feedback();
    if (state.activity !== "wrapup") wrapPending = false;
    renderCall();
  }
  function renderQueues() {
    const rows = desk?.queues || [];
    $("my-queues").replaceChildren();
    for (const queue of rows) {
      const row = document.createElement("tr");
      for (const value of [queue.name, queue.waiting, queue.waiting ? duration(queue.longestWait) : "—"]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.append(cell);
      }
      $("my-queues").append(row);
    }
    const total = rows.reduce((sum, q) => sum + q.waiting, 0),
      longest = Math.max(0, ...rows.map((q) => q.longestWait || 0));
    $("queue-load").textContent = t("{count} waiting in your queues", { count: total });
    $("longest-wait").textContent = t("Longest wait {time}", { time: duration(longest) });
  }
  // One authenticated socket; reconnect and refresh the brain's snapshot.
  function connectLive() {
    if (live || !tokens || stopping) return;
    const socket = (live = new WebSocket(
      `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${config.liveUrl}`,
    ));
    socket.onopen = () => socket.send(JSON.stringify({ type: "auth", token: tokens.access }));
    socket.onmessage = ({ data: raw }) => {
      let data;
      try {
        data = JSON.parse(raw);
      } catch {
        return;
      }
      if (data.type === "hello") {
        liveRetry = 1000;
        liveReady = true;
        $("live-chip").textContent = t("Live: on");
        applyState(data.state);
        scheduleSnapshot();
        renderCall();
      } else if (data.type === "queues.changed") scheduleSnapshot();
      else if (data.type === "event") onEvent(data.event);
    };
    socket.onclose = async (event) => {
      if (live !== socket) return;
      live = null;
      liveReady = false;
      $("live-chip").textContent = t("Live: reconnecting");
      renderCall();
      if (signingOut || stopping || !tokens) return;
      if (event.code === 4401 && !(await refresh())) {
        tokens = null;
        store.drop("algodial.tokens");
        showSignIn(t("Your session ended. Please sign in again."));
        return;
      }
      liveTimer = setTimeout(connectLive, liveRetry);
      liveRetry = Math.min(liveRetry * 2, 15000);
    };
  }
  function onEvent(event) {
    const data = event.data || {};
    switch (event.type) {
      case "agent.state":
        stateVersion++;
        applyState(data, event.at);
        scheduleSnapshot();
        break;
      case "call.offered":
        offer = data;
        offeredAt = stamp(event.at);
        connectedAt = callEndedAt = null;
        notice();
        note(t("Incoming call"));
        announceOffer();
        break;
      case "call.connected":
        callEndedAt = null;
        connectedAt = stamp(event.at);
        lastConnected = data.call;
        note(t("On a call"));
        scheduleSnapshot();
        break;
      case "call.held":
        note(t("On hold"));
        break;
      case "call.resumed":
        note(t("Call resumed"));
        break;
      case "call.offer_failed":
        if (["no_answer", "offline"].includes(data.reason))
          notice(
            t("You missed a call, so you were set to Not responding. Press Go Ready when you are back."),
          );
        offer = null;
        ring(false);
        break;
      case "call.ended":
        callEndedAt ||= stamp(event.at);
        transfer = null;
        ring(false);
        scheduleSnapshot();
        break;
      case "transfer.started":
        if (data.agent === desk.uid)
          transfer = { id: data.transfer, kind: data.kind, state: "ringing", target: data.target };
        else if (data.target_agent === desk.uid) {
          offer = { caller: t("Transferred call"), queue: t("Consult transfer") };
          announceOffer();
        }
        break;
      case "transfer.answered":
        if (transfer?.id === data.transfer) transfer.state = "consulting";
        break;
      case "transfer.completed":
      case "transfer.cancelled":
      case "transfer.failed":
        if (transfer?.id === data.transfer) {
          transfer = null;
          $("transfer").hidden = true;
        }
        if (event.type === "transfer.failed") notice(t("Transfer failed. The caller is still with you."));
        break;
      case "conference.started":
        if (data.agent === desk.uid) transfer = { id: data.transfer, kind: "consult", state: "conference" };
        break;
      case "call.party_left":
        if (transfer?.state === "conference") transfer = null;
        break;
      case "agent.wrapped_up":
        if (data.by === "timeout" && $("notes").value.trim()) {
          store.set(`algodial.unsaved.${desk.uid}`, { call: draftCall, notes: $("notes").value });
          showRecovery();
          notice(t("Wrap-up time ran out. Open Unsaved notes from last call to copy your draft."));
        } else if (data.by !== "timeout" && draftCall) {
          store.drop(draftKey(draftCall));
          $("notes").value = "";
          draftCall = null;
        }
        wrapPending = false;
        scheduleSnapshot();
        break;
      case "command.rejected":
        notice(data.reason);
        feedback();
        wrapPending = false;
        break;
      default:
        break;
    }
    renderCall();
  }
  function draftKey(call) {
    return `algodial.notes.${desk.uid}.${call}`;
  }
  function syncDraft(call) {
    if (!call || call === draftCall) return;
    if (draftCall) store.set(draftKey(draftCall), $("notes").value);
    draftCall = call;
    $("notes").value = store.get(draftKey(call)) || "";
    $("note-status").textContent = t("Draft; saved with wrap-up");
  }
  $("notes").addEventListener("input", () => {
    if (draftCall) store.set(draftKey(draftCall), $("notes").value);
  });
  function showRecovery() {
    const saved = store.get(`algodial.unsaved.${desk.uid}`);
    $("unsaved-notes").hidden = !saved;
    $("recovery-notes").value = saved?.notes || "";
  }
  $("discard-draft").addEventListener("click", () => {
    const saved = store.get(`algodial.unsaved.${desk.uid}`);
    if (saved?.call) store.drop(draftKey(saved.call));
    store.drop(`algodial.unsaved.${desk.uid}`);
    showRecovery();
    notice();
  });
  function renderCall() {
    if (!desk || !state) return;
    const ringing = Boolean(session && !session.isEstablished() && !session.isEnded());
    const talking = Boolean(session?.isEstablished()),
      wrapping = state.activity === "wrapup";
    const held = state.activity === "on_hold",
      incoming = ringing || state.activity === "offered";
    const problem = !phoneReady || !micReady;
    let page = problem
      ? "phone-problem"
      : wrapping
        ? "wrap-up"
        : transfer
          ? "transfer"
          : incoming
            ? "incoming"
            : talking
              ? held
                ? "on-hold"
                : "talking"
              : state.availability === "ready"
                ? "ready"
                : "not-ready";
    document.body.dataset.state = document.body.dataset.page = page;
    const key = problem
      ? "Phone not connected"
      : wrapping
        ? "Wrap-up"
        : incoming
          ? "Incoming call"
          : talking
            ? held
              ? "On hold"
              : "On a call"
            : state.activity === "not_responding"
              ? "Not ready: Not responding"
              : state.availability === "ready"
                ? "Ready"
                : "Not ready: {reason}";
    $("state-title").textContent = t(key, { reason: state.reason || t("Choose a reason") });
    icon(
      {
        "phone-problem": "plug",
        "wrap-up": "clipboard",
        incoming: "ring",
        talking: "phone",
        "on-hold": "pause",
        transfer: "pause",
        ready: "check",
        "not-ready": "minus",
      }[page],
    );
    $("availability").textContent = t(
      state.availability === "ready"
        ? "Ready"
        : state.availability === "logged_out"
          ? "Logged out"
          : "Not Ready",
    );
    // The state bar's second line gives context; the state itself is already the title (UI_DESIGN.md 4).
    const where = offer?.queue ? `${offer.queue}${offer.language ? ` / ${offer.language}` : ""}` : "";
    $("activity").textContent = problem
      ? phoneProblem || t("Connecting your phone…")
      : transfer
        ? t("Consulting {name}; caller on hold", { name: targetName(transfer) })
        : incoming || talking || wrapping
          ? where || t(STATE_NAMES[state.activity] || "On a call")
          : state.activity === "not_responding"
            ? t("You missed a call")
            : state.availability === "ready"
              ? t("Waiting for the next call")
              : state.reason
                ? t("Not ready since {time}", { time: clockTime(since) })
                : t("Go Ready when you can take calls");
    $("ready").disabled = $("empty-ready").disabled =
      problem || !liveReady || (state.availability === "ready" && state.activity !== "not_responding");
    $("not-ready").disabled = state.availability === "logged_out" || !liveReady;
    $("incoming-offer").hidden = !incoming;
    $("answer").disabled = !ringing || !micReady;
    $("reject").disabled = !ringing;
    const identity = session?.remote_identity;
    const caller =
      offer?.caller ||
      offer?.caller_name ||
      offer?.callerName ||
      identity?.display_name ||
      identity?.uri.user ||
      t("Unknown caller");
    const queue = offer?.queue || t("Queue unknown"),
      language = offer?.language || t("Language unknown");
    $("offer-caller").textContent = $("caller").textContent = caller;
    $("caller-name").textContent = offer?.caller_name || offer?.callerName || "";
    $("offer-meta").textContent = `${queue} / ${language}`;
    $("caller-queue").textContent = queue;
    $("caller-language").textContent = language;
    const wait = offer?.waitSeconds ?? offer?.wait_seconds;
    $("caller-wait").textContent =
      wait == null ? t("Wait unknown") : t("Waited {time}", { time: duration(wait) });
    const summary = offer?.summary || "";
    $("offer-summary").textContent = summary || t("No AI summary for this call.");
    $("ai-summary").hidden = !(summary && !wrapping && (talking || incoming));
    $("ai-summary-text").textContent = summary;
    $("ai-reason").textContent = t(AI_REASONS[offer?.ai_reason || offer?.aiReason] || "");
    $("context-language").textContent = language;
    $("context-topic").textContent = queue;
    $("context-meta").hidden = wrapping || !(talking || incoming);
    $("context-empty").hidden = talking || incoming || wrapping;
    $("context-heading").textContent = t(wrapping ? "Finish this call" : "Context");
    $("notes-zone").hidden = !(talking || wrapping);
    $("wrapup").hidden = $("wrapup-actions").hidden = !wrapping;
    $("wrapup-done").disabled = wrapPending || !liveReady || problem;
    $("wrapup-not-ready").disabled = wrapPending || !liveReady;
    $("code-required").hidden = !desk.wrapupCodes.length;
    syncDraft(state.call || state.wrapupCall || (talking ? lastConnected : null));
    $("call").hidden = !(talking || wrapping);
    $("empty-call").hidden = talking || wrapping;
    $("empty-ready").hidden = page !== "not-ready";
    $("reconnect").hidden = !problem;
    $("empty-title").textContent = t(
      problem
        ? "Reconnect your phone"
        : incoming
          ? "A call is waiting for you"
          : page === "ready"
            ? "Waiting for the next call"
            : "No call",
    );
    $("empty-description").textContent = problem
      ? phoneProblem || t("Connecting your phone…")
      : t(
          incoming
            ? "Review the caller’s summary above, then answer."
            : page === "ready"
              ? "Calls from your assigned queues will appear here."
              : "Go Ready when you can take calls.",
        );
    $("call-title").textContent = t(wrapping ? "Call ended" : held ? "On hold" : "Connected");
    $("call-controls").hidden = !talking;
    $("call-time").hidden = Boolean(transfer);
    $("transfer-toggle").hidden = $("keypad-toggle").hidden = Boolean(transfer);
    $("hold").hidden = !talking || held || Boolean(transfer);
    $("resume").hidden = !talking || !held || Boolean(transfer);
    $("hold").disabled = $("resume").disabled = !liveReady;
    $("transfer-toggle").disabled = !liveReady || held || Boolean(transfer);
    $("hangup").disabled = !talking;
    $("mute-label").textContent = t(session?.isMuted().audio ? "Unmute" : "Mute");
    $("mute").setAttribute("aria-pressed", String(Boolean(session?.isMuted().audio)));
    $("consult").hidden = !(transfer?.kind === "consult" && transfer.state !== "conference");
    $("consult-status").textContent = t(
      transfer?.state === "ringing" ? "Ringing {name}; caller on hold" : "Consulting {name}; caller on hold",
      { name: transfer ? targetName(transfer) : "" },
    );
    $("consult-complete").disabled = $("consult-conference").disabled =
      transfer?.state !== "consulting" || !liveReady;
    $("conference").hidden = transfer?.state !== "conference";
    if (transfer || !talking) {
      $("transfer").hidden = true;
      $("transfer-toggle").setAttribute("aria-expanded", "false");
    }
    if (!talking) {
      $("keypad").hidden = true;
      $("keypad-toggle").setAttribute("aria-expanded", "false");
    }
    tick();
  }
  function tick() {
    $("call-timer").textContent = duration(
      ((callEndedAt || session?.end_time?.getTime() || Date.now()) - (connectedAt || Date.now())) / 1000,
    );
    const timeout =
      offer?.ringTimeout ||
      offer?.ring_timeout ||
      desk?.queues?.find((q) => q.name === offer?.queue)?.ringTimeout ||
      20;
    const left = Math.max(0, timeout - (Date.now() - (offeredAt || Date.now())) / 1000);
    $("ring-progress").max = timeout;
    $("ring-progress").value = left;
    $("offer-timeout").textContent = t("{seconds} seconds left to answer", { seconds: Math.ceil(left) });
    const wrap = offer?.wrapupSeconds ?? offer?.wrapup_seconds ?? 0;
    $("since").textContent = duration(
      state?.activity === "wrapup" && wrap ? wrap - (Date.now() - since) / 1000 : (Date.now() - since) / 1000,
    );
    $("wrapup-time").textContent = wrap
      ? t("Wrap-up ends in {time}", { time: duration(wrap - (Date.now() - since) / 1000) })
      : t("Time in wrap-up {time}", { time: duration((Date.now() - since) / 1000) });
  }
  setInterval(tick, 1000);

  // Browser media never decides the brain's call state.
  const disconnectedMessage =
    "Your phone is not connected to the call server. Press Reconnect. If it fails twice, tell your supervisor.";
  const blockedMessage =
    "Your browser is blocking the microphone. Allow it in the address bar, then press Reconnect.";
  function unavailable(message) {
    const wasReady = phoneReady;
    phoneReady = false;
    phoneProblem = t(message);
    $("phone-chip").textContent = t("Phone disconnected");
    renderCall();
    if (!stopping && !signingOut && tokens && (wasReady || state?.availability === "ready")) {
      command(
        'mutation { setMyAvailability(ready: false, reason: "Phone not connected") { status code message } }',
        {},
        "Calls paused until your phone reconnects",
      );
    }
  }
  async function microphone() {
    try {
      if (!navigator.mediaDevices?.getUserMedia) throw new Error("unavailable");
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      stream.getTracks().forEach((track) => track.stop());
      micReady = true;
      return true;
    } catch (error) {
      micReady = false;
      unavailable(
        error.name === "NotAllowedError"
          ? blockedMessage
          : "Your microphone is unavailable. Connect it, allow microphone access, then press Reconnect.",
      );
      return false;
    }
  }
  async function startPhone() {
    if (ua) return;
    phoneProblem = t("Connecting your phone…");
    renderCall();
    if (!(await microphone())) return;
    let phone;
    try {
      phone = await api(
        `mutation { startAgentSession { status code message data { sipId password wsUrl domain endpoint commandId } } }`,
      );
    } catch (error) {
      unavailable(disconnectedMessage);
      notice(error.message);
      return;
    }
    if (!tokens || stopping) return;
    JsSIP.debug.disable("JsSIP:*");
    const phoneUa = (ua = new JsSIP.UA({
      sockets: [new JsSIP.WebSocketInterface(phone.wsUrl)],
      uri: `sip:${phone.sipId}@${phone.domain}`,
      authorization_user: phone.sipId,
      password: phone.password,
      display_name: desk.name,
      register: true,
      register_expires: 120,
      session_timers: false,
      user_agent: "AlgoDial agent page",
    }));
    phoneUa.on("connecting", () => {
      $("phone-chip").textContent = t("Phone connecting");
    });
    phoneUa.on("registered", () => {
      if (ua !== phoneUa) return;
      phoneReady = true;
      phoneProblem = "";
      $("phone-chip").textContent = t("Phone connected"); // plain words; the account is for support
      $("phone-chip").dataset.sipId = phone.sipId;
      $("phone-chip").title = t("Phone: {id}", { id: phone.sipId });
      note(t("Phone connected"));
      renderCall();
    });
    for (const event of ["disconnected", "unregistered", "registrationFailed"])
      phoneUa.on(event, () => {
        if (ua === phoneUa && !stopping) unavailable(disconnectedMessage);
      });
    phoneUa.on("newRTCSession", ({ session: incoming, originator }) => {
      if (originator !== "remote") return;
      if (session || !micReady) {
        incoming.terminate({ status_code: 486, reason_phrase: "Busy Here" });
        return;
      }
      // SIP can arrive before the next live offer. Never show the previous caller's context.
      if (offer?.call && offer.call === lastConnected) {
        offer = null;
        offeredAt = null;
      }
      connectedAt = callEndedAt = null;
      session = incoming;
      offeredAt ||= Date.now();
      incoming.on("peerconnection", ({ peerconnection }) =>
        peerconnection.addEventListener("track", (track) => {
          $("remote-audio").srcObject = track.streams[0];
          $("remote-audio")
            .play()
            .catch(() => notice(t("Press Answer again to enable call audio.")));
        }),
      );
      incoming.on("confirmed", () => {
        connectedAt ||= Date.now();
        ring(false);
        renderCall();
      });
      incoming.on("getusermediafailed", () => {
        micReady = false;
        unavailable(blockedMessage);
      });
      incoming.on("ended", endSession);
      incoming.on("failed", endSession);
      ring(true);
      announceOffer();
      renderCall();
    });
    phoneUa.start();
  }
  function endSession() {
    callEndedAt ||= Date.now();
    ring(false);
    session = null;
    $("remote-audio").srcObject = null;
    $("transfer").hidden = $("keypad").hidden = true;
    renderCall();
    scheduleSnapshot();
  }
  function ring(on) {
    if (!on) {
      if (ringer) {
        clearInterval(ringer.timer);
        ringer.context.close().catch(() => {});
        ringer = null;
      }
      return;
    }
    if (ringer) return;
    try {
      const context = new AudioContext();
      const beep = () => {
        for (const frequency of [440, 480]) {
          const oscillator = context.createOscillator(),
            gain = context.createGain();
          oscillator.frequency.value = frequency;
          gain.gain.value = 0.05;
          oscillator.connect(gain).connect(context.destination);
          oscillator.start();
          oscillator.stop(context.currentTime + 1.2);
        }
      };
      context.resume().catch(() => {});
      beep();
      ringer = { context, timer: setInterval(beep, 3000) };
    } catch {
      /* the visible incoming offer remains usable without Web Audio */
    }
  }
  function announceOffer() {
    if (document.hidden && "Notification" in window && Notification.permission === "granted")
      new Notification(t("Incoming call"), {
        body: t("Return to AlgoDial to answer."),
        tag: "algodial-incoming",
      });
  }
  function toggle(id, button) {
    $(id).hidden = !$(id).hidden;
    $(button).setAttribute("aria-expanded", String(!$(id).hidden));
    if (!$(id).hidden) $(id).querySelector("select,input,button")?.focus();
  }
  $("state-menu-toggle").addEventListener("click", () => toggle("state-menu", "state-menu-toggle"));
  $("ready").addEventListener("click", async () => {
    if (await command("mutation { setMyAvailability(ready: true) { status code message } }")) {
      $("state-menu").hidden = true;
      $("state-menu-toggle").setAttribute("aria-expanded", "false");
    }
  });
  $("empty-ready").addEventListener("click", () => $("ready").click());
  $("not-ready").addEventListener("click", async () => {
    if (!$("reason").value) {
      notice(t("Choose a reason before going Not Ready."));
      $("reason").focus();
      return;
    }
    if (
      await command(
        "mutation R($reason: String) { setMyAvailability(ready: false, reason: $reason) { status code message } }",
        { reason: $("reason").value },
      )
    ) {
      $("state-menu").hidden = true;
      $("state-menu-toggle").setAttribute("aria-expanded", "false");
    }
  });
  $("enable-notifications").addEventListener("click", async () => {
    if ("Notification" in window) {
      const value = await Notification.requestPermission();
      feedback(
        t(
          value === "granted"
            ? "Call notifications enabled"
            : "Call notifications are blocked in your browser",
        ),
      );
    }
  });
  $("reconnect").addEventListener("click", async () => {
    if (session) {
      notice(t("Hang up before reconnecting your phone."));
      return;
    }
    $("reconnect").disabled = true;
    if (ua) {
      const old = ua;
      ua = null;
      old.stop();
    }
    phoneReady = false;
    await startPhone();
    $("reconnect").disabled = false;
  });
  $("answer").addEventListener("click", async () => {
    if (!session) return;
    try {
      ring(false);
      if (session.isEstablished()) await $("remote-audio").play();
      else session.answer({ mediaConstraints: { audio: true, video: false }, pcConfig: { iceServers: [] } });
    } catch {
      micReady = false;
      unavailable(blockedMessage);
    }
  });
  $("reject").addEventListener("click", () =>
    session?.terminate({ status_code: 486, reason_phrase: "Busy Here" }),
  );
  $("hangup").addEventListener("click", () => session?.terminate());
  $("hold").addEventListener("click", () =>
    command("mutation { holdMyCall(hold: true) { status code message } }"),
  );
  $("resume").addEventListener("click", () =>
    command("mutation { holdMyCall(hold: false) { status code message } }"),
  );
  $("mute").addEventListener("click", () => {
    if (!session) return;
    if (session.isMuted().audio) session.unmute({ audio: true });
    else session.mute({ audio: true });
    renderCall();
  });
  $("keypad-toggle").addEventListener("click", () => toggle("keypad", "keypad-toggle"));
  $("send-digits").addEventListener("click", () => {
    const digits = $("digits").value.trim();
    if (!session?.isEstablished()) return;
    if (!/^[0-9*#]+$/.test(digits)) {
      notice(t("Use digits 0 to 9, star or hash."));
      return;
    }
    try {
      session.sendDTMF(digits);
      feedback(t("Digits sent"));
    } catch {
      notice(t("Could not send digits. Try again."));
    }
  });
  $("transfer-toggle").addEventListener("click", () => {
    toggle("transfer", "transfer-toggle");
    refreshDesk();
  });
  $("transfer-target-kind").addEventListener("change", () => {
    const kind = $("transfer-target-kind").value;
    for (const choice of ["agent", "queue", "number"]) $("transfer-" + choice).hidden = kind !== choice;
  });
  $("transfer-go").addEventListener("click", () => {
    const targetKind = $("transfer-target-kind").value,
      target = $("transfer-" + targetKind).value.trim();
    if (!target) {
      notice(t("Choose a transfer target."));
      return;
    }
    command(
      "mutation T($kind: String!, $targetKind: String!, $target: String!) { transferMyCall(kind: $kind, targetKind: $targetKind, target: $target) { status code message } }",
      { kind: $("transfer-kind").value, targetKind, target },
    );
  });
  const step = (name) => () =>
    command("mutation S($step: String!) { myTransferStep(step: $step) { status code message } }", {
      step: name,
    });
  for (const [id, action] of [
    ["consult-complete", "complete"],
    ["consult-cancel", "cancel"],
    ["consult-conference", "conference"],
    ["conference-drop", "drop_other"],
    ["conference-leave", "leave"],
  ])
    $(id).addEventListener("click", step(action));
  $("wrapup-search").addEventListener("input", () => {
    const selected = $("wrapup-code").value;
    fill(
      $("wrapup-code"),
      desk.wrapupCodes.filter((code) =>
        code.toLocaleLowerCase().includes($("wrapup-search").value.toLocaleLowerCase()),
      ),
      "Choose a wrap-up code",
    );
    if ([...$("wrapup-code").options].some((o) => o.value === selected)) $("wrapup-code").value = selected;
  });
  async function finishWrapup(ready) {
    const code = $("wrapup-code").value,
      reason = $("wrapup-reason").value;
    if (desk.wrapupCodes.length && !code) {
      notice(t("Choose a wrap-up code to finish this call."));
      $("wrapup-code").focus();
      return;
    }
    if (!ready && !reason) {
      notice(t("Choose a reason before going Not Ready."));
      $("wrapup-reason").focus();
      return;
    }
    wrapPending = true;
    renderCall();
    if (
      !(await command(
        "mutation W($code: String!, $notes: String, $ready: Boolean, $reason: String) { finishMyWrapup(code: $code, notes: $notes, ready: $ready, reason: $reason) { status code message } }",
        { code, notes: $("notes").value, ready, reason },
      ))
    ) {
      wrapPending = false;
      renderCall();
    }
  }
  $("wrapup-done").addEventListener("click", () => finishWrapup(true));
  $("wrapup-not-ready").addEventListener("click", () => finishWrapup(false));
  $("signout").addEventListener("click", async () => {
    if (session || state?.call || state?.activity === "wrapup") {
      notice(t("Finish your call and wrap-up before signing out."));
      return;
    }
    signingOut = true;
    if (!(await command("mutation { endAgentSession { status code message } }"))) {
      signingOut = false;
      return;
    }
    if (tokens)
      fetch(config.revokeUrl, {
        method: "POST",
        body: new URLSearchParams({ token: tokens.access, client_id: config.clientId }),
        credentials: "omit",
      }).catch(() => {});
    tokens = null;
    store.drop("algodial.tokens");
    showSignIn(t("Signed out."));
    signingOut = false;
  });
  document.addEventListener("keydown", (event) => {
    if (
      $("desk").hidden ||
      event.repeat ||
      event.altKey ||
      event.ctrlKey ||
      event.metaKey ||
      event.isComposing
    )
      return;
    if (event.key === "Escape") {
      for (const [id, control] of [
        ["state-menu", "state-menu-toggle"],
        ["transfer", "transfer-toggle"],
        ["keypad", "keypad-toggle"],
      ]) {
        if (!$(id).hidden) {
          $(id).hidden = true;
          $(control).setAttribute("aria-expanded", "false");
          $(control).focus();
        }
      }
      return;
    }
    let id;
    if (event.key === "Enter" && !$("incoming-offer").hidden && !$("answer").disabled) id = "answer";
    else {
      if (event.target.closest("input,textarea,select,[contenteditable=true]")) return;
      id = {
        h: $("resume").hidden ? "hold" : "resume",
        m: "mute",
        t: "transfer-toggle",
        k: "keypad-toggle",
        e: "hangup",
        r: "state-menu-toggle",
      }[event.key.toLowerCase()];
    }
    if (id && !$(id).disabled && $(id).getClientRects().length) {
      event.preventDefault();
      $(id).click();
    }
  });
  function stopEverything() {
    deskEpoch++;
    stopping = true;
    clearTimeout(refreshTimer);
    clearTimeout(liveTimer);
    clearTimeout(snapshotTimer);
    snapshotTimer = null;
    ring(false);
    if (ua) {
      const old = ua;
      ua = null;
      old.stop();
    }
    if (live) {
      live.onclose = null;
      live.close();
      live = null;
    }
    session = null;
    offer = null;
    transfer = null;
    state = null;
    desk = null;
    draftCall = null;
    connectedAt = callEndedAt = offeredAt = null;
    phoneReady = micReady = liveReady = false;
    $("notes").value = "";
    $("remote-audio").srcObject = null;
    $("log").replaceChildren();
    notice();
    feedback();
    stopping = false;
  }
  function showSignIn(message) {
    stopEverything();
    $("desk").hidden = true;
    $("signin").hidden = false;
    $("signin-error").textContent = message || "";
    $("email").focus();
  }
  window.addEventListener("beforeunload", () => {
    stopping = true;
    if (ua) ua.stop();
  });
  window.agentDesk = Object.freeze({
    get session() {
      return session;
    },
    get state() {
      return state;
    },
    get transfer() {
      return transfer;
    },
  });
  if (tokens) openDesk();
  else showSignIn("");
})();
