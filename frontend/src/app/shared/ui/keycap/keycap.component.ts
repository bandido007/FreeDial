import { ChangeDetectionStrategy, Component } from "@angular/core";

@Component({
  selector: "ad-keycap",
  templateUrl: "./keycap.component.html",
  styleUrl: "./keycap.component.scss",
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class KeycapComponent {}
