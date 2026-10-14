import type { PaddingOptions } from "maplibre-gl";

const element = <T extends HTMLElement = HTMLElement>(id: string): T => document.getElementById(id) as T;

/** Frame routes beside the visible tool panel or above the mobile sheet. */
export function mapPadding(): PaddingOptions {
  const panel = document.querySelector<HTMLElement>(".controls")!.getBoundingClientRect();
  const legend = document.querySelector<HTMLElement>(".map-legend")!.getBoundingClientRect();
  const narrow = window.matchMedia("(max-width: 600px)").matches;
  return { left: narrow ? 24 : panel.right + 24, right: narrow ? 24 : 48,
    top: legend.height ? legend.bottom + 16 : 40,
    bottom: narrow ? window.innerHeight - panel.top + 24 : 48 };
}

export function startLayout(): void {
  const panel = document.querySelector<HTMLElement>(".controls")!;
  const toggle = element<HTMLButtonElement>("panel-toggle");
  toggle.addEventListener("click", () => {
    const collapsed = panel.classList.toggle("is-collapsed");
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.textContent = collapsed ? "Expand" : "Collapse";
  });

  const dialog = element<HTMLDialogElement>("settings-dialog");
  for (const opener of document.querySelectorAll<HTMLButtonElement>("[data-settings]")) {
    opener.addEventListener("click", () => {
      const paths = opener.dataset.settings === "paths";
      element("settings-paths").hidden = !paths;
      element("settings-map").hidden = paths;
      element("settings-title").textContent = paths ? "Path settings" : "Map settings";
      dialog.showModal();
    });
  }
  element("settings-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", event => {
    if (event.target !== dialog) return;
    const bounds = dialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right
      || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
  });

  const tabs = [...document.querySelectorAll<HTMLButtonElement>('[role="tab"]')];
  for (const tab of tabs) tab.addEventListener("keydown", event => {
    const index = tabs.indexOf(tab);
    const next = event.key === "ArrowRight" ? (index + 1) % tabs.length
      : event.key === "ArrowLeft" ? (index + tabs.length - 1) % tabs.length
      : event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : undefined;
    if (next === undefined || tabs[next].disabled) return;
    event.preventDefault(); tabs[next].focus(); tabs[next].click();
  });

  const opacity = element<HTMLInputElement>("opacity");
  const updateLegend = (): void => { element("terrain-legend").hidden = Number(opacity.value) === 0; };
  opacity.addEventListener("input", updateLegend);
  updateLegend();
  new ResizeObserver(() => {
    document.documentElement.style.setProperty("--tool-height", `${panel.getBoundingClientRect().height}px`);
  }).observe(panel);
}
