// What the "Yours" cell says a value is.
//
// The cell is where a reader checks their record. Codes there make a
// correctly stored value look wrong, and make a wrong one indistinguishable
// from a right one — so a comma-joined list of codes reading as
// "age,stage" beside an editor showing "Age: Greater than 60 years" is
// worth fixing rather than explaining (#586).
//
// The care is in not going too far: not every string with a comma is a
// list, and relabelling part of somebody's free text because it collided
// with an option would be worse than the codes.

import { describe, expect, it } from "vitest";

import { formatValue } from "./TrialDetailPage";

const FLIPI = [
  { value: "age", label: "Age over 60" },
  { value: "stage", label: "Ann Arbor III or IV" },
  { value: "ldh", label: "LDH above normal" },
];

describe("a value the column stores comma-joined", () => {
  it("reads as labels, not as codes", () => {
    expect(formatValue("age,stage", FLIPI)).toBe("Age over 60, Ann Arbor III or IV");
  });

  it("tolerates the spacing the record happens to have", () => {
    expect(formatValue("age, stage", FLIPI)).toBe("Age over 60, Ann Arbor III or IV");
  });

  it("still labels a single value", () => {
    expect(formatValue("age", FLIPI)).toBe("Age over 60");
  });
});

describe("what it refuses to treat as a list", () => {
  it("leaves free text alone, even with a comma in it", () => {
    // The whole reason this is all-or-nothing. A name is not two codes.
    expect(formatValue("Smith, John", FLIPI)).toBe("Smith, John");
  });

  it("leaves it alone when only some parts are known", () => {
    // A legacy spelling beside a current one. Half-labelled output is more
    // confusing than the raw value, and the raw value is what it read as
    // before, so nothing is lost by stopping here.
    expect(formatValue("age,something-else", FLIPI)).toBe("age,something-else");
  });

  it("does not split inside brackets", () => {
    // `inv(3)(q21,q26)` is ONE cytogenetic marker. A naive split makes it
    // two the record has never heard of — which is why this and the editor
    // share one splitting rule instead of each having its own.
    //
    // Two markers, not one: with a single one a naive split fails to match
    // anything and falls back to the raw value, which happens to read
    // correctly and hides the bug. It takes a real LIST containing a
    // bracketed marker to tell the two rules apart.
    const markers = [
      { value: "inv(3)(q21,q26)", label: "inv(3)" },
      { value: "del17p", label: "del(17p)" },
    ];

    expect(formatValue("inv(3)(q21,q26),del17p", markers)).toBe("inv(3), del(17p)");
  });

  it("says nothing when there is nothing", () => {
    expect(formatValue("", FLIPI)).toBe("—");
    expect(formatValue(null, FLIPI)).toBe("—");
  });

  it("leaves a value alone when the row carries no vocabulary", () => {
    expect(formatValue("age,stage", undefined)).toBe("age,stage");
  });
});

describe("the shapes that already worked", () => {
  it("labels a real list", () => {
    expect(formatValue(["age", "stage"], FLIPI)).toBe(
      "Age over 60, Ann Arbor III or IV",
    );
  });

  it("says Yes and No for a boolean", () => {
    expect(formatValue(true)).toBe("Yes");
    expect(formatValue(false)).toBe("No");
  });
});
