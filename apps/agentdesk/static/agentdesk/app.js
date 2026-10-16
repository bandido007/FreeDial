/* Approved agent workspace. The brain owns availability and call state;
 * SIP owns browser media. UI preferences and per-call draft notes are local. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const config = JSON.parse($("desk-config").textContent);
  // Labels come from translations/<language>.json; the agent picks the language (UI_DESIGN.md 2.7).
  // Switching re-labels the page in place: it never reloads, so a call in progress goes on.
  const LANGUAGES = config.messages;
  let language = "en";
  try {
    language = LANGUAGES[localStorage.getItem("algodial.language")]
      ? localStorage.getItem("algodial.language")
      : "en";
  } catch {
    /* no storage: English */
  }
  const t = (key, values = {}) =>
    Object.entries(values).reduce(
      (text, [name, value]) => text.replaceAll(`{${name}}`, String(value)),
      LANGUAGES[language]?.[key] || LANGUAGES.en[key] || key,
    );
  function translatePage() {
    document.documentElement.lang = language;
    document.querySelectorAll("[data-i18n]").forEach((node) => {
      node.textContent = t(node.dataset.i18n);
    });
    document.querySelectorAll("[data-placeholder]").forEach((node) => {
      node.placeholder = t(node.dataset.placeholder);
    });
    for (const attribute of ["aria-label", "title"]) {
      const keep = `i18n${attribute === "title" ? "Title" : "Aria"}`; // the English key, kept for the next switch
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
  function label(node, key) {
    // Text that stays on screen: keep its English key, so a language switch relabels it.
    node.dataset.i18n = key;
    node.textContent = t(key);
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
  // A desk phone an admin set on this agent ({sipId}): calls ring it, not the browser.
  let deskPhone = null;
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
  function stateAction() {
    // What the state button offers next. Not Ready (or not responding): Go Ready, at once.
    // Ready: Go Not Ready, with a reason. During a call either applies once the call ends.
    const busy = ["offered", "talking", "on_hold", "wrapup"].includes(state?.activity);
    const notReady = state?.availability !== "ready" || state?.activity === "not_responding";
    if (notReady) return { direct: true, label: busy ? "Ready after this call" : "Go Ready" };
    return { direct: false, label: busy ? "Not Ready after this call" : "Go Not Ready" };
  }
  function targetName(current) {
    // An agent transfer carries the colleague's uid; the page already lists colleagues by name.
    const colleague = desk?.transferTargets?.agents?.find((agent) => agent.uid === current.target);
    return colleague ? colleague.name : current.target || t("the other party");
  }
  function clockTime(stamp) {
    return new Date(stamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  const WRAPUP_EDIT_SECONDS = 120; // the brain accepts a corrected code this long
  let lastWrap = null; // { call, code, at }: the code just given, still correctable
  const SEARCH_FROM = 10; // wrap-up codes before a search box helps (a short list is quicker as a select)
  // UX_PRINCIPLES.md section 4: every failure has a designed message that says what to do.
  const CONNECTION_LOST =
    "Connection lost. Your call continues if your phone is still connected. Reconnecting…";
  const TRANSFER_FAILURES = {
    no_answer: "{name} did not answer. You are back with the caller.",
    rejected: "{name} declined the call. You are back with the caller.",
    offline: "{name} could not be reached. You are back with the caller.",
  };
  let connectionLost = false;
  let phoneLibraryLoading = null;
  function phoneLibrary() {
    // UX_PRINCIPLES.md section 3: the first load stays small; the SIP library comes after sign-in.
    if (window.JsSIP) return Promise.resolve(true);
    phoneLibraryLoading ||= new Promise((resolve) => {
      const script = document.createElement("script");
      script.src = config.phoneLibraryUrl;
      script.onload = () => resolve(true);
      script.onerror = () => {
        phoneLibraryLoading = null;
        resolve(false);
      };
      document.head.append(script);
    });
    return phoneLibraryLoading;
  }
  const LANGUAGE_NAMES = { swahili: "Swahili", english: "English" }; // routing skills, shown in the agent's language
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
  // ── UX signals (UX_PRINCIPLES.md section 6): measurement only, sent and forgotten ──
  const SIGNAL_MUTATION =
    "mutation S($signal: String!, $signalId: String!, $call: String, $data: GenericScalar) " +
    "{ recordUxSignal(signal: $signal, signalId: $signalId, call: $call, data: $data) { status } }";
  const sentSignals = new Set();
  function newId() {
    if (crypto.randomUUID) return crypto.randomUUID();
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  function signal(kind, call, data = {}, once = "") {
    if (once) {
      if (sentSignals.has(once)) return;
      sentSignals.add(once);
    }
    api(SIGNAL_MUTATION, { signal: kind, signalId: newId(), call: call || "", data }).catch(() => {});
  }
  // Section 3: how long a button takes from press to the brain's confirmation (a state change).
  const CONFIRMED_BY_STATE = new Set(["setMyAvailability", "holdMyCall", "finishMyWrapup"]);
  let pendingAction = null;
  function confirmAction(ok) {
    if (!pendingAction) return;
    const { action, pressed, call } = pendingAction;
    pendingAction = null;
    signal("ux.action", call, { action, ms: Math.round(performance.now() - pressed), ok });
  }
  async function command(query, variables, message = "Waiting for confirmation…") {
    notice();
    feedback(t(message));
    const action = (query.match(/\{\s*(\w+)/) || [])[1] || "action";
    const pressed = performance.now();
    const call = state?.call || state?.wrapupCall || "";
    // Wait for the confirming state change before sending: it can arrive before the API reply.
    const confirmed = CONFIRMED_BY_STATE.has(action);
    if (confirmed) {
      pendingAction = { action, pressed, call };
      setTimeout(() => pendingAction?.pressed === pressed && confirmAction(false), 10000);
    }
    try {
      await api(query, variables);
      if (!confirmed)
        signal("ux.action", call, { action, ms: Math.round(performance.now() - pressed), ok: true });
      return true;
    } catch (error) {
      if (confirmed && pendingAction?.pressed === pressed) confirmAction(false);
      else if (!confirmed)
        signal("ux.action", call, { action, ms: Math.round(performance.now() - pressed), ok: false });
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
    deskPhone uid number name notReadyReasons wrapupCodes stateSince
    state { availability activity reason call wrapupCall endpoint }
    queues { name waiting longestWait ringTimeout wrapupSeconds }
    myDay { calls averageTalkSeconds notReadySeconds }
    currentCall { call caller callerName queue language summary aiReason connectedAt endedAt offeredAt waitSeconds ringTimeout wrapupSeconds }
    transferTargets { queues agents { uid number name availability activity } }
  } } }`;
  function fill(select, names, empty) {
    const placeholder = new Option(t(empty), "");
    placeholder.dataset.i18n = empty; // relabelled when the agent switches language
    select.replaceChildren(placeholder);
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
    $("reason-buttons").replaceChildren(
      ...desk.notReadyReasons.map((reason) => {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.reason = reason;
        button.textContent = reason;
        button.addEventListener("click", () => goNotReady(reason));
        return button;
      }),
    );
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
    if (changed) {
      feedback();
      confirmAction(true);
    }
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
        if (connectionLost) {
          connectionLost = false;
          notice();
          feedback(t("Connection back. Your screen is up to date."));
        }
        label($("live-chip"), "Live: on");
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
      label($("live-chip"), "Live: reconnecting");
      if (!signingOut && !stopping && tokens) {
        connectionLost = true;
        notice(t(CONNECTION_LOST));
      }
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
      case "transfer.failed": {
        const ours = transfer?.id === data.transfer ? transfer : null;
        if (ours) {
          transfer = null;
          $("transfer").hidden = true;
        }
        if (event.type === "transfer.failed")
          notice(
            ours?.kind === "consult" && TRANSFER_FAILURES[data.reason]
              ? t(TRANSFER_FAILURES[data.reason], { name: targetName(ours) })
              : t("Transfer failed. The caller is still with you."),
          );
        break;
      }
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
    // A desk phone rings and is answered on the phone itself: only the brain's state shows it here.
    const ringing = deskPhone
      ? state.activity === "offered"
      : Boolean(session && !session.isEstablished() && !session.isEnded());
    const talking = deskPhone
        ? state.activity === "talking" || state.activity === "on_hold"
        : Boolean(session?.isEstablished()),
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
    // Logged out here means the brain has not taken this sign-in yet: Ready would be ignored.
    const starting = state.availability === "logged_out";
    $("ready").disabled = $("empty-ready").disabled =
      problem ||
      !liveReady ||
      starting ||
      (state.availability === "ready" && state.activity !== "not_responding");
    const action = stateAction();
    $("state-action").textContent = t(action.label);
    $("state-action-icon").setAttribute("href", action.direct ? "#i-check" : "#i-minus");
    $("state-action-caret").style.display = action.direct ? "none" : "";
    $("state-menu-toggle").classList.toggle("cta", action.direct && !problem);
    $("state-menu-toggle").disabled = action.direct && $("ready").disabled;
    $("state-menu-toggle").title =
      action.direct && problem
        ? t("Reconnect your phone first")
        : starting
          ? t("Starting your session…")
          : "";
    $("ready").hidden = !action.direct;
    $("reason-heading").hidden = $("reason-buttons").hidden = action.direct;
    for (const button of $("reason-buttons").children) button.disabled = !liveReady;
    $("incoming-offer").hidden = !incoming;
    $("answer").disabled = !ringing || !micReady;
    $("reject").disabled = !ringing;
    $("answer").hidden = $("reject").hidden = Boolean(deskPhone);
    $("offer-hint").hidden = !deskPhone;
    if (deskPhone) $("offer-hint").textContent = t("Pick up phone {id} to answer.", { id: deskPhone.sipId });
    const identity = session?.remote_identity;
    const caller =
      offer?.caller ||
      offer?.caller_name ||
      offer?.callerName ||
      identity?.display_name ||
      identity?.uri.user ||
      t("Unknown caller");
    const queue = offer?.queue || t("Queue unknown"),
      language = offer?.language
        ? t(LANGUAGE_NAMES[offer.language.toLowerCase()] || offer.language)
        : t("Language unknown");
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
    const callId = offer?.call || state.call || state.wrapupCall || "";
    if (incoming && callId)
      signal("ux.offer_shown", callId, { shown_at: new Date().toISOString() }, `shown:${callId}`);
    if (summary && callId && (incoming || talking)) {
      const where = incoming ? "offer" : "call";
      signal("ux.ai_summary_shown", callId, { where }, `summary:${callId}:${where}`);
    }
    $("summary-wrong").disabled = sentSignals.has(`summary-wrong:${callId}`);
    $("queue-wrong").disabled = sentSignals.has(`queue-wrong:${callId}`);
    $("last-wrap").hidden = !lastWrap || incoming || talking || wrapping || problem;
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
    // Mute, digits and hang up happen on the desk phone itself.
    $("mute").hidden = $("hangup").hidden = Boolean(deskPhone);
    if (deskPhone) $("keypad-toggle").hidden = true;
    $("phone-hint").hidden = !deskPhone || !talking;
    if (deskPhone)
      $("phone-hint").textContent = t("Hang up, mute and send digits on phone {id}.", {
        id: deskPhone.sipId,
      });
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
    if (lastWrap) {
      const left = WRAPUP_EDIT_SECONDS - (Date.now() - lastWrap.at) / 1000;
      if (left <= 0) {
        lastWrap = null;
        $("last-wrap").hidden = true;
      } else {
        $("last-wrap-code").textContent = lastWrap.code;
        $("last-wrap-time").textContent = t("You can change it for {time}", { time: duration(left) });
      }
    }
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
  function unavailable(message, values = {}) {
    const wasReady = phoneReady;
    phoneReady = false;
    phoneProblem = t(message, values);
    label($("phone-chip"), "Phone disconnected");
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
  async function startDeskPhone() {
    phoneProblem = t("Connecting your phone…");
    renderCall();
    let phone;
    try {
      phone = await api(
        `mutation { startAgentSession { status code message data { deskPhone sipId endpoint commandId phoneState } } }`,
      );
    } catch (error) {
      unavailable(disconnectedMessage);
      notice(error.message);
      return;
    }
    if (!tokens || stopping) return;
    deskPhone = { sipId: phone.sipId };
    micReady = true; // the desk phone has its own microphone
    $("phone-chip").dataset.sipId = phone.sipId;
    $("phone-chip").title = t("Phone: {id}", { id: phone.sipId });
    if (phone.phoneState !== "online") {
      unavailable(
        phone.phoneState === "unknown"
          ? "Cannot check phone {id}: the call server did not answer. Press Reconnect."
          : "Phone {id} is not connected. Turn it on or open your softphone, then press Reconnect.",
        { id: phone.sipId },
      );
      return;
    }
    deskPhoneConnected();
  }
  function deskPhoneConnected() {
    phoneReady = true;
    phoneProblem = "";
    label($("phone-chip"), "Desk phone connected");
    note(t("Desk phone connected"));
    renderCall();
  }
  // Asterisk learns a phone's state only at its next check (every 30 s), and a desk phone can be
  // switched off mid-shift: ask again every 10 s while it is not connected, every 30 s while it is.
  let deskChecks = 0;
  async function checkDeskPhone() {
    if (!deskPhone || !tokens || stopping) return;
    deskChecks++;
    if (phoneReady && deskChecks % 3) return;
    let answer;
    try {
      answer = await api("query { myPhoneState { status code message data { state } } }");
    } catch {
      return; // a failed request says nothing about the phone
    }
    if (!deskPhone) return;
    if (answer.state === "online" && !phoneReady) deskPhoneConnected();
    else if (answer.state === "offline" && phoneReady)
      unavailable("Phone {id} is not connected. Turn it on or open your softphone, then press Reconnect.", {
        id: deskPhone.sipId,
      });
  }
  setInterval(checkDeskPhone, config.deskPhoneCheckMs || 10000);
  async function startPhone() {
    if (ua) return;
    if (desk.deskPhone) return startDeskPhone();
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
    if (!(await phoneLibrary())) {
      unavailable(disconnectedMessage);
      return;
    }
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
      label($("phone-chip"), "Phone connecting");
    });
    phoneUa.on("registered", () => {
      if (ua !== phoneUa) return;
      phoneReady = true;
      phoneProblem = "";
      label($("phone-chip"), "Phone connected"); // plain words; the account is for support
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
        body: deskPhone
          ? t("Pick up phone {id} to answer.", { id: deskPhone.sipId })
          : t("Return to AlgoDial to answer."),
        tag: "algodial-incoming",
      });
  }
  function toggle(id, button) {
    $(id).hidden = !$(id).hidden;
    $(button).setAttribute("aria-expanded", String(!$(id).hidden));
    if (!$(id).hidden) $(id).querySelector("select,input,button")?.focus();
  }
  // The state button does what it says: "Go Ready" acts at once; "Go Not Ready" opens the reasons.
  $("state-menu-toggle").addEventListener("click", () => {
    if (stateAction().direct) $("ready").click();
    else toggle("state-menu", "state-menu-toggle");
  });
  $("ready").addEventListener("click", async () => {
    if (await command("mutation { setMyAvailability(ready: true) { status code message } }")) {
      $("state-menu").hidden = true;
      $("state-menu-toggle").setAttribute("aria-expanded", "false");
    }
  });
  $("empty-ready").addEventListener("click", () => $("ready").click());
  async function goNotReady(reason) {
    if (
      await command(
        "mutation R($reason: String) { setMyAvailability(ready: false, reason: $reason) { status code message } }",
        { reason },
      )
    ) {
      $("state-menu").hidden = true;
      $("state-menu-toggle").setAttribute("aria-expanded", "false");
    }
  }
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
    const wrapCall = state?.wrapupCall || "";
    if (
      !(await command(
        "mutation W($code: String!, $notes: String, $ready: Boolean, $reason: String) { finishMyWrapup(code: $code, notes: $notes, ready: $ready, reason: $reason) { status code message } }",
        { code, notes: $("notes").value, ready, reason },
      ))
    ) {
      wrapPending = false;
      renderCall();
    } else if (wrapCall && desk.wrapupCodes.length) {
      lastWrap = { call: wrapCall, code, at: Date.now() };
      fill($("last-wrap-select"), desk.wrapupCodes, "Choose a wrap-up code");
      $("last-wrap-select").value = code;
    }
  }
  document.querySelectorAll("[data-language]").forEach((button) =>
    button.addEventListener("click", () => {
      language = button.dataset.language;
      try {
        localStorage.setItem("algodial.language", language);
      } catch {
        /* the choice lasts this page only */
      }
      translatePage();
      if (desk) {
        const chosen = $("transfer-agent").value;
        fillTargets(desk.transferTargets);
        $("transfer-agent").value = chosen;
        renderQueues();
        renderCall();
      }
    }),
  );
  $("summary-wrong").addEventListener("click", () => aiCorrection("ux.ai_summary_wrong", "summary-wrong"));
  $("queue-wrong").addEventListener("click", () => aiCorrection("ux.ai_queue_wrong", "queue-wrong"));
  function aiCorrection(kind, id) {
    const callId = offer?.call || state?.call || state?.wrapupCall || "";
    signal(
      kind,
      callId,
      kind === "ux.ai_queue_wrong" ? { queue: offer?.queue || "" } : {},
      `${id}:${callId}`,
    );
    $(id).disabled = true;
    feedback(t("Thanks. Noted for whoever improves the AI."));
  }
  $("last-wrap-save").addEventListener("click", async () => {
    const code = $("last-wrap-select").value;
    if (!lastWrap || !code || code === lastWrap.code) return;
    const done = await command(
      "mutation C($call: String!, $code: String!) { changeMyWrapupCode(call: $call, code: $code) { status code message } }",
      { call: lastWrap.call, code },
    );
    if (done && lastWrap) {
      lastWrap.code = code;
      feedback(t("Wrap-up code changed"));
      renderCall();
    }
  });
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
      const key = event.key.toLowerCase();
      // Hang up needs Shift+E: a stray "e" must never end a call (UX_PRINCIPLES.md section 4).
      if (key === "e" && !event.shiftKey) return;
      id = {
        h: $("resume").hidden ? "hold" : "resume",
        m: "mute",
        t: "transfer-toggle",
        k: "keypad-toggle",
        e: "hangup",
        r: "state-menu-toggle",
      }[key];
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
    deskPhone = null;
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
