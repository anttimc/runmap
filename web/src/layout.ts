import type { PaddingOptions } from "maplibre-gl";

import { element } from "./dom";

/** Frame routes beside the visible tool panel or above the mobile sheet. */
export function mapPadding(): PaddingOptions {
  const panel = document.querySelector<HTMLElement>(".controls")!.getBoundingClientRect();
  const legend = document.querySelector<HTMLElement>(".map-legend")!.getBoundingClientRect();
  const narrow = window.matchMedia("(max-width: 600px)").matches;
  return {
    left: narrow ? 24 : panel.right + 24,
    right: narrow ? 24 : 48,
    top: legend.height ? legend.bottom + 16 : 40,
    bottom: narrow ? window.innerHeight - panel.top + 24 : 48,
  };
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
      dialog.showModal();
    });
  }
  element("settings-close").addEventListener("click", () => dialog.close());
  dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const bounds = dialog.getBoundingClientRect();
    if (
      event.clientX < bounds.left ||
      event.clientX > bounds.right ||
      event.clientY < bounds.top ||
      event.clientY > bounds.bottom
    )
      dialog.close();
  });

  const sections = document.querySelectorAll<HTMLButtonElement>("[data-section]");
  for (const button of sections) {
    button.addEventListener("click", () => {
      const open = button.getAttribute("aria-expanded") !== "true";
      for (const other of sections) {
        const expanded = other === button && open;
        other.setAttribute("aria-expanded", String(expanded));
        element(other.dataset.section!).hidden = !expanded;
      }
      document.dispatchEvent(new Event("sectionchange"));
    });
  }
  new ResizeObserver(() => {
    document.documentElement.style.setProperty(
      "--tool-height",
      `${panel.getBoundingClientRect().height}px`,
    );
  }).observe(panel);
}
