import type { PaddingOptions } from "maplibre-gl";

import { element } from "./dom";

/** Frame routes beside the visible tool panel or above the mobile sheet. */
export function mapPadding(): PaddingOptions {
  const panel = document.querySelector<HTMLElement>(".controls")!.getBoundingClientRect();
  const legend = document.querySelector<HTMLElement>(".map-legend")!.getBoundingClientRect();
  const narrow = window.matchMedia("(max-width: 600px)").matches;
  return {
    left: narrow ? 24 : panel.right + 24,
    right: narrow ? 24 : window.innerWidth - legend.left + 24,
    top: narrow && legend.height ? Math.min(legend.bottom + 16, panel.top / 2) : 40,
    bottom: narrow ? window.innerHeight - panel.top + 24 : 48,
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
    });
  }
  new ResizeObserver(() => {
    document.documentElement.style.setProperty(
      "--tool-height",
      `${panel.getBoundingClientRect().height}px`,
    );
  }).observe(panel);
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
