import { describe, expect, it } from "vitest";

import { asText, scoreTier } from "./TrialCard";

describe("scoreTier", () => {
  // Thresholds mirror CB `getScoreColor`: >=80 green, >=60 yellow, else red.
  it("returns green at and above 80", () => {
    expect(scoreTier(80)).toBe("green");
    expect(scoreTier(100)).toBe("green");
  });
  it("returns yellow in [60, 80)", () => {
    expect(scoreTier(79)).toBe("yellow");
    expect(scoreTier(60)).toBe("yellow");
  });
  it("returns red below 60", () => {
    expect(scoreTier(59)).toBe("red");
    expect(scoreTier(0)).toBe("red");
  });
});

describe("asText", () => {
  it("joins arrays with ', ' by default and drops falsy entries", () => {
    expect(asText(["Boston", "", "New York"])).toBe("Boston, New York");
  });
  it("honours a custom separator", () => {
    expect(asText(["Phase 1", "Phase 2"], " / ")).toBe("Phase 1 / Phase 2");
  });
  it("passes strings through and coerces non-strings", () => {
    expect(asText("Interventional")).toBe("Interventional");
    expect(asText(3)).toBe("3");
  });
  it("returns an empty string for null/undefined", () => {
    expect(asText(null)).toBe("");
    expect(asText(undefined)).toBe("");
  });
});
