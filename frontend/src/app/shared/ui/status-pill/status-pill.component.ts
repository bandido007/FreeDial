import { ChangeDetectionStrategy, Component, Input } from "@angular/core";

export type StatusPillTone = "success" | "caution" | "danger" | "neutral" | "info";

@Component({
  selector: "ad-status-pill",
  templateUrl: "./status-pill.component.html",
  styleUrl: "./status-pill.component.scss",
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class StatusPillComponent {
  @Input() tone: StatusPillTone = "neutral";
  @Input() dot = true;
}
