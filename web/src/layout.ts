/** Manage responsive panel layout, settings dialogs, and contextual help. */
import type { PaddingOptions } from "maplibre-gl";

import { element } from "./dom.ts";

/** Frame routes beside the visible tool panel or above the mobile sheet. */
export function mapPadding(): PaddingOptions {
  const panel = document.querySelector<HTMLElement>(".controls")!.getBoundingClientRect();
  const legend = document.querySelector<HTMLElement>(".map-legend")!.getBoundingClientRect();
  const results = element("run-result-dock").getBoundingClientRect();
  const narrow = window.matchMedia("(max-width: 600px)").matches;
  const top = narrow
    ? Math.max(
        40,
        results.height ? results.bottom + 16 : 0,
        legend.height ? Math.min(legend.bottom + 16, panel.top / 2) : 0,
      )
    : 40;
  const bottom = narrow
    ? window.innerHeight - panel.top + 24
    : Math.max(48, results.height ? window.innerHeight - results.top + 16 : 0);
  // Leave a usable map area even when both panels fill a short viewport.
  const scale = Math.min(1, Math.max(0, window.innerHeight - 80) / (top + bottom));
  return {
    left: narrow ? 24 : panel.right + 24,
    right: narrow ? 24 : window.innerWidth - legend.left + 24,
    top: top * scale,
    bottom: bottom * scale,
  };
}

export function startLayout(): void {
  startTooltips();
  if (window.matchMedia("(max-width: 600px)").matches)
    document.querySelector<HTMLDetailsElement>(".map-layer-panel")!.open = false;
  const panel = document.querySelector<HTMLElement>(".controls")!;
  const toggle = element<HTMLButtonElement>("panel-toggle");
  toggle.addEventListener("click", () => {
    const collapsed = panel.classList.toggle("is-collapsed");
    toggle.setAttribute("aria-expanded", String(!collapsed));
    toggle.textContent = collapsed ? "Expand" : "Collapse";
  });

  connectDialog("settings-dialog", "[data-settings]", "settings-close");
  connectDialog("run-settings-dialog", "[data-run-settings]", "run-settings-close");

  const sections = document.querySelectorAll<HTMLButtonElement>("[data-section]");
  for (const button of sections) {
    button.addEventListener("click", () => {
      const open = button.getAttribute("aria-expanded") !== "true";
      for (const other of sections) {
        const expanded = other === button && open;
        other.setAttribute("aria-expanded", String(expanded));
        element(other.dataset.section!).hidden = !expanded;
      }
    });
  }
  new ResizeObserver(() => {
    document.documentElement.style.setProperty(
      "--tool-height",
      `${panel.getBoundingClientRect().height}px`,
    );
  }).observe(panel);
}

/** Native modal dialogs keep keyboard focus inside the active settings menu. */
function connectDialog(id: string, selector: string, closeId: string): void {
  const dialog = element<HTMLDialogElement>(id);
  for (const opener of document.querySelectorAll<HTMLButtonElement>(selector)) {
    opener.addEventListener("click", () => dialog.showModal());
  }
  element(closeId).addEventListener("click", () => dialog.close());
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
}

/** Keep help available to mouse, keyboard and touch without expanding the panels. */
function startTooltips(): void {
  const tips = document.querySelectorAll<HTMLElement>(".help-tip");
  const close = (): void => {
    for (const tip of tips) {
      tip.querySelector<HTMLElement>(".help-content")!.hidePopover();
      tip.querySelector("button")!.setAttribute("aria-expanded", "false");
    }
  };
  tips.forEach((tip, index) => {
    const trigger = tip.querySelector<HTMLButtonElement>("button")!;
    const content = tip.querySelector<HTMLElement>(".help-content")!;
    content.popover = "manual";
    content.id ||= `help-${index}`;
    trigger.setAttribute("aria-describedby", content.id);
    if (trigger.getAttribute("aria-label") === "More information") {
      const context = tip.closest(".control-label, .layer-section, .settings-content section");
      const label = context?.querySelector("label, .section-toggle, h3")?.textContent?.trim();
      if (label) trigger.setAttribute("aria-label", `About ${label}`);
    }
    const show = (): void => {
      close();
      content.showPopover();
      trigger.setAttribute("aria-expanded", "true");
      const anchor = trigger.getBoundingClientRect();
      const bounds = content.getBoundingClientRect();
      content.style.left = `${Math.max(8, Math.min(anchor.left, innerWidth - bounds.width - 8))}px`;
      content.style.top = `${Math.max(8, Math.min(anchor.bottom + 6, innerHeight - bounds.height - 8))}px`;
    };
    trigger.addEventListener("pointerenter", show);
    trigger.addEventListener("focus", show);
    trigger.addEventListener("click", show);
    tip.addEventListener("pointerleave", () => {
      if (!tip.contains(document.activeElement)) close();
    });
    tip.addEventListener("focusout", (event) => {
      if (!tip.contains(event.relatedTarget as Node | null)) close();
    });
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });
  document.addEventListener("click", (event) => {
    if (!(event.target as Element).closest(".help-tip")) close();
  });
  document.addEventListener("scroll", close, true);
  window.addEventListener("resize", close);
}
