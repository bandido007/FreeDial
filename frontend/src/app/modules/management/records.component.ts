import { ChangeDetectionStrategy, Component, inject, OnDestroy, OnInit } from "@angular/core";
import { CommonModule } from "@angular/common";
import { FormsModule } from "@angular/forms";
import { ActivatedRoute, Router } from "@angular/router";
import { Subscription } from "rxjs";
import { DataService, errorMessage } from "../../services/data.service";
import { AuthService } from "../../services/auth.service";
import { resourceIcon } from "../../shared/workspace-navigation";
import { formValues } from "../../shared/form-values";
import { Editor, Field, Inline, Listing, RecordRow } from "../../common";
import { FieldComponent } from "../../shared/field.component";
import { IconComponent } from "../../shared/icon.component";
import { ConfirmService } from "../../shared/confirm.service";
@Component({
  changeDetection: ChangeDetectionStrategy.Eager,
  selector: "ad-records",
  standalone: true,
  imports: [CommonModule, FormsModule, FieldComponent, IconComponent],
  templateUrl: "./records.component.html",
})
export class RecordsComponent implements OnInit, OnDestroy {
  private route = inject(ActivatedRoute);
  private router = inject(Router);
  private api = inject(DataService);
  private auth = inject(AuthService);
  private confirm = inject(ConfirmService);
  private subscription?: Subscription;
  private epoch = 0;
  private destroyed = false;
  listing: Listing | null = null;
  editor: Editor | null = null;
  key = "";
  search = "";
  page = 1;
  filters: Record<string, string> = {};
  sort = "";
  busy = false;
  saving = false;
  error = "";
  notice = "";
  dirty = false;
  selected = new Set<string>();
  action = "";
  dateFrom = "";
  dateTo = "";
  historyOpen = false;
  history: any[] = [];
  previewAgent = "";
  previewLanguage = "";
  previewWait = 0;
  previewQueue = "";
  password1 = "";
  password2 = "";
  passwordOpen = false;
  passwordError = "";
  passwordMode = "true";
  private searches = new Map<string, ReturnType<typeof setTimeout>>();
  filtersOpen = false;
  get filterCount() {
    return (
      Object.values(this.filters).filter(Boolean).length + Number(!!this.dateFrom) + Number(!!this.dateTo)
    );
  }
  get icon() {
    return this.listing ? resourceIcon(this.listing) : "grid";
  }
  async clearFilters() {
    this.filters = {};
    this.dateFrom = this.dateTo = "";
    this.page = 1;
    await this.load();
  }
  async clearSearch() {
    this.search = "";
    this.page = 1;
    await this.load();
  }
  ngOnInit() {
    this.subscription = this.route.paramMap.subscribe((params) => {
      this.key = params.get("key")!;
      this.search = "";
      this.page = 1;
      this.filters = {};
      this.dateFrom = this.dateTo = "";
      this.sort = "";
      this.filtersOpen = false;
      this.editor = null;
      this.dirty = false;
      this.load();
    });
  }
  async load() {
    const epoch = ++this.epoch;
    this.busy = true;
    this.error = "";
    try {
      const listing = await this.api.get<Listing>("records/" + this.key, {
        q: this.search,
        page: this.page,
        sort: this.sort,
        dateFrom: this.dateFrom,
        dateTo: this.dateTo,
        ...this.filters,
      });
      if (epoch !== this.epoch) return;
      this.listing = listing;
      this.selected.clear();
      const id = this.route.snapshot.queryParamMap.get("record");
      if (id && !this.editor) await this.open(id, false);
    } catch (e) {
      if (epoch === this.epoch) this.error = errorMessage(e);
    } finally {
      if (epoch === this.epoch) this.busy = false;
    }
  }
  async open(id: string, check = true) {
    if (check && !(await this.mayLeave())) return;
    this.dirty = false;
    this.notice = "";
    const epoch = this.epoch;
    try {
      const result = await this.api.get<Editor>("records/" + this.key + "/" + id);
      if (epoch !== this.epoch) return;
      this.editor = result;
      this.historyOpen = false;
      this.history = [];
      this.passwordOpen = false;
      this.password1 = this.password2 = "";
      this.router.navigate([], { relativeTo: this.route, queryParams: { record: id }, replaceUrl: true });
    } catch (e) {
      this.error = errorMessage(e);
    }
  }
  values() {
    return formValues(this.editor!);
  }

  async save() {
    if (!this.editor || this.saving) return;
    this.saving = true;
    this.error = "";
    this.notice = "";
    try {
      const values = this.values();
      let body: any = { values };
      if (Object.values(values).some((v) => v instanceof File)) {
        body = new FormData();
        for (const [key, value] of Object.entries(values)) {
          if (Array.isArray(value)) for (const item of value) body.append(key, item);
          else body.append(key, value instanceof File ? value : String(value ?? ""));
        }
      }
      const result: any = await this.api.post("records/" + this.key + "/" + (this.editor.id || "new"), body);
      if (this.destroyed) return;
      this.dirty = false;
      this.notice = [result.message, ...(result.messages || [])].join(" ");
      await this.open(result.id, false);
      this.notice = [result.message, ...(result.messages || [])].join(" ");
      await this.load();
    } catch (e: any) {
      if (e.status === 422) this.editor = e.error;
      this.error = e.status === 422 ? "Check the highlighted fields and try again." : errorMessage(e);
    } finally {
      this.saving = false;
    }
  }
  async showHistory() {
    if (!this.editor?.id) return;
    this.historyOpen = !this.historyOpen;
    if (this.historyOpen)
      try {
        this.history = await this.api.get<any[]>("history/" + this.key + "/" + this.editor.id);
      } catch (e) {
        this.error = errorMessage(e);
      }
  }
  async close() {
    if (await this.mayLeave()) {
      this.editor = null;
      this.dirty = false;
      await this.router.navigate([], { relativeTo: this.route, queryParams: {}, replaceUrl: true });
    }
  }
  async changePassword() {
    if (!this.editor?.id) return;
    this.passwordError = "";
    try {
      if (
        this.passwordMode === "false" &&
        !(await this.confirm.ask(
          "Disable password sign-in?",
          "This user will need another configured sign-in method.",
          "Disable sign-in",
        ))
      )
        return;
      const reply: any = await this.api.post("user-password/" + this.editor.id, {
        password1: this.password1,
        password2: this.password2,
        usable_password: this.passwordMode,
      });
      this.notice = reply.message;
      this.password1 = this.password2 = "";
      this.passwordOpen = false;
    } catch (e: any) {
      this.passwordError =
        e.status === 422
          ? Object.values(e.error.errors || {})
              .flat()
              .join(" ")
          : errorMessage(e);
    }
  }
  async remove() {
    if (!this.editor?.id) return;
    const counts = Object.entries(this.editor.deleteImpact?.counts || {})
      .map(([name, count]) => `${count} ${name}`)
      .join(", ");
    if (
      !(await this.confirm.ask(
        "Delete " + this.editor.label + "?",
        "This affects " + (counts || "this record") + ".",
        "Delete",
      ))
    )
      return;
    try {
      await this.api.delete("records/" + this.key + "/" + this.editor.id, { confirm: true });
      if (this.destroyed) return;
      this.editor = null;
      this.dirty = false;
      this.notice = "Deleted.";
      await this.router.navigate([], { relativeTo: this.route, queryParams: {}, replaceUrl: true });
      await this.load();
    } catch (e) {
      this.error = errorMessage(e);
    }
  }
  addInline(group: Inline) {
    const total = Number(group.management[group.prefix + "-TOTAL_FORMS"]);
    const row = structuredClone(group.empty);
    for (const field of row.fields) {
      field.key = field.key.replaceAll("__prefix__", String(total));
      field.id = field.id?.replaceAll("__prefix__", String(total));
      if (field.kind === "checkbox") field.value = false;
    }
    group.rows.push(row);
    group.management[group.prefix + "-TOTAL_FORMS"] = total + 1;
    this.dirty = true;
  }
  change() {
    this.dirty = true;
  }
  searchChoices(field: Field, query: string, group?: Inline) {
    clearTimeout(this.searches.get(field.key));
    this.searches.set(
      field.key,
      setTimeout(async () => {
        try {
          field.options = await this.api.get("choices/" + this.key, {
            record: this.editor?.id || "new",
            field: field.name,
            q: query,
            ...(group ? { inline: group.prefix } : {}),
          });
        } catch (e) {
          this.error = errorMessage(e);
        }
      }, 200),
    );
  }
  toggle(row: RecordRow, checked: boolean) {
    checked ? this.selected.add(row.id) : this.selected.delete(row.id);
  }
  async runAction() {
    if (!this.action || !this.selected.size) return;
    const label = this.listing?.actions.find((a) => a.name === this.action)?.label || this.action;
    if (!(await this.confirm.ask(label + "?", `Apply to ${this.selected.size} selected records?`, label)))
      return;
    try {
      await this.api.post("action/" + this.key, {
        action: this.action,
        ids: [...this.selected],
        confirm: true,
      });
      this.notice = "Action completed.";
      await this.load();
    } catch (e) {
      this.error = errorMessage(e);
    }
  }
  async move(page: number) {
    if (!(await this.mayLeave())) return;
    this.page = page;
    await this.load();
  }
  async mayLeave() {
    return (
      !this.auth.session() ||
      !this.dirty ||
      (await this.confirm.ask("Discard unsaved changes?", "Your edits have not been saved.", "Discard"))
    );
  }
  get ruleSteps() {
    if (this.key !== "queues.skillruleset" || !this.editor) return [];
    const steps = new Map<number, Record<string, number>>();
    for (const group of this.editor.inlines)
      for (const row of group.rows) {
        const value = (name: string) => row.fields.find((f) => f.name === name)?.value;
        if (value("DELETE") || !value("skill") || !value("min_level")) continue;
        const end =
          value("until_seconds") === "" || value("until_seconds") == null
            ? Infinity
            : Number(value("until_seconds"));
        steps.set(end, { ...steps.get(end), [value("skill")]: Number(value("min_level")) });
      }
    return [...steps]
      .sort(([a], [b]) => a - b)
      .map(([until, needs], index, all) => ({
        from: index ? all[index - 1][0] : 0,
        until: until === Infinity ? "Forever" : until + " s",
        needs: Object.entries(needs)
          .map(([skill, level]) => (skill === "$language" ? "Caller language" : skill) + " ≥ " + level)
          .join(", "),
        valid: needs["$language"] >= 4,
      }));
  }
  get ruleMatch() {
    const preview = this.editor?.rulePreview,
      agent = preview?.agents.find((a) => a.uid === this.previewAgent),
      language = this.previewLanguage;
    if (!agent || !language) return "Choose an agent and caller language to test.";
    const group = new Map<number, Record<string, number>>();
    for (const inline of this.editor!.inlines)
      for (const row of inline.rows) {
        const value = (name: string) => row.fields.find((f) => f.name === name)?.value;
        if (value("DELETE") || !value("skill") || !value("min_level")) continue;
        const until =
          value("until_seconds") === "" || value("until_seconds") == null
            ? Infinity
            : Number(value("until_seconds"));
        group.set(until, { ...group.get(until), [value("skill")]: Number(value("min_level")) });
      }
    const active = [...group].sort(([a], [b]) => a - b).find(([until]) => this.previewWait < until);
    if (!active) return "No step covers this wait. The last step must last forever.";
    if ((active[1]["$language"] || 0) < 4)
      return "Every step needs the caller’s language at level 4 or higher.";
    const queue = preview!.queues.find((q) => q.uid === this.previewQueue);
    if (queue && !queue.agents.includes(agent.uid))
      return "This agent is not a member of the selected queue.";
    const missing = Object.entries(active[1]).filter(
      ([skill, level]) => (agent.skills[skill === "$language" ? language : skill] || 0) < level,
    );
    return missing.length
      ? "Does not match: " +
          missing
            .map(([skill, level]) => (skill === "$language" ? language : skill) + " ≥ " + level)
            .join(", ")
      : "All required skills match this step.";
  }
  ngOnDestroy() {
    this.destroyed = true;
    this.epoch++;
    this.subscription?.unsubscribe();
    for (const timer of this.searches.values()) clearTimeout(timer);
  }
}
