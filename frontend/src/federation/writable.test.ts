import { describe, expect, it } from "vitest";

import { controlFor, editabilityOf, optionsOf } from "./writable";
import type { WritableFieldEntry, WritableFields } from "./writable";

const entry = (over: Partial<WritableFieldEntry> = {}): WritableFieldEntry => ({
  kind: "direct",
  writable: true,
  ...over,
});

describe("the two shapes PROMOP sends options in", () => {
  it("reads the curated-vocabulary shape, which has a value and no label", () => {
    expect(optionsOf(entry({ options: [{ value: "Partial response" }] }))).toEqual([
      { value: "Partial response", label: "Partial response" },
    ]);
  });

  it("writes the value and never the code beside it", () => {
    // The shape PROMOP actually sends for curator-managed choices and for
    // demographics. `code` is vocabulary metadata for the OMOP projection;
    // sending it would put `254837009` where the record wants a word.
    const opts = optionsOf(entry({
      options: [{ value: "Invasive ductal", code: "254837009" }],
    }));
    expect(opts).toEqual([{ value: "Invasive ductal", label: "Invasive ductal" }]);
    expect(opts[0].value).not.toBe("254837009");
  });

  it("ignores the extra coding on a cytogenetics option, including `display`", () => {
    // `display` there is the OMOP CONCEPT NAME, not a label for the value:
    // showing it would replace "del(17p)" with a sentence of nomenclature.
    const opts = optionsOf(entry({
      options: [{
        value: "del(17p)", code: "1220582008", vocabulary: "SNOMED",
        concept_id: 4300134, display: "Deletion of short arm of chromosome 17",
      }],
    }));
    expect(opts).toEqual([{ value: "del(17p)", label: "del(17p)" }]);
  });

  it("keeps the value when a choice has no code at all", () => {
    expect(optionsOf(entry({ options: [{ value: "Unknown", code: null }] }))).toEqual([
      { value: "Unknown", label: "Unknown" },
    ]);
  });

  it("reads the array form display-first, though PROMOP does not send it", () => {
    // Kept because the tuple it would come from still exists one function
    // inside PROMOP; if it ever reaches the wire the order is pinned here.
    expect(optionsOf(entry({ options: [["Invasive ductal", "254837009"]] })))
      .toEqual([{ value: "Invasive ductal", label: "Invasive ductal" }]);
  });

  it("honours an explicit label when one is sent", () => {
    expect(optionsOf(entry({ options: [{ value: "CR", label: "Complete response" }] })))
      .toEqual([{ value: "CR", label: "Complete response" }]);
  });

  it("takes a bare list of strings too", () => {
    expect(optionsOf(entry({ options: ["Yes", "No"] }))).toEqual([
      { value: "Yes", label: "Yes" },
      { value: "No", label: "No" },
    ]);
  });

  it("drops entries with nothing to send, rather than offering a blank choice", () => {
    // A blank option in a select writes an empty value when picked. Better to
    // not offer it: the reader can always leave the field alone.
    expect(optionsOf(entry({ options: [{ value: "" }, { value: null }, [""], ""] })))
      .toEqual([]);
  });

  it("is empty, not a crash, when options are absent or the wrong type", () => {
    expect(optionsOf(entry())).toEqual([]);
    expect(optionsOf(entry({ options: { a: 1 } }))).toEqual([]);
    expect(optionsOf(undefined)).toEqual([]);
  });
});

describe("choosing the control", () => {
  it("gives a select to anything carrying options, whatever its kind", () => {
    // `direct` fields carry options as readily as `selectable` ones, so the
    // presence of a bounded set decides this, not the kind.
    expect(controlFor(entry({ kind: "direct", options: [["A", null]] }))).toBe("select");
    expect(controlFor(entry({ kind: "selectable", options: [["A", null]] }))).toBe("select");
  });

  it("distinguishes a multi-valued field from a single choice", () => {
    expect(controlFor(entry({ options: [["A", null]], multiple: true }))).toBe("multiselect");
  });

  it("reads the value kind when there is no bounded set", () => {
    expect(controlFor(entry({ value_kind: "number" }))).toBe("number");
    expect(controlFor(entry({ value_kind: "boolean" }))).toBe("boolean");
    expect(controlFor(entry({ value_kind: "date" }))).toBe("date");
    expect(controlFor(entry({ value_kind: "string" }))).toBe("text");
  });

  it("falls back to text for a value kind it has never heard of", () => {
    expect(controlFor(entry({ value_kind: "quantity" }))).toBe("text");
    expect(controlFor(entry())).toBe("text");
  });
});

describe("deciding whether a row may be edited", () => {
  const fields: WritableFields = {
    hemoglobin_g_dl: entry({ value_kind: "number", unit: "g/dL" }),
    bmi: entry({
      kind: "computed",
      writable: false,
      reason: "Derived from height and weight; edit those instead.",
    }),
    hemoglobin_level: entry({
      kind: "alias",
      writable: false,
      canonical: "hemoglobin_g_dl",
      reason: "Mirrors hemoglobin_g_dl; edit that field instead.",
    }),
  };

  it("says yes for a writable field, and which control it wants", () => {
    const answer = editabilityOf("hemoglobin_g_dl", fields);
    expect(answer.can).toBe("edit");
    if (answer.can !== "edit") return;
    expect(answer.control).toBe("number");
    expect(answer.entry.unit).toBe("g/dL");
  });

  it("says no WITH the reason, so the row can explain itself", () => {
    const answer = editabilityOf("bmi", fields);
    expect(answer.can).toBe("no");
    if (answer.can !== "no") return;
    expect(answer.why).toBe("Derived from height and weight; edit those instead.");
  });

  it("names the field to edit instead of an alias", () => {
    const answer = editabilityOf("hemoglobin_level", fields);
    if (answer.can !== "no") throw new Error("expected no");
    expect(answer.why).toContain("hemoglobin_g_dl");
  });

  it("answers unknown while the descriptor has not arrived", () => {
    // Not "no": before the answer is known the page must look exactly as it
    // does today. A control drawn early is a control that may be taken away.
    expect(editabilityOf("hemoglobin_g_dl", undefined)).toEqual({ can: "unknown" });
  });

  it("answers unknown when EXACT could not name the attribute", () => {
    // 19 of EXACT's eligibility fields have no patient-record column, and
    // `upatientField` is null for them (#421). Nothing to look up.
    expect(editabilityOf(null, fields)).toEqual({ can: "unknown" });
    expect(editabilityOf(undefined, fields)).toEqual({ can: "unknown" });
    expect(editabilityOf("", fields)).toEqual({ can: "unknown" });
  });

  it("answers unknown for a name the descriptor does not carry", () => {
    // The descriptor documents the whole record, so this should not happen —
    // but if the two sides drift, the row must not sprout a control that
    // writes into nothing, and must not claim a reason it was never given.
    expect(editabilityOf("no_such_field", fields)).toEqual({ can: "unknown" });
  });

  it("refuses a control for a field written through another resource", () => {
    // `genetic_mutations` is `writable: true` with `target: "genomics"`.
    // PATCHing the record would write nothing at all, so the pencil must not
    // appear — and its own reason names where the edit does belong.
    const fields: WritableFields = {
      genetic_mutations: entry({
        kind: "editable",
        writable: true,
        target: "genomics",
        reason: "Edit individual variants in the Genomics tab.",
      }),
    };
    const answer = editabilityOf("genetic_mutations", fields);
    expect(answer.can).toBe("no");
    if (answer.can !== "no") return;
    expect(answer.why).toContain("Genomics tab");
  });

  it("still edits a field PROMOP routes onward from the record itself", () => {
    // Demographics carry `projection_target: "person"` but `target` stays
    // `patient_record` — PROMOP routes them internally from the same PATCH,
    // so refusing them would remove working controls.
    const fields: WritableFields = {
      gender: entry({ target: "patient_record", projection_target: "person",
                      options: [{ value: "Female", code: "F" }] }),
    };
    expect(editabilityOf("gender", fields).can).toBe("edit");
  });

  it("refuses a text box for a structured value", () => {
    const fields: WritableFields = {
      genomics_ngs: entry({ kind: "editable", writable: true, value_kind: "json" }),
    };
    const answer = editabilityOf("genomics_ngs", fields);
    expect(answer.can).toBe("no");
    if (answer.can !== "no") return;
    expect(answer.why).toContain("structured");
  });

  it("does not treat a note on a writable field as a refusal", () => {
    // PROMOP attaches `reason` to writable entries too — "Inferred by
    // default; enter a value to override". Reading it as an error would take
    // away a control that works.
    const fields: WritableFields = {
      relapse_count: entry({
        value_kind: "number",
        reason: "Inferred by default; enter a value to override.",
      }),
    };
    expect(editabilityOf("relapse_count", fields).can).toBe("edit");
  });

  it("does not let a writable:false entry through on kind alone", () => {
    // `kind` is descriptive; `writable` is the permission. A read-only caller
    // gets every entry back with `writable` flipped to false and the kinds
    // untouched, so reading the kind instead would put a box in front of
    // someone whose every save is refused.
    const readOnly: WritableFields = {
      hemoglobin_g_dl: entry({
        kind: "direct",
        writable: false,
        reason: "You have read-only access to this patient record.",
      }),
    };
    const answer = editabilityOf("hemoglobin_g_dl", readOnly);
    expect(answer.can).toBe("no");
  });
});
