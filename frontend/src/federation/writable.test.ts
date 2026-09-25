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
    // Silent on the page, deliberately: this row never offered a control, so
    // nothing went missing for the reader. It is the case most worth
    // revisiting — the reason names the tab to edit the value in, which is
    // actionable — so the decision is pinned rather than left to prose.
    expect(answer.announce).toBe(false);
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
    // Same reasoning as the genomics row above: refused by the descriptor's
    // own shape, never offered a control, so it stays quiet.
    expect(answer.announce).toBe(false);
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

describe("attributes whose vocabulary has not finished moving to PROMOP", () => {
  // PROMOP describes these as writable strings with no options, so the editor
  // would draw a free text box — and the matcher parses the value as a list
  // of codes. A reader typing "English" stores something that matches
  // nothing, silently, on an attribute that gates eligibility.
  const unmappedEntry = entry({
    value_kind: "string",
    reason: "Written directly to PatientRecord. No OMOP mapping yet.",
  });

  it("offers no editor for one", () => {
    const answer = editabilityOf("languages_skills", {
      languages_skills: unmappedEntry,
    } as WritableFields);

    expect(answer.can).toBe("no");
    expect(answer.can === "no" && answer.why).toMatch(/still moving between services/);
  });

  it("says why in its own words, not the descriptor's", () => {
    // The descriptor's reason explains the OMOP mapping, not why the box a
    // reader expected is missing. Asserted as the NEGATIVE: the positive
    // duplicates the test above, since both strings are substrings of one
    // constant and no mutation kills one without the other.
    const answer = editabilityOf("languages_skills", {
      languages_skills: entry({
        value_kind: "string",
        reason: "Written directly to PatientRecord. No OMOP mapping yet.",
      }),
    } as WritableFields);

    expect(answer.can === "no" && answer.why).not.toMatch(/No OMOP mapping yet/);
  });

  it("marks its own refusals for announcement, and the descriptor's not", () => {
    // The reader saw a box on this row before and is owed the reason it went.
    // The 114 the descriptor itself refuses never offered one, so a sentence
    // on each of those is noise rather than news.
    const ours = editabilityOf("languages_skills", {
      languages_skills: entry({ value_kind: "string" }),
    } as WritableFields);
    const theirs = editabilityOf("anc_thousand_per_ul_alias", {
      anc_thousand_per_ul_alias: entry({
        writable: false,
        reason: "Mirrors anc_thousand_per_ul; edit that field instead.",
      }),
    } as WritableFields);

    expect(ours.can === "no" && ours.announce).toBe(true);
    expect(theirs.can === "no" && theirs.announce).toBe(false);
  });

  it("blocks FLIPI too, because the picker the argument relied on is display-only", () => {
    // This one was taken off the list and put back. The removal argued that
    // EXACT ships the vocabulary on the row in `uoptions`, so the row wanted a
    // multiselect — true about the vocabulary, false about the editor:
    // `uoptions` feeds `formatValue` and nothing else, and `controlFor` reads
    // the descriptor alone. Asserting the CONTROL is what would have caught
    // it: the earlier version of this test asserted only `can === "edit"`,
    // which is exactly as true of a free text box.
    const answer = editabilityOf("flipi_score_options", {
      flipi_score_options: entry({ value_kind: "string" }),
    } as WritableFields);

    expect(answer.can).toBe("no");
  });

  it("stays blocked when options arrive without `multiple`", () => {
    // The safety net has to be narrower than "options exist". Both columns
    // are read back as comma-separated lists, so a single `select` would save
    // one value and silently drop the rest — a different way to store an
    // answer nobody gave. Asked of the releasable field, or it would pass for
    // the wrong reason.
    const answer = editabilityOf("flipi_score_options", {
      flipi_score_options: entry({
        value_kind: "string",
        options: [{ value: "age", label: "Age over 60" }],
      }),
    } as WritableFields);

    expect(answer.can).toBe("no");
  });

  it("never releases the language field, however good the control", () => {
    // A multiselect over this column would be a perfectly good control
    // sending its answer down a path PROMOP says nothing should write. The
    // block is about the far end, not about the widget, so a usable control
    // is not enough to lift it.
    const answer = editabilityOf("languages_skills", {
      languages_skills: entry({
        value_kind: "string",
        multiple: true,
        options: [{ value: "write__en", label: "Write/Read English" }],
      }),
    } as WritableFields);

    expect(answer.can).toBe("no");
  });

  it("releases the field whose only problem was the missing picker", () => {
    // A safety net, not a retirement plan: it covers somebody wiring the
    // vocabulary properly and forgetting this file. Only for the field where
    // a control is genuinely the whole of what was wrong.
    const answer = editabilityOf("flipi_score_options", {
      flipi_score_options: entry({
        value_kind: "string",
        multiple: true,
        options: [{ value: "age", label: "Age over 60" }],
      }),
    } as WritableFields);

    expect(answer.can).toBe("edit");
    expect(answer.can === "edit" && answer.control).toBe("multiselect");
  });

  it("leaves every other optionless string alone", () => {
    // The block is a named list, not a rule about optionless strings — most
    // of them are ordinary free text and editing them works.
    const answer = editabilityOf("some_other_note", {
      some_other_note: entry({ value_kind: "string" }),
    } as WritableFields);

    expect(answer.can).toBe("edit");
    expect(answer.can === "edit" && answer.control).toBe("text");
  });
});
