import { EXPORT_REPORT } from "../../store/api.graphql";
import { ChangeDetectionStrategy, Component, inject, OnInit } from "@angular/core";
import { CommonModule } from "@angular/common";
import { FormsModule } from "@angular/forms";
import { DataService, errorMessage } from "../../services/data.service";
@Component({
  changeDetection: ChangeDetectionStrategy.Eager,
  standalone: true,
  imports: [CommonModule, FormsModule],
  template: `<section class="page-heading">
      <div>
        <p class="eyebrow">CONTACT CENTRE</p>
        <h1>Reports</h1>
        <p class="muted">Calls, agent time, AI handoffs, and workspace experience.</p>
      </div>
    </section>
    <form class="surface report-controls" (ngSubmit)="load()">
      <label
        >Period<select name="period" [(ngModel)]="period">
          <option value="today">Today</option>
          <option value="yesterday">Yesterday</option>
          <option value="last_7_days">Last 7 days</option>
          <option value="last_30_days">Last 30 days</option>
          <option value="custom">Custom dates</option>
        </select></label
      >
      @if (period === "custom") {
        <label>From<input type="date" name="since" [(ngModel)]="since" required /></label
        ><label>To<input type="date" name="until" [(ngModel)]="until" required /></label>
      }
      <button class="button primary" type="submit" [disabled]="busy">
        {{ busy ? "Loading…" : "Show report" }}
      </button>
    </form>
    @if (error) {
      <p class="alert danger" role="alert">{{ error }}</p>
    }
    @if (report) {
      <p class="help">{{ report.since | date: "medium" }} — {{ report.until | date: "medium" }}</p>
      @for (table of report.tables; track table.key) {
        <section class="surface report-section">
          <header>
            <h2>{{ table.title }}</h2>
            <div class="export-actions">
              @for (format of formats; track format.value) {
                <button
                  class="button small"
                  [disabled]="exporting"
                  (click)="download(table.key, format.value)"
                >
                  {{ format.label }}
                </button>
              }
            </div>
          </header>
          <div class="table-scroll" tabindex="0" [attr.aria-label]="table.title">
            <table>
              <thead>
                <tr>
                  @for (column of table.columns; track $index) {
                    <th scope="col">{{ column }}</th>
                  }
                </tr>
              </thead>
              <tbody>
                @for (row of table.rows; track $index) {
                  <tr>
                    @for (cell of row; track $index) {
                      <td>{{ cell }}</td>
                    }
                  </tr>
                } @empty {
                  <tr>
                    <td [attr.colspan]="table.columns.length">
                      <div class="empty-state">Nothing in this period.</div>
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        </section>
      }
    }`,
})
export class ReportsComponent implements OnInit {
  private api = inject(DataService);
  period = "today";
  since = "";
  until = "";
  busy = false;
  exporting = false;
  error = "";
  report: any;
  formats = [
    { value: "PDF", label: "PDF" },
    { value: "EXCEL", label: "Excel" },
    { value: "CSV", label: "CSV" },
  ];
  ngOnInit() {
    this.load();
  }
  params() {
    return {
      period: this.period,
      ...(this.period === "custom" ? { since: this.since, until: this.until } : {}),
    };
  }
  async load() {
    this.busy = true;
    this.error = "";
    try {
      this.report = await this.api.get("reports", this.params());
    } catch (e) {
      this.error = errorMessage(e);
    } finally {
      this.busy = false;
    }
  }
  async download(section: string, format: string) {
    this.exporting = true;
    this.error = "";
    try {
      const data = await this.api.graph(EXPORT_REPORT, { section, format, ...this.params() });
      await this.api.download(data);
    } catch (e) {
      this.error = errorMessage(e);
    } finally {
      this.exporting = false;
    }
  }
}
