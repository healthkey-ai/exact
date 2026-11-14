import { describe, expect, it } from "vitest";

import { controlFor, editabilityOf, invertBoolean, negatedEditability, optionsOf } from "./writable";
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
    no_hiv_status: entry({
      kind: "negated",
      writable: false,
      canonical: "hiv_status",
      inverted: true,
      value_kind: "boolean",
      reason: "Inverted view of hiv_status.",
    }),
    hiv_status: entry({ value_kind: "boolean" }),
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

  it("edits the canonical field an alias mirrors", () => {
    // The descriptor says "edit that field instead", and this row is the only
    // place a reader meets it. Refusing here left the row with no control at
    // all; the save goes to the field the alias names.
    const answer = editabilityOf("hemoglobin_level", fields);
    if (answer.can !== "edit") throw new Error("expected edit");
    expect(answer.field).toBe("hemoglobin_g_dl");
    expect(answer.entry.unit).toBe("g/dL");
  });

  it("still refuses an alias whose canonical is not editable", () => {
    const answer = editabilityOf("hemoglobin_level", {
      ...fields,
      hemoglobin_g_dl: entry({ kind: "computed", writable: false, reason: "No." }),
    });
    if (answer.can !== "no") throw new Error("expected no");
    expect(answer.why).toContain("No.");
  });

  it("edits the positive field a negated view names, and remembers the polarity", () => {
    // "No HIV required" asks the opposite of the stored `hiv_status`. The row
    // must write the positive field, and the value must cross the boundary —
    // writing the displayed answer straight through would store the opposite
    // of what the reader chose.
    const answer = editabilityOf("no_hiv_status", fields);
    if (answer.can !== "edit") throw new Error("expected edit");
    expect(answer.field).toBe("hiv_status");
    expect(answer.control).toBe("boolean");
    expect(answer.invert).toBe(true);
  });

  it("does not invert an ordinary boolean", () => {
    const answer = editabilityOf("hiv_status", fields);
    if (answer.can !== "edit") throw new Error("expected edit");
    expect(answer.field).toBe("hiv_status");
    expect(answer.invert).toBe(false);
  });

  it("inverts booleans in both directions and leaves an unknown unknown", () => {
    expect(invertBoolean(true)).toBe(false);
    expect(invertBoolean(false)).toBe(true);
    // `!null` is true. A missing answer must not become a confident "yes".
    expect(invertBoolean(null)).toBe(null);
    expect(invertBoolean(undefined)).toBe(undefined);
  });

  it("refuses a negated view whose target is not a yes/no field", () => {
    const answer = editabilityOf("no_hiv_status", {
      ...fields,
      hiv_status: entry({ value_kind: "number" }),
    });
    // Refused rather than offered: this branch can only be reached by a
    // descriptor that broke its own contract, and a yes/no box over a number
    // would write a value nobody can invert meaningfully.
    if (answer.can !== "no") throw new Error("expected no");
    expect(answer.field).toBe("hiv_status");
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

  it("stays quiet about a destination it has no wording for", () => {
    // A target PROMOP adds tomorrow. The fallback sentence — "This field is
    // edited elsewhere in the patient record" — is a reason in form and not
    // in substance: it tells the reader there is a door and refuses to name
    // it. And printing PROMOP's own string instead is the thing this avoids,
    // since nobody here has read it. Silence until somebody writes a sentence.
    const fields: WritableFields = {
      some_relocated_field: entry({
        kind: "editable",
        writable: true,
        target: "somewhere_new",
        reason: "Written via the widgets resource; see docs/widgets.md.",
      }),
    };
    const answer = editabilityOf("some_relocated_field", fields);

    expect(answer.can).toBe("no");
    expect(answer.can === "no" && answer.announce).toBe(false);
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
    // EXACT's sentence, not PROMOP's. The descriptor's reason here is
    // "Edit individual variants in the Genomics tab." — good prose, and not
    // ours to print: `reason` is documented as carried and not rendered
    // because some of those strings are written for integrators, and EXACT
    // is a federated remote that cannot know the host has a Genomics tab.
    expect(answer.why).toContain("genomics part of the record");
    expect(answer.why).not.toContain("tab");
    // Said out loud, though. Without it the honest conclusion from a row
    // with no pencil is that the record cannot hold their variants.
    expect(answer.announce).toBe(true);
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

  it("blocks FLIPI when no row vocabulary reaches it", () => {
    // This one was taken off the list and put back, and is now lifted by
    // `controlFor` instead — but only where the row actually carries the
    // codes. Called without a row, the block is still what stands between the
    // reader and a free text box over a column of codes. Asserting the
    // CONTROL is what caught the original mistake: the first version of this
    // test asserted only `can === "edit"`, which is exactly as true of a
    // text box.
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

describe("the vocabulary the row carries", () => {
  // EXACT builds some of these value sets itself (`value_options.py`) and
  // ships them on the attribute row. They were reaching the page and being
  // used for DISPLAY only, while the editor asked PROMOP's descriptor, got
  // nothing, and drew a free text box over a column the matcher reads as a
  // list of codes.
  const FLIPI = [
    { value: "age", label: "Age over 60" },
    { value: "stage", label: "Ann Arbor III or IV" },
  ];

  it("falls back to it when the descriptor is silent", () => {
    const control = controlFor(entry({ value_kind: "string" }), {
      type: "multiselect",
      options: FLIPI,
    });

    expect(control).toBe("multiselect");
  });

  it("gives a single select when the row does not say multiselect", () => {
    const control = controlFor(entry({ value_kind: "string" }), {
      type: "string",
      options: FLIPI,
    });

    expect(control).toBe("select");
  });

  it("leaves a kind the descriptor named alone", () => {
    // `ecog_performance_status` and four others are `value_kind: "number"`
    // with an option list on the row. A select there would send "3" where the
    // column had been getting 3, which is a change this one is not making.
    const control = controlFor(entry({ value_kind: "number" }), {
      type: "select",
      options: [{ value: 0 }, { value: 2 }],
    });

    expect(control).toBe("number");
  });

  it("hands a picker no list it will not draw", () => {
    const answer = editabilityOf(
      "some_other_note",
      { some_other_note: entry({ value_kind: "number" }) } as WritableFields,
      { type: "select", options: [{ value: 0 }] },
    );

    expect(answer.can === "edit" && answer.options).toEqual([]);
  });

  it("lets the descriptor's `multiple` outrank the row's widget", () => {
    // The row is the authority on the VOCABULARY, never on the column's
    // shape. A single select over a list column would replace the whole list
    // with one value, and nothing would report it.
    const control = controlFor(entry({ value_kind: "string", multiple: true }), {
      type: "select",
      options: FLIPI,
    });

    expect(control).toBe("multiselect");
  });

  it("keeps the descriptor's VALUES even when the row offers its own", () => {
    // PROMOP is where these value sets are migrating TO. A row disagreeing
    // with a descriptor that has an answer would be the old copy winning.
    const answer = editabilityOf(
      "some_other_note",
      {
        some_other_note: entry({
          value_kind: "string",
          options: [{ value: "Yes" }],
        }),
      } as WritableFields,
      { type: "multiselect", options: FLIPI },
    );

    expect(answer.can === "edit" && answer.options.map((o) => o.value)).toEqual([
      "Yes",
    ]);
  });

  it("lets the row say HOW MANY when the descriptor only says which", () => {
    // The other half, and the one that was wrong. `staging_modalities` is
    // `value_kind: "string"` with three descriptor options and `multiple:
    // null`, over a comma-joined column the matcher reads as a list — so a
    // single select replaced the whole list with one value, silently, on an
    // attribute that gates eligibility (#585).
    //
    // Cardinality only. The values above still come from the descriptor.
    const control = controlFor(
      entry({ value_kind: "string", options: [{ value: "CT" }, { value: "PET" }] }),
      { type: "multiselect", options: [{ value: "CT" }] },
    );

    expect(control).toBe("multiselect");
  });

  it("does not let the row DOWNGRADE a declared list", () => {
    // `multiple: true` is a statement, and a row saying `select` over a list
    // column would put the truncation back.
    const control = controlFor(
      entry({ value_kind: "string", multiple: true, options: [{ value: "CT" }] }),
      { type: "select", options: [{ value: "CT" }] },
    );

    expect(control).toBe("multiselect");
  });

  it("hands the resolved list to the caller, whichever source it came from", () => {
    const answer = editabilityOf(
      "some_other_note",
      { some_other_note: entry({ value_kind: "string" }) } as WritableFields,
      { type: "multiselect", options: FLIPI },
    );

    expect(answer.can).toBe("edit");
    expect(answer.can === "edit" && answer.options.map((o) => o.value)).toEqual([
      "age",
      "stage",
    ]);
  });

  it("retires the FLIPI block, which is what the block was waiting for", () => {
    // The block existed because there was no picker. The row has always
    // carried one; the editor just never looked. Now it does, and the entry
    // in `UNMAPPED_VOCABULARY` lifts on its own.
    const answer = editabilityOf(
      "flipi_score_options",
      { flipi_score_options: entry({ value_kind: "string" }) } as WritableFields,
      { type: "multiselect", options: FLIPI },
    );

    expect(answer.can).toBe("edit");
    expect(answer.can === "edit" && answer.control).toBe("multiselect");
  });

  it("marks a row-derived multiselect as joined, because its column is text", () => {
    const answer = editabilityOf(
      "some_other_note",
      { some_other_note: entry({ value_kind: "string" }) } as WritableFields,
      { type: "multiselect", options: FLIPI },
    );

    expect(answer.can === "edit" && answer.joined).toBe(true);
  });

  it("leaves a descriptor multiselect unjoined, because its column is a list", () => {
    // `multiple` is the signal, and PROMOP declares it on exactly two
    // attributes — the only two whose serializer field takes a JSON array.
    const answer = editabilityOf(
      "some_other_note",
      {
        some_other_note: entry({
          value_kind: "string",
          multiple: true,
          options: [{ value: "autologous SCT" }],
        }),
      } as WritableFields,
    );

    expect(answer.can === "edit" && answer.control).toBe("multiselect");
    expect(answer.can === "edit" && answer.joined).toBe(false);
  });

  it("does not retire the language block, which was never about the picker", () => {
    // Its row carries a vocabulary too, and a perfectly good multiselect can
    // be drawn from it — asserted here so this test notices if the fallback
    // stops working, rather than passing for the wrong reason. It stays
    // blocked anyway: PROMOP denormalizes that column from
    // `PersonLanguageSkill`, so the control would be writing down a path
    // nothing should write.
    const descriptor = entry({ value_kind: "string" });
    const languages = {
      type: "multiselect",
      options: [{ value: "write__en", label: "Write/Read English" }],
    };

    expect(controlFor(descriptor, languages)).toBe("multiselect");

    const answer = editabilityOf(
      "languages_skills",
      { languages_skills: descriptor } as WritableFields,
      languages,
    );

    expect(answer.can).toBe("no");
  });
});

describe("the dedicated negated dialog (#5667)", () => {
  const fields: WritableFields = {
    no_hiv_status: entry({
      kind: "negated",
      writable: false,
      canonical: "hiv_status",
      inverted: true,
      value_kind: "boolean",
      inputs: ["hiv_status"],
    }),
    hiv_status: entry({
      value_kind: "boolean",
      target: "patient_record",
      answer_labels: { true: "HIV Positive", false: "No HIV" },
    }),
  };
  const source = {
    upatientField: "hiv_status",
    type: "boolean",
    available: true,
    value: null as unknown,
  };

  it("offers the canonical field with the positive answer labels", () => {
    const answer = negatedEditability("no_hiv_status", fields, source);
    if (answer.can !== "edit") throw new Error("expected edit");
    expect(answer.field).toBe("hiv_status");
    expect(answer.control).toBe("boolean");
    expect(answer.invert).toBe(true);
    expect(answer.negative?.positive).toBe("HIV Positive");
    expect(answer.negative?.negative).toBe("No HIV");
    expect(answer.negative?.source.value).toBe(null);
  });

  it("does not offer an editor where the context never carried the field", () => {
    // `available: false` is not "answered unknown": there is no seed and no
    // evidence the row is answerable here, so the dialog is not drawn.
    const answer = negatedEditability(
      "no_hiv_status", fields, { ...source, available: false },
    );
    expect(answer.can).toBe("no");
  });

  it.each([
    ["provenance for another field", { ...source, upatientField: "hepatitis_b_status" }, fields],
    ["provenance with no available flag", { ...source, available: undefined as unknown as boolean }, fields],
    ["provenance that says available but carries no value", { available: true, upatientField: "hiv_status" }, fields],
    ["provenance carrying a non-boolean answer", { ...source, value: "yes" }, fields],
    ["provenance naming another type", { ...source, type: "number" }, fields],
    ["inputs that name a different field", source, { ...fields, no_hiv_status: entry({ ...fields.no_hiv_status, inputs: ["hepatitis_b_status"] }) }],
    ["two inputs where the pair is one", source, { ...fields, no_hiv_status: entry({ ...fields.no_hiv_status, inputs: ["hiv_status", "hepatitis_b_status"] }) }],
    ["no explicit inversion", source, { ...fields, no_hiv_status: entry({ ...fields.no_hiv_status, inverted: false }) }],
    ["a canonical that is not direct", source, { ...fields, hiv_status: entry({ ...fields.hiv_status, kind: "alias" }) }],
    ["a canonical that is not writable", source, { ...fields, hiv_status: entry({ ...fields.hiv_status, writable: false }) }],
    ["a canonical targeting another resource", source, { ...fields, hiv_status: entry({ ...fields.hiv_status, target: "genomics" }) }],
    ["a canonical that is not a boolean", source, { ...fields, hiv_status: entry({ ...fields.hiv_status, value_kind: "number" }) }],
    ["an options-driven control posing as yes/no", source, { ...fields, hiv_status: entry({ ...fields.hiv_status, options: [{ value: "pos" }] }) }],
    ["no answer labels on the canonical", source, { ...fields, hiv_status: entry({ ...fields.hiv_status, answer_labels: undefined }) }],
    ["half an answer label pair", source, { ...fields, hiv_status: entry({ ...fields.hiv_status, answer_labels: { true: "HIV Positive" } }) }],
  ])("refuses %s", (_name, provenance, descriptor) => {
    const answer = negatedEditability(
      "no_hiv_status", descriptor as WritableFields, provenance as typeof source,
    );
    expect(answer.can).not.toBe("edit");
  });

  it("keeps the legacy generic editor for a descriptor without the pair", () => {
    // The page chooses this guard over `editabilityOf` when the negated entry
    // carries the `inputs` key. A descriptor that predates the pair has no
    // such key, and its rows must keep the editor they had.
    const answer = editabilityOf("no_hiv_status", fields);
    expect(answer.can).toBe("edit");
  });
});

