import { describe, expect, it } from "vitest";

import { groupByMatchingType } from "./grouping";
import type { TrialMatch } from "./types";

function trial(trialId: number, matchingType: TrialMatch["matchingType"]): TrialMatch {
  return { trialId, matchingType } as unknown as TrialMatch;
}

describe("groupByMatchingType", () => {
  it("returns all-empty buckets for an empty list", () => {
    expect(groupByMatchingType([])).toEqual({
      eligible: [],
      potential: [],
      not_eligible: [],
    });
  });

  it("buckets trials by their matcher verdict", () => {
    const a = trial(1, "eligible");
    const b = trial(2, "potential");
    const c = trial(3, "eligible");
    const d = trial(4, "not_eligible");
    const grouped = groupByMatchingType([a, b, c, d]);
    expect(grouped.eligible).toEqual([a, c]);
    expect(grouped.potential).toEqual([b]);
    expect(grouped.not_eligible).toEqual([d]);
  });

  it("drops a trial with an unexpected matchingType rather than throwing", () => {
    const weird = trial(9, "bogus" as TrialMatch["matchingType"]);
    const ok = trial(1, "eligible");
    const grouped = groupByMatchingType([weird, ok]);
    expect(grouped.eligible).toEqual([ok]);
    expect(grouped.potential).toEqual([]);
    expect(grouped.not_eligible).toEqual([]);
  });
});
