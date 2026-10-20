import { ChangeDetectionStrategy, Component, HostBinding, Input } from "@angular/core";

export type SkeletonVariant = "text" | "title" | "block" | "circle" | "pill";

@Component({
  selector: "ad-skeleton",
  templateUrl: "./skeleton.component.html",
  styleUrl: "./skeleton.component.scss",
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class SkeletonComponent {
  @Input() variant: SkeletonVariant = "text";
  @Input() width = "100%";
  @Input() height?: string;
  /** Number of stacked lines (variants text/title only). The last line is shortened when more than one. */
  @Input() lines = 1;
  @Input() radius?: string;

  @HostBinding("attr.aria-hidden") readonly ariaHidden = "true";

  get lineList(): number[] {
    const count = Math.max(1, Math.floor(this.lines || 1));
    return Array.from({ length: count }, (_, i) => i);
  }

  get resolvedHeight(): string {
    if (this.height) {
      return this.height;
    }
    switch (this.variant) {
      case "title":
        return "22px";
      case "block":
        return "80px";
      case "circle":
        return this.width;
      case "pill":
        return "20px";
      default:
        return "12px";
    }
  }

  get resolvedRadius(): string | null {
    return this.radius ?? null;
  }

  lineWidth(index: number): string {
    return this.lineList.length > 1 && index === this.lineList.length - 1 ? "60%" : this.width;
  }
}
