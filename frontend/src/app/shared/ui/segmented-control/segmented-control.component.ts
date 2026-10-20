import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  ElementRef,
  EventEmitter,
  HostBinding,
  Input,
  Output,
  QueryList,
  ViewChildren,
  forwardRef,
  inject,
} from "@angular/core";
import { ControlValueAccessor, NG_VALUE_ACCESSOR } from "@angular/forms";
import { MatIconModule } from "@angular/material/icon";

export interface SegmentedOption<T = unknown> {
  value: T;
  label: string;
  icon?: string;
  disabled?: boolean;
}

/** Single-choice radio group rendered as a pill track. Usable with forms or `[value]`/`(valueChange)`. */
@Component({
  selector: "ad-segmented-control",
  standalone: true,
  imports: [MatIconModule],
  templateUrl: "./segmented-control.component.html",
  styleUrl: "./segmented-control.component.scss",
  changeDetection: ChangeDetectionStrategy.OnPush,
  providers: [
    {
      provide: NG_VALUE_ACCESSOR,
      useExisting: forwardRef(() => SegmentedControlComponent),
      multi: true,
    },
  ],
})
export class SegmentedControlComponent implements ControlValueAccessor {
  @Input() options: SegmentedOption[] = [];
  @Input() value: unknown;
  @Input() size: "sm" | "md" = "md";
  @Input() ariaLabel = "";
  @Input() fullWidth = false;

  @Output() valueChange = new EventEmitter<unknown>();

  @ViewChildren("optionButton") private buttons!: QueryList<ElementRef<HTMLButtonElement>>;

  disabled = false;

  @HostBinding("style.display") get hostDisplay(): string | null {
    return this.fullWidth ? "block" : null;
  }

  private readonly cdr = inject(ChangeDetectorRef);
  private onChange: (value: unknown) => void = () => {};
  private onTouched: () => void = () => {};

  writeValue(value: unknown): void {
    this.value = value;
    this.cdr.markForCheck();
  }

  registerOnChange(fn: (value: unknown) => void): void {
    this.onChange = fn;
  }

  registerOnTouched(fn: () => void): void {
    this.onTouched = fn;
  }

  setDisabledState(isDisabled: boolean): void {
    this.disabled = isDisabled;
    this.cdr.markForCheck();
  }

  isChecked(option: SegmentedOption): boolean {
    return option.value === this.value;
  }

  isDisabled(option: SegmentedOption): boolean {
    return this.disabled || !!option.disabled;
  }

  /** Roving tabindex: the checked option, otherwise the first enabled one, is the tab stop. */
  tabIndexFor(index: number): number {
    const checkedIndex = this.options.findIndex((o) => this.isChecked(o) && !this.isDisabled(o));
    const stop = checkedIndex >= 0 ? checkedIndex : this.options.findIndex((o) => !this.isDisabled(o));
    return index === stop ? 0 : -1;
  }

  select(option: SegmentedOption): void {
    if (this.isDisabled(option) || this.isChecked(option)) {
      return;
    }
    this.value = option.value;
    this.onChange(option.value);
    this.valueChange.emit(option.value);
    this.cdr.markForCheck();
  }

  onKeydown(event: KeyboardEvent, index: number): void {
    let target = -1;
    switch (event.key) {
      case "ArrowRight":
      case "ArrowDown":
        target = this.nextEnabled(index, 1);
        break;
      case "ArrowLeft":
      case "ArrowUp":
        target = this.nextEnabled(index, -1);
        break;
      case "Home":
        target = this.nextEnabled(-1, 1);
        break;
      case "End":
        target = this.nextEnabled(this.options.length, -1);
        break;
      default:
        return;
    }
    event.preventDefault();
    if (target < 0) {
      return;
    }
    this.buttons.get(target)?.nativeElement.focus();
    this.select(this.options[target]);
  }

  onBlur(): void {
    this.onTouched();
  }

  private nextEnabled(from: number, step: 1 | -1): number {
    const count = this.options.length;
    for (let i = 1; i <= count; i++) {
      const candidate = (((from + step * i) % count) + count) % count;
      if (!this.isDisabled(this.options[candidate])) {
        return candidate;
      }
    }
    return -1;
  }
}
