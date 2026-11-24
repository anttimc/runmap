# Goals

Help runners easily choose their preferences, generate a suitable running route,
and transfer it to a sports watch. Map exploration is a secondary purpose:
help runners design routes roughly themselves and discover training hills
without relying on route optimization.

## Intended outcomes

- **Plan with preferences:** make it easy to express the desired terrain and
  surroundings, generate a route, and understand the resulting tradeoffs.
  Reuse preferences through named browser-local presets; cross-device reuse
  remains a possible improvement.
- **Take the route running:** make transfer to a sports watch straightforward.
  Existing GPX export and the optional local Suunto integration provide the
  starting point; support for other watches is not yet committed.
- **Interact responsively:** keep map use, route generation, and route review
  responsive. Target a revised route for runs up to 40 km within one second of
  changing settings or releasing a dragged pin. This is an intended outcome,
  not a delivered guarantee. Assess complete browser interactions as well as
  backend computation, including changed inputs and uncached requests.
- **Shape a route:** give runners more control over where a route goes while
  retaining useful guidance. Route shaping follows responsiveness in priority;
  automatic suggestions come later.
- **Explore independently:** understand paths, terrain, and surroundings without
  generating an optimized route. Support rough route design and finding short
  steep hills, sustained climbs, and rolling terrain for training.
- **Understand the evidence:** make route choices and measurements interpretable,
  preserving uncertainty about missing data, access, crossings, and structures.

## Audience and coverage

Runmap is currently a hobby project used with locally prepared data. The near-term
ambition is to make it useful and shareable with friends. Cloud hosting would
make access easier; a public service is a longer-term ambition, subject to the
resources needed to operate it.

Current prepared coverage focuses on the Helsinki capital area. Finland is the
target for wider coverage, rather than a promise of nationwide availability.

## Product direction

The core planning and export features are present. Improve the everyday workflow
before expanding automatic recommendations: prioritize responsiveness, then
route shaping. Browser-local presets support repeated use. Consider hosting and
cross-device preferences to make sharing and reuse easier; their scope and
scheduling remain undecided.

Keep exploration useful on its own. Discovering training hills is an intended
outcome, not a claim that automatic hill discovery is already available.

Use prepared pedestrian-network and terrain evidence consistently across
exploration and planning. Respect real junctions, access restrictions, bridges,
and tunnels; allow necessary retracing. Keep ordinary interaction independent
of data acquisition and able to work over bounded areas as coverage grows.
Implementation boundaries belong in the [architecture](architecture.md), and
performance targets and outstanding work belong in the [roadmap](roadmap.md).

## Scope

Terrain and path measurements inform route choice; they do not establish current
path conditions or predict heart rate, personal effort, or training outcomes.
Social features, live activity tracking, training prescriptions, and turn-by-turn
navigation are outside the current product scope. Accounts are not a current
requirement; whether future preference sync or hosting needs them remains undecided.

See the [guide](guide.md) for delivered behavior and the [roadmap](roadmap.md)
for next steps and optional experiments.
