import {
  ChangeDetectionStrategy,
  ChangeDetectorRef,
  Component,
  inject,
  Input,
  OnChanges,
  OnDestroy,
  OnInit,
} from "@angular/core";
import { TranslateModule } from "@ngx-translate/core";

export type SpinnerSize = 16 | 24 | 40;
export type SpinnerTone = "brand" | "neutral" | "inverse";

@Component({
  selector: "ad-spinner",
  templateUrl: "./spinner.component.html",
  styleUrl: "./spinner.component.scss",
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [TranslateModule],
})
export class SpinnerComponent implements OnInit, OnChanges, OnDestroy {
  @Input() size: SpinnerSize = 24;
  @Input() label = "loading";
  /** Milliseconds to wait before rendering the ring (avoids flicker on fast loads). 0 renders immediately. */
  @Input() delay = 300;
  @Input() tone: SpinnerTone = "brand";

  visible = false;

  private readonly cdr = inject(ChangeDetectorRef);
  private timer: ReturnType<typeof setTimeout> | null = null;

  ngOnInit(): void {
    this.scheduleReveal();
  }

  ngOnChanges(): void {
    // Re-evaluate only while still hidden, so a late delay change is honoured.
    if (!this.visible) {
      this.scheduleReveal();
    }
  }

  ngOnDestroy(): void {
    this.clearTimer();
  }

  private scheduleReveal(): void {
    this.clearTimer();
    if (!this.delay || this.delay <= 0) {
      this.visible = true;
      return;
    }
    this.timer = setTimeout(() => {
      this.timer = null;
      this.visible = true;
      this.cdr.markForCheck();
    }, this.delay);
  }

  private clearTimer(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }
}
