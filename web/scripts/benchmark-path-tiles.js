(async () => {
  const map = window.__map,
    wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const config = await (await fetch("/api/paths/config")).json();
  const moduleUrl = performance
    .getEntriesByType("resource")
    .find((e) => e.name.includes("/.vite/deps/maplibre-gl.js")).name;
  const lib = await import(moduleUrl);
  const buffers = new Map();
  lib.addProtocol("bench", async (req) => ({ data: buffers.get(req.url).slice(0) }));
  for (const listener of [...(map._listeners.moveend || [])]) map.off("moveend", listener);
  await new Promise((resolve) => {
    map.once("style.load", resolve);
    map.setStyle({ version: 8, sources: {}, layers: [] });
  });
  let snapshot = await (
    await fetch("/api/paths/snapshots", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        dataset_id: config.dataset_id,
        longitude: 24.80259,
        latitude: 60.21682,
        budget_km: 6,
      }),
    })
  ).json();
  if (snapshot.status !== "ready") throw Error(JSON.stringify(snapshot));
  let sequence = 0;
  const rows = [];
  window.__tileBenchResult = {
    browser: navigator.userAgent,
    canvas: [map.getCanvas().width, map.getCanvas().height],
    dataset_id: config.dataset_id,
    rows,
  };
  async function source(spec) {
    const id = "bench-" + ++sequence,
      start = performance.now();
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error("source timeout")), 30000);
      const listener = (e) => {
        if (e.sourceId === id && e.isSourceLoaded) {
          map.off("sourcedata", listener);
          clearTimeout(timer);
          resolve();
        }
      };
      map.on("sourcedata", listener);
      map.addSource(id, spec);
      map.addLayer({
        id,
        type: "line",
        source: id,
        ...(spec.type === "vector" ? { "source-layer": "paths" } : {}),
        paint: { "line-width": 3.5, "line-color": "#087f8c" },
      });
    });
    const loaded = performance.now();
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
    const frame = performance.now();
    map.removeLayer(id);
    map.removeSource(id);
    return { sourceMs: loaded - start, frameMs: frame - start };
  }
  for (const [viewport, lon, lat] of [
    ["urban", 24.807, 60.219],
    ["forest", 24.771, 60.242],
  ])
    for (const zoom of [11, 13, 15]) {
      map.jumpTo({ center: [lon, lat], zoom });
      await wait(250);
      const b = map.getBounds();
      for (const mode of ["gradient", "gradient_explore", "distance_explore"]) {
        if (mode !== "gradient")
          snapshot = await (
            await fetch("/api/paths/snapshots", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                dataset_id: config.dataset_id,
                longitude: 24.80259,
                latitude: 60.21682,
                budget_km: 6,
              }),
            })
          ).json();
        const params = new URLSearchParams({
          west: b.getWest(),
          south: b.getSouth(),
          east: b.getEast(),
          north: b.getNorth(),
          zoom,
          surface: "all",
          avoid_stairs: true,
          dataset_id: config.dataset_id,
          metric: mode === "distance_explore" ? "distance" : "gradient",
        });
        if (mode !== "gradient") {
          params.set("longitude", 24.80259);
          params.set("latitude", 60.21682);
          params.set("budget_km", 6);
        }
        const baseline = await (
          await fetch("/api/paths/" + (mode === "gradient" ? "viewport" : "explore") + "?" + params)
        ).json();
        const template = (
          mode === "gradient"
            ? config.tile_template.replace("{policy}", "all-no-stairs")
            : snapshot.tile_template
        ).replace("{variant}", (mode === "distance_explore" ? "distance" : "gradient") + "-hidden");
        const project = (lon, lat) => [
          ((lon + 180) / 360) * 2 ** zoom,
          ((1 - Math.asinh(Math.tan((lat * Math.PI) / 180)) / Math.PI) / 2) * 2 ** zoom,
        ];
        const [l, t] = project(b.getWest(), b.getNorth()),
          [r, bt] = project(b.getEast(), b.getSouth());
        let bytes = 0,
          fetchMs = performance.now();
        for (let x = Math.floor(l); x <= Math.floor(r); x++)
          for (let y = Math.floor(t); y <= Math.floor(bt); y++) {
            const suffix = `${zoom}/${x}/${y}.mvt`,
              url = template.replace("{z}", zoom).replace("{x}", x).replace("{y}", y);
            const response = await fetch(url);
            if (!response.ok) throw Error(`Tile HTTP ${response.status}: ${url}`);
            const buffer = await response.arrayBuffer();
            buffers.set("bench://" + suffix, buffer);
            bytes += buffer.byteLength;
          }
        fetchMs = performance.now() - fetchMs;
        const samples = { geojson: [], vector: [] };
        for (let repeat = 0; repeat < 11; repeat++)
          for (const kind of repeat % 2 ? ["vector", "geojson"] : ["geojson", "vector"]) {
            const result = await source(
              kind === "geojson"
                ? { type: "geojson", data: baseline }
                : {
                    type: "vector",
                    tiles: ["bench://{z}/{x}/{y}.mvt"],
                    minzoom: 10,
                    maxzoom: 16,
                    bounds: config.bounds_wgs84,
                  },
            );
            samples[kind].push(result);
          }
        const httpSamples = [];
        for (let repeat = 0; repeat < 11; repeat++)
          httpSamples.push(
            await source({
              type: "vector",
              tiles: [template],
              minzoom: 10,
              maxzoom: 16,
              bounds: config.bounds_wgs84,
            }),
          );
        rows.push({
          viewport,
          zoom,
          mode,
          bounds: b.toArray(),
          bytes,
          fetchMs,
          samples,
          httpSamples,
        });
        window.__tileBenchProgress = { viewport, zoom, mode, completed: rows.length };
      }
    }
  return {
    browser: navigator.userAgent,
    canvas: [map.getCanvas().width, map.getCanvas().height],
    dataset_id: config.dataset_id,
    method:
      "Matched camera, empty background with one styled line layer, 10 warm paired repetitions alternating source order. Preloaded GeoJSON and MVT (custom in-memory protocol) isolate source processing. FetchMs is serial tile prefetch and is not request-to-frame.",
    rows,
  };
})();
