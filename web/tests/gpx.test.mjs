/** GPX exports preserve the displayed route's order and download locally. */
import assert from "node:assert/strict";
import { test } from "node:test";
import { downloadRouteGpx, routeGpx } from "../src/gpx.ts";

const outbound = {
  coordinates: [
    [24.8, 60.2],
    [24.81, 60.21],
  ],
  distance_m: 1200,
};
const returning = {
  coordinates: [
    [24.81, 60.21],
    [24.8, 60.2],
  ],
  distance_m: 1200,
};
const oneWay = { mode: "one_way", legs: [outbound], distance_m: 1200 };
const roundTrip = {
  mode: "round_trip",
  legs: [outbound, returning],
  distance_m: 2400,
  outbound_left: true,
};

function points(xml) {
  return [...xml.matchAll(/<trkpt lat="([^"]+)" lon="([^"]+)"(?: \/>|>)/g)].map(([, lat, lon]) => [
    Number(lon),
    Number(lat),
  ]);
}

test("one-way GPX uses GPX 1.1 latitude/longitude and ends at destination", () => {
  const xml = routeGpx(oneWay);
  assert.ok(xml.startsWith('<?xml version="1.0" encoding="UTF-8"?>'));
  assert.match(
    xml,
    /version="1.1" creator="Runmap" xmlns="http:\/\/www.topografix.com\/GPX\/1\/1"/,
  );
  assert.match(xml, /<name>Runmap · One way · 1.20 km<\/name>/);
  assert.equal((xml.match(/<trkseg>/g) ?? []).length, 1);
  assert.deepEqual(points(xml), outbound.coordinates);
  assert.doesNotMatch(xml, /<ele>|<time>|<rtept/);
});

test("profile samples export elevation in metres, preserve return order, and omit missing heights", () => {
  const locations = [
    [24.8, 60.2],
    [24.805, 60.205],
    [24.81, 60.21],
    [24.8, 60.2],
  ];
  const heights = [-2.5, 0, null, 12.75];
  const profile = {
    intervals: locations.slice(1).map((location, index) => ({
      coordinates: [locations[index], location],
      start_elevation_m: heights[index],
      end_elevation_m: heights[index + 1],
    })),
  };
  const xml = routeGpx(roundTrip, profile);
  assert.deepEqual(points(xml), locations);
  assert.deepEqual(
    [...xml.matchAll(/<ele>([^<]+)<\/ele>/g)].map((match) => Number(match[1])),
    [-2.5, 0, 12.75],
  );
  assert.match(xml, /<trkpt lat="60.21" lon="24.81" \/>/);
  assert.deepEqual(points(routeGpx(oneWay, { intervals: [] })), outbound.coordinates);
});

test("round trips keep the return leg and repeated paths, joining turnaround once", () => {
  assert.deepEqual(points(routeGpx(roundTrip)), [
    ...outbound.coordinates,
    returning.coordinates[1],
  ]);
  const detour = {
    ...returning,
    coordinates: [
      [24.81, 60.21],
      [24.82, 60.19],
      [24.8, 60.2],
    ],
  };
  for (const outbound_left of [true, false]) {
    const candidate = { ...roundTrip, legs: [outbound, detour], outbound_left };
    const xml = routeGpx(candidate);
    assert.match(xml, new RegExp(outbound_left ? "Clockwise" : "Counter-clockwise"));
    assert.deepEqual(points(xml), [...outbound.coordinates, ...detour.coordinates.slice(1)]);
  }
});

test("download creates a GPX file and releases its temporary resources", async (context) => {
  let downloaded;
  let clicked = false;
  let removed = false;
  let cleanup;
  const link = {
    click: () => {
      clicked = true;
    },
    remove: () => {
      removed = true;
    },
  };
  context.mock.method(URL, "createObjectURL", (blob) => {
    downloaded = blob;
    return "blob:route";
  });
  const revoke = context.mock.method(URL, "revokeObjectURL", () => {});
  context.after(() => {
    delete globalThis.document;
    delete globalThis.window;
  });
  globalThis.document = {
    createElement: (tag) => {
      assert.equal(tag, "a");
      return link;
    },
    body: { append: (node) => assert.equal(node, link) },
  };
  globalThis.window = {
    setTimeout: (callback) => {
      cleanup = callback;
    },
  };
  downloadRouteGpx(roundTrip);
  assert.equal(link.download, "runmap-out-and-back-2.40km.gpx");
  assert.equal(link.href, "blob:route");
  assert.equal(downloaded.type, "application/gpx+xml;charset=utf-8");
  assert.equal(await downloaded.text(), routeGpx(roundTrip));
  assert.ok(clicked && removed);
  assert.equal(revoke.mock.callCount(), 0);
  cleanup();
  assert.deepEqual(revoke.mock.calls[0].arguments, ["blob:route"]);
});
