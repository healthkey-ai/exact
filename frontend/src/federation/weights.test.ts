/** The weights as data: what counts as one, what survives storage, and what
 *  reaches the wire.
 *
 *  Reachable through the dialog, but only through values a number input will
 *  produce. These come from elsewhere too — a host's `initialFilters`, a row
 *  written by another client, a hand-edited `localStorage` — and that is the
 *  half the component tests cannot pose.
 */
import { describe, expect, it } from "vitest";

import { filterStateToParams } from "./api";
import { sanitizeStoredFilters } from "./filters";
import { DEFAULT_WEIGHT, isUsableWeight, weightValue, weightsAreCustom } from "./weights";

describe("what counts as a weight", () => {
  it("takes zero and up, and nothing that is not a finite number", () => {
    expect(isUsableWeight(0)).toBe(true);
    expect(isUsableWeight(25)).toBe(true);
    // Zero is a real answer — "ignore this term" — which is why the guard is
    // `>= 0` and not truthiness.
    expect(isUsableWeight(-1)).toBe(false);
    expect(isUsableWeight(Number.POSITIVE_INFINITY)).toBe(false);
    expect(isUsableWeight(NaN)).toBe(false);
    expect(isUsableWeight("25")).toBe(false);
    expect(isUsableWeight(undefined)).toBe(false);
  });

  it("falls back to the server's own default for anything else", () => {
    expect(weightValue({}, "riskWeight")).toBe(DEFAULT_WEIGHT);
    expect(weightValue({ riskWeight: -5 }, "riskWeight")).toBe(DEFAULT_WEIGHT);
    expect(weightValue({ riskWeight: 0 }, "riskWeight")).toBe(0);
  });

  it("calls the score custom only when a weight says something new", () => {
    expect(weightsAreCustom({})).toBe(false);
    expect(weightsAreCustom({ riskWeight: DEFAULT_WEIGHT })).toBe(false);
    // Unusable is not custom: it is not going on the wire either, so the
    // trigger would be claiming a score the server is not computing.
    expect(weightsAreCustom({ riskWeight: -5 })).toBe(false);
    expect(weightsAreCustom({ riskWeight: 0 })).toBe(true);
  });
});

describe("a weight on its way to the wire", () => {
  it("refuses one a host set to something the score cannot use", () => {
    // `initialFilters` is public API and never passes through the form.
    expect(filterStateToParams({ riskWeight: -5 })).toEqual({});
    expect(filterStateToParams({ riskWeight: Number.NaN })).toEqual({});
  });

  it("says nothing the server already assumes", () => {
    expect(filterStateToParams({ riskWeight: DEFAULT_WEIGHT })).toEqual({});
    expect(filterStateToParams({ riskWeight: 0 })).toEqual({ riskWeight: "0" });
  });
});

describe("a weight coming back from storage", () => {
  const stored = (value: unknown) =>
    sanitizeStoredFilters({ riskWeight: value }).riskWeight;

  it("is taken only as a number inside the range the control offers", () => {
    expect(stored(40)).toBe(40);
    expect(stored(0)).toBe(0);
    expect(stored(100)).toBe(100);
    // Above the range: another client's idea of a weight, or an edited file.
    // Dropped, so the field falls back to 25 — the score everyone else sees.
    expect(stored(101)).toBeUndefined();
    expect(stored(-1)).toBeUndefined();
    // CB spells its own weights as decimal STRINGS ("25.00"). Its row is not
    // this row, and a string here is a value of the wrong type like any other.
    expect(stored("40")).toBeUndefined();
    expect(stored(null)).toBeUndefined();
    expect(stored({ value: 40 })).toBeUndefined();
  });

  it("leaves the other three alone", () => {
    const out = sanitizeStoredFilters({ riskWeight: 40 });
    expect(out.benefitWeight).toBeUndefined();
    expect(out.patientBurdenWeight).toBeUndefined();
    expect(out.distancePenaltyWeight).toBeUndefined();
  });
});
