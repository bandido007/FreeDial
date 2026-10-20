import {
  ChangeDetectionStrategy,
  Component,
  Input,
  Output,
  EventEmitter,
  inject,
  OnDestroy,
  OnChanges,
  SimpleChanges,
} from "@angular/core";
import { CommonModule } from "@angular/common";
import { FormsModule } from "@angular/forms";
import { Field } from "../common";
import { DataService, errorMessage } from "../services/data.service";
@Component({
  changeDetection: ChangeDetectionStrategy.Eager,
  selector: "ad-field",
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: ` <div
    class="field"
    [class.checkbox-field]="field.kind === 'checkbox'"
    [class.has-error]="field.errors.length"
    [hidden]="field.hidden"
  >
    @if (field.kind === "readonly" || field.readonly) {
      <span class="field-label">{{ field.label }}</span>
      <div class="readonly-value">{{ field.value === null || field.value === "" ? "—" : field.value }}</div>
      @if (field.media) {
        <button class="button small" type="button" (click)="play()">
          {{ audio ? "Reload recording" : "Load recording" }}
        </button>
        @if (audio) {
          <audio [src]="audio" controls preload="none"></audio>
        }
      }
    } @else if (field.kind === "checkbox") {
      <label [for]="field.id"
        ><input
          type="checkbox"
          [id]="field.id"
          [name]="field.key"
          [attr.aria-invalid]="field.errors.length ? 'true' : null"
          [attr.aria-describedby]="
            field.id && (field.help || field.errors.length) ? field.id + '-description' : null
          "
          [(ngModel)]="field.value"
          (ngModelChange)="changed.emit()"
        />{{ field.label }}</label
      >
    } @else {
      <label [for]="field.id"
        >{{ field.label }}
        @if (field.required) {
          <span class="required" aria-hidden="true">*</span>
        }
      </label>
      @if (field.searchable && ((field.options?.length || 0) >= 8 || searchText)) {
        <input
          class="choice-search"
          type="search"
          [attr.aria-label]="'Search ' + field.label"
          placeholder="Search choices…"
          [value]="searchText"
          (input)="searchText = $any($event.target).value; search.emit(searchText)"
        />
      }
      @switch (field.kind) {
        @case ("select") {
          <select
            [id]="field.id"
            [name]="field.key"
            [attr.aria-invalid]="field.errors.length ? 'true' : null"
            [attr.aria-describedby]="
              field.id && (field.help || field.errors.length) ? field.id + '-description' : null
            "
            [(ngModel)]="field.value"
            (ngModelChange)="changed.emit()"
            [required]="!!field.required"
          >
            <option value="">Choose…</option>
            @for (option of field.options; track option.value) {
              <option [value]="option.value">{{ option.label }}</option>
            }
          </select>
        }
        @case ("multiselect") {
          <select
            multiple
            [id]="field.id"
            [name]="field.key"
            [attr.aria-invalid]="field.errors.length ? 'true' : null"
            [attr.aria-describedby]="
              field.id && (field.help || field.errors.length) ? field.id + '-description' : null
            "
            [(ngModel)]="field.value"
            (ngModelChange)="changed.emit()"
            [required]="!!field.required"
          >
            @for (option of field.options; track option.value) {
              <option [value]="option.value">{{ option.label }}</option>
            }
          </select>
        }
        @case ("textarea") {
          <textarea
            rows="4"
            [id]="field.id"
            [name]="field.key"
            [attr.aria-invalid]="field.errors.length ? 'true' : null"
            [attr.aria-describedby]="
              field.id && (field.help || field.errors.length) ? field.id + '-description' : null
            "
            [(ngModel)]="field.value"
            (ngModelChange)="changed.emit()"
            [required]="!!field.required"
          ></textarea>
        }
        @case ("file") {
          <input
            type="file"
            [id]="field.id"
            [name]="field.key"
            [attr.aria-invalid]="field.errors.length ? 'true' : null"
            [attr.aria-describedby]="
              field.id && (field.help || field.errors.length) ? field.id + '-description' : null
            "
            (change)="fileChanged($event)"
          />
        }
        @default {
          <input
            [type]="field.kind === 'datetime' ? 'datetime-local' : field.kind"
            [id]="field.id"
            [name]="field.key"
            [attr.aria-invalid]="field.errors.length ? 'true' : null"
            [attr.aria-describedby]="
              field.id && (field.help || field.errors.length) ? field.id + '-description' : null
            "
            [(ngModel)]="field.value"
            (ngModelChange)="changed.emit()"
            [required]="!!field.required"
            [attr.min]="field.min"
            [attr.max]="field.max"
            [attr.maxlength]="field.maxLength"
            [autocomplete]="field.kind === 'password' ? 'new-password' : 'off'"
          />
        }
      }
    }
    <div [attr.id]="field.id ? field.id + '-description' : null">
      @if (field.help) {
        <small class="help">{{ field.help }}</small>
      }
      @for (error of field.errors; track $index) {
        <small class="field-error" role="alert">{{ error }}</small>
      }
    </div>
    @if (error) {
      <small class="field-error" role="alert">{{ error }}</small>
    }
  </div>`,
  styles: [
    `
      .field {
        margin-bottom: 20px;
      }
      .field > label,
      .field-label {
        display: block;
        font-size: 14px;
        font-weight: 600;
        margin-bottom: 7px;
      }
      .field input:not([type="checkbox"]),
      .field select,
      .field textarea {
        width: 100%;
      }
      .help {
        display: block;
        margin-top: 6px;
        line-height: 1.5;
      }
      .readonly-value {
        white-space: pre-wrap;
        overflow-wrap: anywhere;
        max-height: 420px;
        overflow: auto;
        font-size: 14px;
        color: var(--color-fg-secondary);
      }
      .choice-search {
        font-size: 12px;
        min-height: 32px;
        margin-bottom: 7px;
        background: var(--color-surface-muted);
      }
      .checkbox-field {
        border: 1px solid var(--color-line);
        border-radius: 9px;
        padding: 9px 11px;
        background: var(--color-surface-muted);
      }
      .checkbox-field label {
        display: flex;
        align-items: center;
        gap: 10px;
      }
      .choice-search {
        margin-bottom: 6px;
      }
      .has-error input,
      .has-error select {
        border-color: var(--color-danger);
      }
      audio {
        display: block;
        max-width: 100%;
        margin-top: 12px;
      }
      select[multiple] {
        min-height: 140px;
      }
    `,
  ],
})
export class FieldComponent implements OnDestroy, OnChanges {
  searchText = "";
  @Input({ required: true }) field!: Field;
  @Output() changed = new EventEmitter<void>();
  @Output() search = new EventEmitter<string>();
  private api = inject(DataService);
  private mediaVersion = 0;
  audio = "";
  error = "";
  ngOnChanges(changes: SimpleChanges) {
    if (changes["field"]) {
      this.searchText = "";
      this.error = "";
      this.mediaVersion++;
      if (this.audio) URL.revokeObjectURL(this.audio);
      this.audio = "";
    }
  }
  fileChanged(event: Event) {
    this.field.value = (event.target as HTMLInputElement).files?.[0] || null;
    this.changed.emit();
  }
  async play() {
    this.error = "";
    const version = ++this.mediaVersion;
    try {
      if (this.audio) URL.revokeObjectURL(this.audio);
      this.audio = "";
      const url = await this.api.media(this.field.media!);
      if (version !== this.mediaVersion) {
        URL.revokeObjectURL(url);
        return;
      }
      this.audio = url;
    } catch (e) {
      this.error = errorMessage(e);
    }
  }
  ngOnDestroy() {
    this.mediaVersion++;
    if (this.audio) URL.revokeObjectURL(this.audio);
  }
}
