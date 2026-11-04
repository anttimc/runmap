# Route-mode UI review — 2026-11-04

The user accepted current routing quality and normal desktop use, requesting
basic UI checks and deferring any fixes. No application code was changed.

## Scope and results

The existing `web/scripts/check-run-planning.mjs` passed against the running local
API and Vite in isolated headless Chrome. Route and elevation responses were
controlled fixtures; configuration, snapping, and map layers used the local API.
No uncaught browser exceptions were recorded.

Passed checks:

- Initial Off state, One way/Round trip transitions, endpoint labels, shape
  control visibility, orientation changes, and zero-width round trips.
- Independent layers, retained route geometry while Off, Clear, late-response
  cancellation on mode change, and a controlled planner error message.
- Elevation open/close, route-click reopening with cached data, missing-height
  readout, profile-location marker, and profile dismissal on mode changes.
- Desktop 1440 × 1000 and emulated mobile 390 × 844 layouts; screenshots inspected.

A supplemental temporary script also passed:

- Keyboard Enter activation of Fit run and Show elevation, and Escape to close
  elevation. Fit run included both endpoints in the viewport at 1440 × 700.
- Shared-start changes and clearing while planning, plus start selection while
  Off followed by enabling One way. Generated requests contained the updated start;
  clearing it disabled generation.
- Visible result card with collapsed controls at 390 × 600; screenshot inspected.

One minor wording issue is deferred: changing the shared start during round-trip
planning can show “Click the map to set your turnaround” instead of the stale-route
warning. Regeneration used the new start correctly. Review whether both messages
should remain visible.

These checks do not establish complete keyboard accessibility, pointer/touch
dragging, all viewport/settings combinations, real-device mobile behavior,
route-elevation accuracy, or backend equivalence/performance.

## Reproduction and evidence

Source revision: `5fdde34`, with documentation edits only. Dataset:
`2f8b220e40a1cf1c40b733db19b1be939e7e93ca064e52c3c110f29f824034b5`.
Home/turnaround: `(24.795, 60.216)` / `(24.815, 60.225)`; changed home:
`(24.796, 60.217)`. All surfaces, stairs avoided, 50 m snapping, hills Off.
Node 24.21.0; local macOS Chrome with software rendering.

Follow the [browser setup](../../development.md#verification), then run:

```sh
node web/scripts/check-run-planning.mjs artifacts/ui-check-2026-11-04/basic
node artifacts/ui-check-2026-11-04/check-extra.mjs artifacts/ui-check-2026-11-04/extra
```

[Local artifacts](../../../artifacts/ui-check-2026-11-04/) contain request observations,
screenshots, and the supplemental script. They are Git-ignored; the main browser
script is tracked. Earlier supplemental attempts exposed test-event/timing and
variable-scope problems; the final run corrected those. A trial point at
`(24.797, 60.218)` could not snap within 50 m, so the Off-mode check used the
known eligible original home instead.
