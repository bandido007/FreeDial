import { Injectable, signal } from "@angular/core";
@Injectable({ providedIn: "root" })
export class ThemeService {
  readonly dark = signal(false);
  constructor() {
    const saved = localStorage.getItem("algodial.angular.theme");
    this.apply(saved ? saved === "dark" : matchMedia("(prefers-color-scheme:dark)").matches);
    matchMedia("(prefers-color-scheme:dark)").addEventListener("change", (event) => {
      if (!localStorage.getItem("algodial.angular.theme")) this.apply(event.matches);
    });
  }
  private apply(dark: boolean) {
    this.dark.set(dark);
    document.documentElement.dataset["theme"] = dark ? "dark" : "light";
    sessionStorage.setItem("algodial.theme", JSON.stringify(dark ? "dark" : "light"));
  }
  toggle() {
    this.apply(!this.dark());
    localStorage.setItem("algodial.angular.theme", this.dark() ? "dark" : "light");
    sessionStorage.setItem("algodial.theme", JSON.stringify(this.dark() ? "dark" : "light"));
  }
}
