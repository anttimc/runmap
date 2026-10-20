(async () => {
  const map = window.__map,
    wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const source = () =>
    Object.keys(map.getStyle().sources).find((id) => id.startsWith("path-tiles-"));
  async function ready() {
    for (let i = 0; i < 300; i++) {
      if (
        document.getElementById("path-status").dataset.state === "ready" &&
        map.isSourceLoaded(source())
      )
        return;
      await wait(100);
    }
    throw Error(document.getElementById("path-status").textContent);
  }
  await ready();
  const requests = [],
    errors = [];
  const orig = window.fetch;
  window.fetch = async (...args) => {
    requests.push(String(args[0]));
    return orig(...args);
  };
  map.on("error", (e) => errors.push(e.error.message));
  const change = (id, value) => {
    const el = document.getElementById(id);
    if (typeof value === "boolean") el.checked = value;
    else el.value = value;
    el.dispatchEvent(new Event("change", { bubbles: true }));
  };
  const choose = (lon, lat) => {
    document.getElementById("outing-set-start").click();
    map.fire("click", {
      point: map.project([lon, lat]),
      lngLat: { lng: lon, lat },
      originalEvent: { target: map.getCanvas() },
    });
  };
  document.getElementById("mode-distance").click();
  choose(24.8034416, 60.2147194);
  const loading = document.getElementById("path-status").textContent;
  map.panBy([100, 0], { duration: 0 });
  await ready();
  const prepared = requests.filter((x) => x.includes("/snapshots")).length;
  const start = document.getElementById("outing-status").textContent;
  document.getElementById("mode-distance").click();
  await ready();
  change("path-hide-building-access", false);
  await ready();
  change("path-hide-building-access", true);
  await ready();
  map.panBy([150, 0], { duration: 0 });
  await ready();
  const after = requests.filter((x) => x.includes("/snapshots")).length;
  const beforeExpirySource = source();
  map.fire("error", {
    sourceId: source(),
    error: Object.assign(new Error("Simulated expiry"), { status: 410 }),
  });
  await ready();
  const expiryRecovered = source() !== beforeExpirySource;
  const expiryRequests = requests.filter((x) => x.includes("/snapshots")).length - after;
  map.fire("error", {
    sourceId: source(),
    error: Object.assign(new Error("Simulated repeated expiry"), { status: 410 }),
  });
  await wait(100);
  const expiryRetryProtected = document.getElementById("path-status").dataset.state === "failed";
  // Race two starts. Only the latest marker and source may survive.
  choose(24.807, 60.219);
  choose(24.771, 60.242);
  await ready();
  const latest = document.getElementById("outing-status").textContent;
  document.getElementById("outing-clear-start").click();
  await ready();
  const cleared = map.getSource(source()).serialize().tiles[0];
  return {
    loading,
    start,
    prepared,
    after,
    expiryRecovered,
    expiryRequests,
    expiryRetryProtected,
    latest,
    cleared,
    legacyRequests: requests.filter((x) => /paths\/(viewport|explore)/.test(x)),
    errors: errors.filter((message) => !message.startsWith("Simulated")),
    source: source(),
    browser: navigator.userAgent,
  };
})();
