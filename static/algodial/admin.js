/* Native form feedback and the skill-rule preview; Django validates every save. */
(() => {
  "use strict";
  const t = (value) => (window.gettext ? gettext(value) : value);
  function labelInlineDeletes() {
    document.querySelectorAll('input[name$="-DELETE"]').forEach((input) => {
      if (input.labels.length || input.hasAttribute("aria-label")) return;
      const record = input.closest("tr")?.querySelector(".original p")?.textContent.trim();
      input.setAttribute("aria-label", t("Delete row") + (record ? `: ${record}` : ""));
    });
  }
  function labelInlineFields() {
    labelInlineDeletes();
    document
      .querySelectorAll(
        ".inline-group .tabular input,.inline-group .tabular select,.inline-group .tabular textarea",
      )
      .forEach((input) => {
        if (
          input.type === "hidden" ||
          input.labels.length ||
          input.hasAttribute("aria-label") ||
          input.hasAttribute("aria-labelledby")
        )
          return;
        const cell = input.closest("td"),
          row = input.closest("tr");
        const title = cell?.closest("table")?.tHead?.rows[0]?.cells[cell.cellIndex]?.textContent.trim();
        const record = row?.querySelector(".original p")?.textContent.trim();
        input.setAttribute(
          "aria-label",
          (title || t("Field")) + (record ? `: ${record}` : ` ${row?.rowIndex || ""}`),
        );
      });
    document
      .querySelectorAll('.select2-container [role="combobox"],.select2-container [role="textbox"]')
      .forEach((input) => {
        if (input.hasAttribute("aria-label")) return;
        const source = input.closest(".select2-container")?.previousElementSibling;
        const label = source?.getAttribute("aria-label") || source?.labels?.[0]?.textContent.trim();
        if (label) input.setAttribute("aria-label", label);
      });
    document
      .querySelectorAll(".help-tooltip[aria-label]:not([role])")
      .forEach((icon) => icon.setAttribute("role", "img"));
    document.querySelectorAll(".select2-search__field").forEach((input) => {
      if (!input.hasAttribute("aria-label")) input.setAttribute("aria-label", t("Search choices"));
    });
  }
  labelInlineFields();
  new MutationObserver(labelInlineFields).observe(document.body, { childList: true, subtree: true });
  document.addEventListener("formset:added", labelInlineFields);
  document
    .querySelectorAll(".workspace-form input,.workspace-form select,.workspace-form textarea")
    .forEach((input) => {
      if (["hidden", "checkbox", "radio", "submit"].includes(input.type)) return;
      const message = document.createElement("span");
      message.className = "workspace-field-error";
      message.setAttribute("aria-live", "polite");
      input.after(message);
      input.addEventListener("change", () => {
        message.textContent = input.checkValidity() ? "" : input.validationMessage;
      });
    });
  const period = document.querySelector("[data-report-period]");
  if (period)
    period.addEventListener("change", () => {
      document.getElementById("custom").hidden = period.value !== "custom";
    });
  const source = document.getElementById("rule-preview-data");
  if (!source) return;
  const data = JSON.parse(source.textContent),
    timeline = document.getElementById("rule-timeline");
  function steps() {
    const grouped = new Map();
    for (const input of document.querySelectorAll('input[name$="-until_seconds"]')) {
      const prefix = input.name.replace(/until_seconds$/, ""),
        field = (name) => document.getElementsByName(prefix + name)[0];
      if (field("DELETE")?.checked) continue;
      const skill = field("skill")?.value,
        level = Number(field("min_level")?.value);
      if (!skill || !level) continue;
      const until = input.value === "" ? null : Number(input.value);
      if (!grouped.has(until)) grouped.set(until, {});
      grouped.get(until)[skill] = level;
    }
    return [...grouped].sort(([a], [b]) => (a ?? Infinity) - (b ?? Infinity));
  }
  function render() {
    const rules = steps();
    timeline.replaceChildren();
    let from = 0;
    for (const [until, needs] of rules) {
      const item = document.createElement("li"),
        label = document.createElement("strong"),
        text = document.createElement("span");
      label.textContent = until === null ? `${from} s / ${t("forever")}` : `${from} s / ${until} s`;
      text.textContent = Object.entries(needs)
        .map(([skill, level]) => `${skill} ≥ ${level}`)
        .join(", ");
      item.append(label, text);
      timeline.append(item);
      from = until ?? from;
    }
    const status = document.getElementById("rule-validation");
    const valid =
      rules.length &&
      rules.at(-1)[0] === null &&
      rules.every(
        ([until, needs]) =>
          (until === null || until > 0) &&
          needs.$language >= 4 &&
          Object.values(needs).every((level) => level >= 1 && level <= 10),
      );
    status.textContent = valid
      ? t("Every step keeps the caller’s language at level 4 or higher.")
      : t("Every step needs the caller’s language at level 4 or higher. The last step must last forever.");
    status.classList.toggle("errornote", !valid);
    if (!document.getElementById("rule-test-agent")) return;
    const agent = data.agents.find((a) => a.uid === document.getElementById("rule-test-agent").value);
    const language = document.getElementById("rule-test-language").value,
      wait = Math.max(0, Number(document.getElementById("rule-test-wait").value));
    const active = rules.find(([until]) => until === null || wait < until);
    const output = document.getElementById("rule-result");
    if (!valid || !agent || !language || !active) {
      output.textContent = t("Choose an agent and language to test the current steps.");
      return;
    }
    const queue = data.queues.find((q) => q.uid === document.getElementById("rule-test-queue").value);
    const missing = Object.entries(active[1]).filter(
      ([skill, level]) =>
        !(
          Object.hasOwn(agent.skills, skill === "$language" ? language : skill) &&
          agent.skills[skill === "$language" ? language : skill] >= level
        ),
    );
    if (queue && !queue.agents.includes(agent.uid))
      output.textContent = t("This agent is not a member of the selected queue.");
    else if (missing.length)
      output.textContent =
        t("Does not match these requirements: ") +
        missing.map(([skill, level]) => `${skill === "$language" ? language : skill} ≥ ${level}`).join(", ");
    else
      output.textContent = queue
        ? t("Skills and queue membership match this step.")
        : t("All required skills match this step.");
  }
  for (const agent of data.agents)
    document.getElementById("rule-test-agent").append(new Option(`${agent.number} ${agent.name}`, agent.uid));
  for (const language of data.languages)
    document.getElementById("rule-test-language").append(new Option(language, language));
  for (const queue of data.queues)
    document.getElementById("rule-test-queue").append(new Option(queue.name, queue.uid));
  document.addEventListener("input", render);
  document.addEventListener("change", render);
  document.addEventListener("formset:added", render);
  document.addEventListener("formset:removed", render);
  render();
})();
