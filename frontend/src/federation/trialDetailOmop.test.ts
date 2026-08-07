import { describe, expect, it } from "vitest";

import { formatOmopConcepts } from "./TrialDetailPage";

// The OMOP therapy regimen/component levels arrive as concept_ids in `value`
// with no `options` map; the server attaches mirror-resolved `omopConcepts`
// (drug names). The detail row must render those names, not raw concept_ids.
describe("formatOmopConcepts", () => {
  const concepts = [
    { code: 19026972, title: "lenalidomide", vocab: "RxNorm" },
    { code: 43014237, title: "pomalidomide", vocab: "RxNorm" },
  ];

  it("resolves concept_id array values to their titles", () => {
    // value arrives as strings over the wire; codes are numbers — compare stringified
    expect(formatOmopConcepts(["19026972", "43014237"], concepts)).toBe(
      "lenalidomide, pomalidomide",
    );
  });

  it("resolves a single (non-array) concept_id", () => {
    expect(formatOmopConcepts("19026972", concepts)).toBe("lenalidomide");
  });

  it("falls back to the raw id for a concept the server did not resolve", () => {
    // never hide a criterion just because one code is unmapped
    expect(formatOmopConcepts(["19026972", "999999"], concepts)).toBe(
      "lenalidomide, 999999",
    );
  });

  it("renders an em dash for empty / missing values", () => {
    expect(formatOmopConcepts([], concepts)).toBe("—");
    expect(formatOmopConcepts(null, concepts)).toBe("—");
    expect(formatOmopConcepts("", concepts)).toBe("—");
  });
});
