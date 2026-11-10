import { describe, expect, it } from "vitest";

import fixture from "./__fixtures__/promopDescriptor.v1.json";
import { resolveEditor, type EditorDecision } from "./editorResolver";
import type { WritableFields } from "./writable";

/**
 * The seam between PROMOP's descriptor and EXACT's resolver, pinned to one
 * producer SHA.
 *
 * The fixture is a curated sample of the real deployment descriptor (every kind
 * it emits, every alias, the documented special cases). Regenerate it when the
 * pinned PROMOP SHA changes; the pin is what makes a producer-side shape change
 * a review item here instead of a silent read-only row.
 */
const meta = fixture._meta;
const descriptors = fixture.fields as unknown as WritableFields;

function decide(
  name: string,
  options: {
    row?: Record<string, unknown>;
    surface?: "row" | "subform";
    entry?: Record<string, unknown>;
    matcherOverride?: boolean;
    negativeSource?: Record<string, unknown> | null;
  } = {},
): EditorDecision {
  return resolveEditor(name, descriptors, {
    surface: options.surface ?? "row",
    row: {
      upatientField: name,
      ureadonly: false,
      ...options.row,
    },
    entry: options.entry as never,
    matcherOverride: options.matcherOverride,
    negativeSource: options.negativeSource as never,
  });
}

const names = Object.keys(descriptors);
const ofKind = (kind: string) => names.filter((name) => descriptors[name].kind === kind);

/** The kinds this resolver implements. A producer kind outside this list must
 *  fail the fixture regeneration, not slip through as `unknown_kind`. */
const RESOLVER_KINDS = [
  "direct", "profile", "editable", "selectable", "computed",
  "alias", "negated", "explicit-answer", "authored", "unmapped",
];

describe("the pinned PROMOP descriptor fixture", () => {
  it("names the producer revision and the schema it was generated from", () => {
    expect(meta.source).toBe("healthkey-ai/promop");
    expect(meta.sha).toMatch(/^[0-9a-f]{40}$/);
    expect(meta.schema_version).toBe(1);
    expect(meta.descriptor_entry_count).toBeGreaterThan(meta.kinds.length);
  });

  it("covers every kind the producer emitted at that SHA", () => {
    const kinds = [...new Set(names.map((name) => descriptors[name].kind))].sort();
    expect(kinds).toEqual([...meta.kinds].sort());
    for (const kind of meta.kinds) {
      expect(ofKind(kind).length, `no fixture entry for kind ${kind}`).toBeGreaterThan(0);
    }
  });

  it("never leaves a kind on the silent path", () => {
    // `unavailable` means "descriptor has not arrived"; a pinned fixture that
    // is present must produce a real decision for every kind. An unknown kind
    // is a contract gap: it is refused loudly AND fails here, so regenerating
    // the fixture for a new producer kind cannot go green by itself.
    const unknown = meta.kinds.filter((kind) => !RESOLVER_KINDS.includes(kind));
    expect(unknown, "descriptor kinds this resolver does not implement").toEqual([]);
    for (const name of names) {
      const decision = decide(name);
      expect(decision.state, name).not.toBe("unavailable");
      if (decision.state === "unsupported") {
        expect(decision.diagnostic, name).not.toMatch(/^unknown_kind:/);
      }
    }
  });
});

describe("aliases", () => {
  it("are one-way and terminate at a canonical field the record describes", () => {
    for (const name of ofKind("alias")) {
      const canonical = descriptors[name].canonical;
      expect(canonical, name).toBeTruthy();
      expect(descriptors[canonical!], `${name} -> ${canonical}`).toBeDefined();
      // One hop only: a chain is a producer bug, not a resolver case.
      expect(descriptors[canonical!].kind, `${name} -> ${canonical}`).not.toBe("alias");
    }
  });

  it("maps the trial catalog's legacy cytogenic spelling to the record column", () => {
    const decision = decide("cytogenic_markers");
    expect(decision.field).toBe("cytogenetic_markers");
    expect(decision.state).not.toBe("unavailable");
    // The canonical is the read-only summary; individual findings go through
    // Genomics. The refusal must be a decision with a reason, not a dash.
    if (decision.state === "unsupported") {
      expect(decision.reason).toBeTruthy();
      expect(decision.owner).toBe("descriptor");
    }
  });

  it("maps the matcher's eGFR spelling to the canonical column", () => {
    expect(descriptors.estimated_glomerular_filtration_rate.canonical).toBe("egfr_ml_min_173m2");
    expect(decide("estimated_glomerular_filtration_rate").field).toBe("egfr_ml_min_173m2");
  });

  it("keeps race and ethnicity separate", () => {
    expect(descriptors.race.canonical).toBeUndefined();
    expect(descriptors.ethnicity.canonical).toBeUndefined();
    expect(decide("race").state).toBe("handled");
    expect(decide("ethnicity").state).toBe("handled");
  });
});

describe("permission and operation", () => {
  it("offers an attestation only through its own endpoint and actions", () => {
    for (const name of ofKind("explicit-answer")) {
      const decision = decide(name);
      expect(decision.state, name).toBe("handled");
      if (decision.state !== "handled") continue;
      expect(decision.editor.kind, name).toBe("explicit-answer");
      if (decision.editor.kind !== "explicit-answer") continue;
      expect(decision.editor.endpoint, name).toBeTruthy();
      expect(decision.editor.actions.length, name).toBeGreaterThan(0);
      // Every action it offers has to carry wording the dialog can show.
      for (const action of decision.editor.actions) {
        expect(decision.editor.actionLabels[action], `${name}:${action}`).toBeTruthy();
      }
    }
    // The attestation is never a field write.
    expect(descriptors.pre_existing_condition_categories.writable).toBe(false);
  });

  it("refuses a negated row without provenance and inverts the canonical with it", () => {
    const without = decide("no_hiv_status");
    expect(without.state).toBe("unsupported");
    if (without.state === "unsupported") {
      expect(without.diagnostic).toBe("negated_missing_provenance");
    }

    // Every negated row, not only HIV: each needs its own canonical and the
    // reviewed answer labels behind it.
    for (const name of ofKind("negated")) {
      const entry = descriptors[name];
      const canonical = String(entry.canonical);
      const labels = descriptors[canonical].answer_labels as
        | { true?: string; false?: string }
        | undefined;
      expect(labels, `${name}: ${canonical} has no answer_labels`).toBeTruthy();

      const decision = decide(name, {
        negativeSource: {
          upatientField: canonical,
          type: "boolean",
          available: true,
          value: false,
        },
      });
      expect(decision.state, name).toBe("handled");
      if (decision.state !== "handled") continue;
      expect(decision.editor.kind, name).toBe("negated");
      if (decision.editor.kind !== "negated") continue;
      expect(decision.editor.field, name).toBe(canonical);
      expect(decision.editor.invert, name).toBe(true);
      expect(decision.editor.positive, name).toBe(labels?.true);
      expect(decision.editor.negative, name).toBe(labels?.false);
    }
  });

  it("treats a read-only caller as a real refusal, not a missing field", () => {
    const decision = resolveEditor("hemoglobin_level", {
      ...descriptors,
      hemoglobin_level: {
        ...descriptors.hemoglobin_level,
        writable: false,
        read_only_for_caller: true,
      },
    }, {
      surface: "row",
      row: { upatientField: "hemoglobin_level", ureadonly: false },
    });
    expect(decision.state).toBe("unsupported");
    if (decision.state === "unsupported") {
      expect(decision.diagnostic).toBe("not_writable:direct");
      expect(decision.reason).toBeTruthy();
    }
  });

  it("never opens a field PATCH for a structured genomics value", () => {
    const decision = decide("genetic_mutations");
    expect(decision.state).toBe("unsupported");
    if (decision.state === "unsupported") {
      expect(decision.diagnostic).not.toBe("");
      expect(decision.reason).toBeTruthy();
    }
  });

  it("fails loud on a kind this version does not know", () => {
    const decision = resolveEditor("brand_new_field", {
      brand_new_field: {
        kind: "brand-new-editor" as never,
        writable: true,
        target: "patient_record",
      },
    }, {
      surface: "row",
      row: { upatientField: "brand_new_field", ureadonly: false },
    });
    expect(decision.state).toBe("unsupported");
    if (decision.state === "unsupported") {
      expect(decision.diagnostic).toBe("unknown_kind:brand-new-editor");
      expect(decision.owner).toBe("descriptor");
    }
  });
});

describe("recompute and subforms", () => {
  it("allows an inferred value only when the descriptor says it is overridable", () => {
    const row = { upatientRecomputed: true };

    const refused = decide("treatment_refractory_status", {
      row: { ...row, upatientRecomputedWhen: { when: "derived" } },
    });
    // Overridable in the descriptor, so the patient may replace the inference.
    expect(descriptors.treatment_refractory_status.overridable).toBe(true);
    expect(refused.state).toBe("handled");

    const notOverridable = {
      ...descriptors.treatment_refractory_status,
      overridable: false,
    };
    const decision = resolveEditor("treatment_refractory_status", {
      ...descriptors,
      treatment_refractory_status: notOverridable,
    }, {
      surface: "row",
      row: { upatientField: "treatment_refractory_status", ureadonly: false, ...row },
      matcherOverride: false,
    });
    expect(decision.state).toBe("unsupported");
    if (decision.state === "unsupported") {
      expect(decision.owner).toBe("matcher");
      expect(decision.diagnostic).toContain("recomputed:");
    }
  });

  it("offers a computed row as a subform over its inputs", () => {
    const decision = decide("molecular_markers");
    expect(decision.state).toBe("handled");
    if (decision.state === "handled") {
      expect(decision.editor.kind).toBe("computed-subform");
      if (decision.editor.kind !== "computed-subform") return;
      expect(decision.editor.inputs).toContain("genetic_mutations");
    }
  });

  it("does not offer a person-scoped input inside a threshold subform", () => {
    const decision = decide("gender", {
      surface: "subform",
      entry: { upatientField: "gender", type: "select" },
    });
    expect(decision.state).toBe("unsupported");
    if (decision.state === "unsupported") {
      expect(decision.diagnostic).toBe("person_scope_in_subform");
    }
  });
});

describe("control shape", () => {
  it("carries the toxicity scale as a select over its numeric grades", () => {
    const decision = decide("toxicity_grade");
    expect(decision.state).toBe("handled");
    if (decision.state !== "handled") return;
    expect(decision.editor.kind).toBe("scalar");
    if (decision.editor.kind !== "scalar") return;
    expect(decision.editor.control).toBe("select");
    // The producer's grades are numbers; the resolver normalises every option
    // value to its text form for the control. The server's IntegerField coerces
    // it back on write, which the producer test pins.
    expect(decision.editor.options.map((option) => option.value)).toEqual([
      "0", "1", "2", "3", "4",
    ]);
  });

  it("keeps a descriptor-declared multiselect an array, not a joined string", () => {
    // `multiple: true` in the descriptor means the control takes the list
    // directly; `joined` is reserved for comma-joined text the descriptor did
    // not declare. The row's own vocabulary picks the multiselect control.
    const decision = decide("stem_cell_transplant_history", {
      row: { utype: "multiselect" },
    });
    expect(decision.state).toBe("handled");
    if (decision.state !== "handled") return;
    expect(decision.editor.kind).toBe("scalar");
    if (decision.editor.kind !== "scalar") return;
    expect(decision.editor.control).toBe("multiselect");
    expect(decision.editor.multiple).toBe(true);
    expect(decision.editor.joined).toBe(false);
    expect(decision.editor.options.length).toBeGreaterThan(0);
    for (const option of decision.editor.options) {
      expect(String(option.value).trim(), "empty option value").not.toBe("");
      expect(String(option.label).trim(), "empty option label").not.toBe("");
    }
  });

  it("writes a source pair through the source field, not the normalised column", () => {
    const source = decide("hemoglobin_level");
    expect(source.state).toBe("handled");
    if (source.state !== "handled") return;
    expect(source.editor.kind).toBe("scalar");
    if (source.editor.kind === "scalar") {
      expect(source.editor.field).toBe("hemoglobin_level");
    }
    const canonical = decide("hemoglobin_g_dl");
    expect(canonical.state).toBe("handled");
    if (canonical.state === "handled") {
      expect(canonical.editor.kind).toBe("computed-subform");
      if (canonical.editor.kind !== "computed-subform") return;
      expect(canonical.editor.inputs).toEqual(["hemoglobin_level"]);
    }
  });
});
