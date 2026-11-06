# Documentation

Start with the [guide](guide.md) for what exists, [goals](goals.md) for why it
exists, and [roadmap](roadmap.md) for outstanding work.

| Page | Authoritative content |
| --- | --- |
| [Project README](../README.md) | Introduction and quick start |
| [Goals](goals.md) | Intended outcomes, scope, and non-goals |
| [Guide](guide.md) | Existing features, usage, and limitations |
| [Roadmap](roadmap.md) | Outstanding work, priorities, and acceptance criteria |
| [Environment preferences design](environment-preferences.md) | Existing field meanings, tiled map delivery, segment costs, accepted preference controls, and OSM-only road-noise learning from municipal reference maps |
| [Architecture](architecture.md) | High-level components, dependencies, data flow, and ownership boundaries |
| [Development](development.md) | Setup, preparation, commands, and configuration |
| [Prepared data](reference/prepared-data.md) | OSM snapshots, artifact identity, publication, network schema, and spatial-selection contracts |
| [API reference](reference/api.md) | Endpoint roles, result contracts, search limits, and request/cache lifetimes |
| [Numeric routing format](numeric-routing-format.md) | Numeric artifact versions, identity, binary layout, and native working-set boundaries |
| [100 ms routing architecture](100ms-routing-architecture.md) | Routing latency target, dated observations, and outstanding performance acceptance |
| [Distance shells](distance-shells.md) | Delivered distance visualization, approximation semantics, methods, and limits |
| [Terrain mathematics](reference/terrain-mathematics.md) | Terrain calculations |
| [Routing mathematics](reference/routing-mathematics.md) | Planner calculations |
| [Loop-length mixtures](reference/loop-length-mixture.md) | MAP polynomial mixture mathematics and implementation; proposed GP uncertainty and adaptive sampling |
| [Archive](archive/README.md) | Dated plans, experiments, and supporting artifacts |

## Maintenance rule

When a feature ships, update its maintained topic page and remove its completed
work from the roadmap in the same change. Archive the implementation plan if it
provides useful history. Completed plans must not remain the primary reference
for current behavior.

- Give each behavior, default, and unresolved task one authoritative home;
  link to it instead of repeating its description across documents.
- Describe module purpose in module docstrings or documentation comments. Keep
  internal code explanations close to the implementation. Architecture covers
  component relationships; contracts and formats belong in technical references.
- Document existing behavior in the guide and technical references. Keep
  planned behavior in the roadmap, distinguishing next steps from optional ideas.
- Track outstanding validation explicitly. Implemented behavior may still need
  local review, runner acceptance, or performance checks.
- Keep goals focused on intended outcomes. Update them when direction or scope
  changes, rather than for routine implementation progress.
- Preserve experiment dates, dataset/model identities, and reproduction details.
  Historical measurements do not establish current correctness or performance.
- Check descriptions against code and configuration. Update links when moving
  documents, and keep dated results with their supporting artifacts.

The numbered documents were consolidated on 2026-10-20. Their original status
text is retained as history under the archive; unfinished historical tasks are
scheduled only when included in the current roadmap.
