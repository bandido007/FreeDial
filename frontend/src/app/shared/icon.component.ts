import { ChangeDetectionStrategy, Component, Input } from "@angular/core";
@Component({
  changeDetection: ChangeDetectionStrategy.Eager,
  selector: "ad-icon",
  standalone: true,
  template: `<svg
    aria-hidden="true"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.7"
    stroke-linecap="round"
    stroke-linejoin="round"
  >
    <path [attr.d]="paths[name] || paths['grid']" />
  </svg>`,
  styles: [
    `
      :host {
        display: inline-flex;
        width: 20px;
        height: 20px;
        flex: none;
      }
      svg {
        width: 100%;
        height: 100%;
      }
    `,
  ],
})
export class IconComponent {
  @Input() name = "grid";
  readonly paths: Record<string, string> = {
    grid: "M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z",
    phone: "M8 3H4a1 1 0 0 0-1 1c0 9 8 17 17 17a1 1 0 0 0 1-1v-4l-5-2-2 3a14 14 0 0 1-7-7l3-2Z",
    users:
      "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M20 21v-2a4 4 0 0 0-3-4 M16 3a4 4 0 0 1 0 8",
    search: "M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
    plus: "M12 5v14 M5 12h14",
    sun: "M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2 M16 12a4 4 0 1 1-8 0 4 4 0 0 1 8 0",
    moon: "M20.9 13A9 9 0 0 1 11 3.1 9 9 0 1 0 20.9 13Z",
    route:
      "M6 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6 M18 15a3 3 0 1 0 0 6 3 3 0 0 0 0-6 M9 6h6a4 4 0 0 1 0 8H9a3 3 0 0 0 0 6h6",
    shield: "M12 3 3 7v5c0 5 9 10 9 10s9-5 9-10V7Z M8 12l3 3 5-6",
    wave: "M3 10v4 M7 6v12 M12 3v18 M17 7v10 M21 10v4",
    logout: "M9 3H4v18h5 M12 12h10 M18 8l4 4-4 4",
    chevron: "M9 5l7 7-7 7",
    menu: "M3 6h18 M3 12h18 M3 18h18",
    check: "M5 12l4 4L19 6",
    settings: "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M9 3h6l1 4 4 2v6l-4 2-1 4H9l-1-4-4-2V9l4-2Z",
    report: "M4 3h16v18H4z M8 16v-3 M12 16V7 M16 16v-6",
    history: "M3 11a9 9 0 1 1 2 7 M3 3v8h8 M12 7v5l3 2",
    close: "M6 6l12 12 M6 18L18 6",
    arrow: "M19 12H5 M11 6l-6 6 6 6",
  };
}
