import {
  ChangeDetectionStrategy,
  ViewEncapsulation,
  AfterViewInit,
  Component,
  ElementRef,
  OnDestroy,
  ViewChild,
  inject,
} from "@angular/core";
import { DeskService } from "../../services/desk.service";
@Component({
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.Eager,
  standalone: true,
  selector: "ad-agent",
  templateUrl: "./agent.component.html",
  styleUrls: ["./agent.scss", "./desk-overrides.scss"],
})
export class AgentComponent implements AfterViewInit, OnDestroy {
  @ViewChild("runtime", { static: true }) root!: ElementRef<HTMLElement>;
  private desk = inject(DeskService);
  private lifetime = new AbortController();
  ngAfterViewInit() {
    this.desk.mount("agent", this.root.nativeElement, this.lifetime.signal).catch((error) => {
      this.root.nativeElement.textContent = error.message;
    });
  }
  ngOnDestroy() {
    this.lifetime.abort();
    this.desk.dispose("agent");
  }
}
@Component({
  encapsulation: ViewEncapsulation.None,
  changeDetection: ChangeDetectionStrategy.Eager,
  standalone: true,
  selector: "ad-supervisor",
  templateUrl: "./supervisor.component.html",
  styleUrls: ["./supervisor-base.scss", "./supervisor.scss", "./desk-overrides.scss"],
})
export class SupervisorComponent implements AfterViewInit, OnDestroy {
  @ViewChild("runtime", { static: true }) root!: ElementRef<HTMLElement>;
  private desk = inject(DeskService);
  private lifetime = new AbortController();
  ngAfterViewInit() {
    this.desk.mount("supervisor", this.root.nativeElement, this.lifetime.signal).catch((error) => {
      this.root.nativeElement.textContent = error.message;
    });
  }
  ngOnDestroy() {
    this.lifetime.abort();
    this.desk.dispose("supervisor");
  }
}
