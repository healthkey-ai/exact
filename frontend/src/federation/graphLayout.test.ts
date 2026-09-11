/** The graph's geometry, without a DOM.
 *
 *  The layout is computed rather than simulated precisely so that it can be
 *  checked like this: the same trials always produce the same picture.
 */
import { describe, expect, it } from "vitest";

import { buildGraphLayout, MAX_CONCEPTS } from "./graphLayout";
import type { GraphTrialNode } from "./types";

const item = (patientField: string, label = patientField) => ({
  patientField,
  label,
  trialField: patientField,
  dependencies: [],
});

const trial = (
  trialId: number,
  match: Partial<GraphTrialNode["match"]> = {},
  score = 80,
): GraphTrialNode => ({
  nodeId: `trial:${trialId}`,
  trialId,
  studyId: `NCT${trialId}`,
  briefTitle: `Trial ${trialId}`,
  matchScore: score,
  goodnessScore: score,
  match: { matched: [], notMatched: [], missing: [], ...match },
});

describe("buildGraphLayout", () => {
  it("draws one node per attribute, however many trials ask about it", () => {
    // The shared attribute is the whole reason for the picture: two cards
    // cannot show a reader that one missing value is standing between them
    // and both trials.
    const layout = buildGraphLayout([
      trial(1, { missing: [item("ecog")] }),
      trial(2, { missing: [item("ecog")] }),
    ]);

    expect(layout.concepts).toHaveLength(1);
    expect(layout.concepts[0].trialIds).toEqual([1, 2]);
    expect(layout.edges).toHaveLength(2);
  });

  it("bands an attribute by its worst answer, not its best", () => {
    // The same attribute can be met for one trial and contradicted for
    // another. Banding on the best answer would file a requirement that rules
    // the patient out with the ones they meet.
    const layout = buildGraphLayout([
      trial(1, { matched: [item("ecog")] }),
      trial(2, { notMatched: [item("ecog")] }),
    ]);

    expect(layout.concepts[0].status).toBe("notMatched");
  });

  it("keeps the attributes the most trials ask about when it has to drop some", () => {
    const shared = Array.from({ length: 3 }, (_, i) => item(`shared${i}`));
    const lonely = Array.from({ length: MAX_CONCEPTS + 10 }, (_, i) => item(`lonely${i}`));
    const layout = buildGraphLayout([
      trial(1, { missing: [...shared, ...lonely] }),
      trial(2, { missing: shared }),
    ]);

    expect(layout.truncated).toBe(true);
    expect(layout.totalConcepts).toBe(shared.length + lonely.length);
    expect(layout.concepts).toHaveLength(MAX_CONCEPTS);
    // The three both trials ask about survive the cut.
    for (const s of shared) {
      expect(layout.concepts.some((c) => c.id === s.patientField)).toBe(true);
    }
  });

  it("points no edge at an attribute it dropped", () => {
    const lonely = Array.from({ length: MAX_CONCEPTS + 5 }, (_, i) => item(`x${i}`));
    const layout = buildGraphLayout([trial(1, { missing: lonely })]);

    const drawn = new Set(layout.concepts.map((c) => c.id));
    for (const edge of layout.edges) expect(drawn.has(edge.conceptId)).toBe(true);
  });

  it("orders trials by score and sizes them by it", () => {
    const layout = buildGraphLayout([trial(1, {}, 20), trial(2, {}, 95)]);

    expect(layout.trials.map((t) => t.trial.trialId)).toEqual([2, 1]);
    expect(layout.trials[0].r).toBeGreaterThan(layout.trials[1].r);
  });

  it("is deterministic — the same trials give the same picture", () => {
    const input = [
      trial(1, { matched: [item("a")], missing: [item("b")] }),
      trial(2, { notMatched: [item("c")] }),
    ];
    expect(buildGraphLayout(input)).toEqual(buildGraphLayout(input));
  });

  it("survives a trial with no attributes at all", () => {
    const layout = buildGraphLayout([trial(1)]);
    expect(layout.concepts).toHaveLength(0);
    expect(layout.edges).toHaveLength(0);
    expect(layout.height).toBeGreaterThan(0);
  });
});

describe("buildGraphLayout — attributes that share a patient field", () => {
  it("keeps two trial requirements apart even when they ask about one value", () => {
    // `age_low_limit` and `age_high_limit` are both `patient_age`, and four
    // `mutation_*_required` are all `genetic_mutations`. Keyed on the
    // patient's field they collapse into one node that keeps the first label
    // and the worst status — "Age Low Limit" in the "Not met" band when it is
    // the upper bound that excludes the patient.
    const layout = buildGraphLayout([
      trial(1, {
        matched: [
          { patientField: "patient_age", trialField: "age_low_limit", label: "Age Low Limit" },
        ],
        notMatched: [
          { patientField: "patient_age", trialField: "age_high_limit", label: "Age High Limit" },
        ],
      }),
    ]);

    expect(layout.concepts).toHaveLength(2);
    const byLabel = new Map(layout.concepts.map((c) => [c.label, c.status]));
    expect(byLabel.get("Age Low Limit")).toBe("matched");
    expect(byLabel.get("Age High Limit")).toBe("notMatched");
  });

  it("says the cut was alphabetical when nothing is shared", () => {
    const lonely = Array.from({ length: MAX_CONCEPTS + 5 }, (_, i) => item(`x${i}`));
    const layout = buildGraphLayout([trial(1, { missing: lonely })]);
    expect(layout.truncated).toBe(true);
    expect(layout.shared).toBe(false);
  });
});
