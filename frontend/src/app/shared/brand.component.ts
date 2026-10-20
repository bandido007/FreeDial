import { ChangeDetectionStrategy, Component } from "@angular/core";
@Component({
  changeDetection: ChangeDetectionStrategy.Eager,
  selector: "ad-brand",
  standalone: true,
  template: `<svg class="ui-logo-symbol" viewBox="0 0 32 32" aria-hidden="true" focusable="false">
      <path class="ui-logo-ring" d="M17.910 5.167A11 11 0 1 0 26.833 14.090" />
      <line class="ui-logo-needle" x1="16" y1="16" x2="23.778" y2="8.222" />
      <circle class="ui-logo-dot" cx="16" cy="16" r="3.5" />
      <circle class="ui-logo-dot" cx="23.778" cy="8.222" r="4.5" /></svg
    ><strong>AlgoDial</strong>`,
  styles: [
    `
      :host {
        display: inline-flex;
        align-items: center;
        gap: 10px;
        white-space: nowrap;
      }
      .ui-logo-ring,
      .ui-logo-needle {
        fill: none;
        stroke: currentColor;
        stroke-width: 3;
        stroke-linecap: round;
      }
      .ui-logo-dot {
        fill: currentColor;
      }
      svg {
        width: 32px;
        height: 32px;
        color: var(--color-brand);
      }
      strong {
        font-size: 22px;
        letter-spacing: -0.6px;
        color: var(--color-fg);
        font-weight: 750;
      }
    `,
  ],
})
export class BrandComponent {}
