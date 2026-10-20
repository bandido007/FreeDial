export function installDeskUi(){
  const paths = {
    phone: "M8 3H4a1 1 0 0 0-1 1c0 9 8 17 17 17a1 1 0 0 0 1-1v-4l-5-2-2 3a14 14 0 0 1-7-7l3-2Z",
    check: "M9 12l2 2 4-5 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
    pause: "M9 8v8 M15 8v8 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
    minus: "M8 12h8 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
    clipboard: "M9 4H5v17h14V4h-4 M9 2h6v5H9Z M8 12h8 M8 16h5",
    ring: "M8 6H5v4 M16 6h3v4 M8 14c2-3 6-3 8 0l3-1 2 4-4 2-2-3H9l-2 3-4-2 2-4Z",
    plug: "M7 3v5 M17 3v5 M5 8h14v4a7 7 0 0 1-7 7v3 M3 3l18 18",
    warning: "M12 3 2 21h20Z M12 9v5 M12 17h.01",
  };
  window.AlgoDialUI = Object.freeze({
    icon(name) {
      const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
      svg.setAttribute("viewBox", "0 0 24 24");
      svg.setAttribute("aria-hidden", "true");
      svg.classList.add("ui-icon");
      const path = document.createElementNS(svg.namespaceURI, "path");
      path.setAttribute("d", paths[name] || paths.phone);
      svg.append(path);
      return svg;
    },
    duration(seconds) {
      const n = Math.max(0, Math.floor(Number(seconds) || 0));
      return `${String(Math.floor(n / 60)).padStart(2, "0")}:${String(n % 60).padStart(2, "0")}`;
    },
  });

}
