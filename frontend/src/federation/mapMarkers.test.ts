/** The map's data, checked without a map. */
import { describe, expect, it } from "vitest";

import { boundsOf, buildMarkers, markerKey, unmappableCount } from "./mapMarkers";
import type { TrialMatch } from "./types";

const at = (
  id: number,
  point: { latitude: number; longitude: number } | null,
  score = 50,
): TrialMatch =>
  ({
    trialId: id,
    studyId: `NCT${id}`,
    briefTitle: `Trial ${id}`,
    matchScore: score,
    goodnessScore: score,
    closestLocationGeoPoint: point,
  }) as unknown as TrialMatch;

describe("buildMarkers", () => {
  it("puts two trials at the same hospital on one pin", () => {
    // Otherwise a busy centre is a stack of pins the reader cannot separate,
    // and the count they actually want — how many trials are here — is not on
    // screen anywhere.
    const markers = buildMarkers([
      at(1, { latitude: 51.5074, longitude: -0.1278 }),
      at(2, { latitude: 51.5074, longitude: -0.1278 }),
      at(3, { latitude: 48.8566, longitude: 2.3522 }),
    ]);

    expect(markers).toHaveLength(2);
    expect(markers[0].trials.map((t) => t.trialId)).toEqual([1, 2]);
  });

  it("treats a short walk as the same place", () => {
    // Two departments of one hospital are one place to someone deciding
    // whether they can get there.
    const markers = buildMarkers([
      at(1, { latitude: 51.50741, longitude: -0.12781 }),
      at(2, { latitude: 51.50744, longitude: -0.12783 }),
    ]);
    expect(markers).toHaveLength(1);
  });

  it("groups by distance, so there is no edge to fall either side of", () => {
    // Rounding to a fixed number of decimals puts a hard boundary somewhere,
    // and two points a couple of metres apart can land on opposite sides of
    // it — 51.50749 and 51.50751 are two metres apart and round differently.
    const markers = buildMarkers([
      at(1, { latitude: 51.50749, longitude: -0.1278 }),
      at(2, { latitude: 51.50751, longitude: -0.1278 }),
    ]);
    expect(markers).toHaveLength(1);
  });

  it("keeps two hospitals across town apart", () => {
    const markers = buildMarkers([
      at(1, { latitude: 51.5074, longitude: -0.1278 }),
      at(2, { latitude: 51.5174, longitude: -0.1278 }),
    ]);
    expect(markers).toHaveLength(2);
  });

  it("orders the trials inside a pin by score, and the pins by how many they hold", () => {
    const markers = buildMarkers([
      at(1, { latitude: 10, longitude: 10 }, 40),
      at(2, { latitude: 20, longitude: 20 }, 90),
      at(3, { latitude: 10, longitude: 10 }, 95),
    ]);

    expect(markers[0].trials.map((t) => t.trialId)).toEqual([3, 1]);
    expect(markers.map((m) => m.trials.length)).toEqual([2, 1]);
  });

  it("drops a row with no location, and says how many", () => {
    // Counted rather than hidden: a map that quietly shows fewer trials than
    // the list lies by omission.
    const trials = [
      at(1, { latitude: 10, longitude: 10 }),
      at(2, null),
      at(3, null),
    ];
    expect(buildMarkers(trials)).toHaveLength(1);
    expect(unmappableCount(trials)).toBe(2);
  });

  it("refuses coordinates that are not places", () => {
    // A bad row would otherwise stretch the bounds far enough to zoom the map
    // out to the whole globe and make every real pin unreadable.
    const markers = buildMarkers([
      at(1, { latitude: 51.5, longitude: -0.12 }),
      at(2, { latitude: 999, longitude: 0 }),
      at(3, { latitude: Number.NaN, longitude: 0 }),
      at(4, { latitude: 0, longitude: -181 }),
    ]);
    expect(markers.map((m) => m.trials[0].trialId)).toEqual([1]);
  });
});

describe("boundsOf", () => {
  it("gives a box with area even for a single pin", () => {
    // A zero-area box is honoured by zooming to street level on one building.
    const bounds = boundsOf(buildMarkers([at(1, { latitude: 51.5, longitude: -0.12 })]));
    expect(bounds).not.toBeNull();
    expect(bounds!.north).toBeGreaterThan(bounds!.south);
    expect(bounds!.east).toBeGreaterThan(bounds!.west);
  });

  it("stays inside the world", () => {
    const bounds = boundsOf(buildMarkers([at(1, { latitude: 89.99, longitude: 179.99 })]));
    expect(bounds!.north).toBeLessThanOrEqual(90);
    expect(bounds!.east).toBeLessThanOrEqual(180);
  });

  it("is null when there is nothing to show", () => {
    expect(boundsOf([])).toBeNull();
  });
});

describe("buildMarkers and unmappableCount agree", () => {
  it("counts a point that is present but unusable as missing", () => {
    // Two copies of "can this be placed?" drift, and the panel then drops a
    // trial while reporting that nothing is missing.
    const trials = [
      at(1, { latitude: 51.5, longitude: -0.12 }),
      at(2, { latitude: 999, longitude: 0 }),
      at(3, { latitude: Number.NaN, longitude: 0 }),
      at(4, null),
    ];
    expect(buildMarkers(trials)).toHaveLength(1);
    expect(unmappableCount(trials)).toBe(3);
  });
});

describe("boundsOf across the antimeridian", () => {
  it("takes the short way round", () => {
    // 179 and -179 are a few kilometres apart. Read as min/max they are 358
    // degrees, and the map zooms out to the whole globe.
    const bounds = boundsOf(
      buildMarkers([
        at(1, { latitude: -16.5, longitude: 179.0 }),
        at(2, { latitude: -16.6, longitude: -179.0 }),
      ]),
    )!;

    // `west > east` is how a box that crosses the antimeridian is expressed.
    expect(bounds.west).toBeGreaterThan(bounds.east);
    const span = 360 - bounds.west + bounds.east;
    expect(span).toBeLessThan(10);
  });

  it("still takes the ordinary way round when that is shorter", () => {
    const bounds = boundsOf(
      buildMarkers([
        at(1, { latitude: 51.5, longitude: -0.12 }),
        at(2, { latitude: 48.85, longitude: 2.35 }),
      ]),
    )!;
    expect(bounds.west).toBeLessThan(bounds.east);
    expect(bounds.east - bounds.west).toBeLessThan(10);
  });
})

describe("buildMarkers across the antimeridian", () => {
  it("groups two sites that are metres apart but 360 degrees by subtraction", () => {
    const markers = buildMarkers([
      at(1, { latitude: -16.5, longitude: 179.9995 }),
      at(2, { latitude: -16.5, longitude: -179.9995 }),
    ]);
    expect(markers).toHaveLength(1);
  });
});

describe("buildMarkers is not at the mercy of the sort", () => {
  // Three buildings of one campus, 140m apart in a line. First-fit grouping is
  // not transitive, so which of them is seen first decides whether they come
  // out as one place or two — and the row order is the reader's sort. Before
  // this, changing the sort changed how many places the map said there were.
  const campus = [
    { latitude: 51.507, longitude: -0.1278 },
    { latitude: 51.50826, longitude: -0.1278 },
    { latitude: 51.50952, longitude: -0.1278 },
  ];

  it("gives the same places whichever order the rows arrive in", () => {
    const shape = (order: number[]) => {
      const markers = buildMarkers(order.map((i) => at(i + 1, campus[i])));
      return markers.map((m) => ({
        key: markerKey(m),
        trials: m.trials.map((t) => t.trialId).sort(),
      }));
    };

    const a = shape([0, 1, 2]);
    const b = shape([2, 1, 0]);
    const c = shape([1, 0, 2]);
    expect(b).toEqual(a);
    expect(c).toEqual(a);
  });

  it("gives a place the same key whichever order it was built from", () => {
    // Which is what makes the key something the panel can hold on to while
    // the reader changes the sort underneath it.
    const forward = buildMarkers(campus.map((p, i) => at(i + 1, p)));
    const reverse = buildMarkers([...campus].reverse().map((p, i) => at(10 + i, p)));
    expect(reverse.map(markerKey)).toEqual(forward.map(markerKey));
  });
});

describe("a place has a name", () => {
  it("uses the site nearest the patient, and falls back to coordinates", () => {
    const named = buildMarkers([
      at(1, { latitude: 51.5, longitude: -0.12 }),
    ]);
    expect(named[0].name).toMatch(/51\.5/);

    const withTitle = buildMarkers([
      { ...at(2, { latitude: 51.5, longitude: -0.12 }), location: ["St Thomas'"] } as never,
    ]);
    expect(withTitle[0].name).toBe("St Thomas'");
  });

  it("does not take an empty title, which would leave a nameless button", () => {
    const blank = buildMarkers([
      { ...at(3, { latitude: 51.5, longitude: -0.12 }), location: [""] } as never,
    ]);
    expect(blank[0].name).not.toBe("");
  });
});

describe("a place is named after the site, not the best match", () => {
  it("takes the nearest trial's site even when another scores higher", () => {
    // A pin is a place and the reader is asking where it is. Naming it after
    // the best-matching trial labels the pin with the wrong hospital whenever
    // the nearest and the best are different trials.
    const near = {
      ...at(1, { latitude: 51.5074, longitude: -0.1278 }, 40),
      location: ["St Thomas' Hospital"],
      distance: 3,
    } as never;
    const far = {
      ...at(2, { latitude: 51.5075, longitude: -0.1279 }, 95),
      location: ["Guy's Hospital"],
      distance: 40,
    } as never;

    const markers = buildMarkers([near, far]);
    expect(markers).toHaveLength(1);
    expect(markers[0].name).toBe("St Thomas' Hospital");
    // The trials inside are still best-match first, which is the panel's order.
    expect(markers[0].trials.map((t) => t.trialId)).toEqual([2, 1]);
  });
});
