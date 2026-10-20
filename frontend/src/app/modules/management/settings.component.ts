import { ChangeDetectionStrategy, Component, inject, OnInit } from "@angular/core";
import { CommonModule } from "@angular/common";
import { FormsModule } from "@angular/forms";
import { DataService, errorMessage } from "../../services/data.service";
import { ConfirmService } from "../../shared/confirm.service";
@Component({
  changeDetection: ChangeDetectionStrategy.Eager,
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `<section class="page-heading">
      <div>
        <p class="eyebrow">CONFIGURATION</p>
        <h1>System settings</h1>
        <p class="muted">Current values and defaults for your contact centre.</p>
      </div>
    </section>
    @if (error) {
      <p class="alert danger" role="alert">{{ error }}</p>
    }
    @if (notice) {
      <p class="alert success" role="status">{{ notice }}</p>
    }
    <div class="settings-workspace surface">
      <aside class="settings-list">
        <label>Search settings<input type="search" [(ngModel)]="search" placeholder="Search…" /></label>
        @for (row of filtered; track row.key) {
          <button [class.selected]="selected?.key === row.key" (click)="select(row)">
            <span>{{ row.name }}</span
            ><small>{{ row.group }}</small>
          </button>
        }
      </aside>
      <section class="setting-editor">
        @if (selected) {
          <p class="eyebrow">{{ selected.group }}</p>
          <h2>{{ selected.name }}</h2>
          <p class="muted">{{ selected.description }}</p>
          <form (ngSubmit)="save()">
            <label
              >{{ selected.name }}
              @switch (selected.type) {
                @case ("BOOLEAN") {
                  <select name="draft" [disabled]="!canChange" [(ngModel)]="draft">
                    <option value="true">Yes</option>
                    <option value="false">No</option>
                  </select>
                }
                @case ("CHOICE") {
                  <select name="draft" [disabled]="!canChange" [(ngModel)]="draft">
                    @for (choice of selected.choices; track choice) {
                      <option [value]="choice">{{ choice }}</option>
                    }
                  </select>
                }
                @case ("LIST") {
                  <textarea name="draft" [disabled]="!canChange" rows="4" [(ngModel)]="draft"></textarea>
                }
                @default {
                  <input
                    [type]="selected.type === 'INTEGER' ? 'number' : 'text'"
                    name="draft"
                    [disabled]="!canChange"
                    [(ngModel)]="draft"
                    [attr.min]="selected.minimum"
                    [attr.max]="selected.maximum"
                  />
                }
              }
            </label>
            <p class="help">
              Default: {{ show(selected.defaultValue) }} /
              {{ selected.isChanged ? "Custom value" : "Using default" }}
            </p>
            @if (canChange) {
              <div class="dialog-actions">
                <button class="button primary" [disabled]="busy">Save changes</button>
                @if (selected.isChanged) {
                  <button class="button" type="button" (click)="reset()" [disabled]="busy">
                    Restore default
                  </button>
                }
              </div>
            }
          </form>
        } @else {
          <div class="empty-state">
            <h2>Select a setting</h2>
            <p>Choose a setting from the list to view its value.</p>
          </div>
        }
      </section>
    </div>`,
})
export class SettingsComponent implements OnInit {
  private api = inject(DataService);
  private confirm = inject(ConfirmService);
  rows: any[] = [];
  selected: any;
  search = "";
  draft = "";
  busy = false;
  error = "";
  notice = "";
  canChange = true;
  ngOnInit() {
    this.load();
  }
  get filtered() {
    return this.rows.filter((r) =>
      (r.name + " " + r.group).toLowerCase().includes(this.search.toLowerCase()),
    );
  }
  show(value: any) {
    return Array.isArray(value) ? value.join(", ") : String(value ?? "");
  }
  select(row: any) {
    this.selected = row;
    this.draft = this.show(row.value);
  }
  async load() {
    try {
      const data: any = await this.api.get("settings");
      this.rows = data.rows.map((row: any) => ({
        ...row,
        defaultValue: row.default_value,
        isChanged: row.is_changed,
      }));
      this.canChange = data.canChange;
    } catch (e) {
      this.error = errorMessage(e);
    }
  }
  async save() {
    if (!this.selected) return;
    this.busy = true;
    this.error = "";
    try {
      const type = this.selected.type;
      let value: any = this.draft;
      if (type === "INTEGER") value = Number(this.draft);
      if (type === "BOOLEAN") value = this.draft === "true";
      if (type === "LIST")
        value = this.draft
          .split(",")
          .map((v) => v.trim())
          .filter(Boolean);
      const data = await this.api.post("settings", { key: this.selected.key, value });
      Object.assign(this.selected, data, {
        defaultValue: (data as any).default_value,
        isChanged: (data as any).is_changed,
      });
      this.select(this.selected);
      this.notice = "Setting saved.";
    } catch (e) {
      this.error = errorMessage(e);
    } finally {
      this.busy = false;
    }
  }
  async reset() {
    if (
      !(await this.confirm.ask(
        "Restore default?",
        "Restore the default for " + this.selected.name + "?",
        "Restore default",
      ))
    )
      return;
    try {
      const data = await this.api.post("settings", { key: this.selected.key, action: "reset" });
      Object.assign(this.selected, data, {
        defaultValue: (data as any).default_value,
        isChanged: (data as any).is_changed,
      });
      this.select(this.selected);
      this.notice = "Default restored.";
    } catch (e) {
      this.error = errorMessage(e);
    }
  }
}
