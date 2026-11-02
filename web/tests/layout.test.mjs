/** Route framing avoids floating results without exhausting the map area. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { mapPadding } from "../src/layout.ts";

test("route framing accounts for results on desktop, mobile, and short screens", (context) => {
  let narrow = false;
  const panel = { right: 258, top: 18 };
  const legend = { left: 1000, height: 300, bottom: 318 };
  const dock = { top: 730, bottom: 860, height: 130 };
  globalThis.window = {
    innerWidth: 1280,
    innerHeight: 900,
    matchMedia: () => ({ matches: narrow }),
  };
  globalThis.document = {
    querySelector: (selector) => ({
      getBoundingClientRect: () => (selector === ".controls" ? panel : legend),
    }),
    getElementById: () => ({ getBoundingClientRect: () => dock }),
  };
  context.after(() => {
    delete globalThis.window;
    delete globalThis.document;
  });
  const desktop = mapPadding();
  assert.ok(window.innerHeight - desktop.bottom < dock.top);
  narrow = true;
  window.innerWidth = 390;
  window.innerHeight = 844;
  panel.top = 500;
  legend.height = 0;
  dock.top = 10;
  dock.bottom = 140;
  dock.height = 130;
  const mobile = mapPadding();
  assert.ok(mobile.top > dock.bottom);
  assert.ok(window.innerHeight - mobile.bottom < panel.top);
  dock.height = 450;
  dock.bottom = 460;
  const crowded = mapPadding();
  assert.ok(window.innerHeight - crowded.top - crowded.bottom >= 79.99);
});
