import { ChangeDetectionStrategy, Component, Injectable, inject } from "@angular/core";
import { Dialog, DialogModule, DialogRef, DIALOG_DATA } from "@angular/cdk/dialog";
import { firstValueFrom } from "rxjs";
@Component({
  changeDetection: ChangeDetectionStrategy.Eager,
  standalone: true,
  imports: [DialogModule],
  template: `<section class="confirm-dialog">
    <p class="eyebrow">PLEASE CONFIRM</p>
    <h2>{{ data.title }}</h2>
    <p class="muted">{{ data.message }}</p>
    <div class="dialog-actions">
      <button class="button" (click)="ref.close(false)">Cancel</button
      ><button class="button danger-button" (click)="ref.close(true)">{{ data.action || "Confirm" }}</button>
    </div>
  </section>`,
})
class ConfirmDialog {
  data = inject(DIALOG_DATA);
  ref = inject(DialogRef<boolean>);
}
@Injectable({ providedIn: "root" })
export class ConfirmService {
  private dialog = inject(Dialog);
  async ask(title: string, message: string, action = "Confirm") {
    return !!(await firstValueFrom(
      this.dialog.open<boolean>(ConfirmDialog, {
        data: { title, message, action },
        ariaLabel: title,
        width: "440px",
        maxWidth: "calc(100vw - 32px)",
        autoFocus: "first-tabbable",
      }).closed,
    ));
  }
}
