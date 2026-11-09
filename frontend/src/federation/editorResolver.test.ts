import { describe, expect, it } from "vitest";

import {
  resolveEditor,
  toEditability,
  type EditorDecision,
} from "./editorResolver";
import { editabilityOf, type WritableFields } from "./writable";

const described = (): WritableFields => ({
  anc_thousand_per_ul: {
    kind: "direct",
    writable: true,
    target: "patient_record",
    value_kind: "number",
  },
  cytogenetic_markers: {
    kind: "direct",
    writable: true,
    target: "patient_record",
    value_kind: "string",
    multiple: true,
    options: [{ value: "del17p13", label: "Del(17p13)" }],
  },
  sex_at_birth: {
    kind: "profile",
    writable: true,
    target: "patient_record",
    projection_target: "person",
    value_kind: "string",
    options: [{ value: "Female", label: "Female" }],
  },
  no_hiv_status: {
    kind: "negated",
    writable: false,
    canonical: "hiv_status",
    inverted: true,
    value_kind: "boolean",
    inputs: ["hiv_status"],
  },
  hiv_status: {
    kind: "direct",
    writable: true,
    target: "patient_record",
    value_kind: "boolean",
    answer_labels: { true: "HIV Positive", false: "No HIV" },
  },
  pre_existing_condition_categories: {
    kind: "explicit-answer",
    writable: false,
    answerable: true,
    target: "patient_record",
    endpoint: "preexisting-conditions-answer",
    actions: ["confirm_none", "set_categories", "withdraw"],
    action_labels: { confirm_none: "No pre-existing conditions" },
    value_kind: "string",
    multiple: true,
  },
  estimated_glomerular_filtration_rate: {
    kind: "computed",
    writable: false,
    inputs: ["serum_creatinine_level", "patient_age", "gender"],
  },
  serum_creatinine_level: {
    kind: "direct",
    writable: true,
    target: "patient_record",
    value_kind: "number",
  },
  lines_of_therapy: {
    kind: "computed",
    writable: false,
    inputs: ["Episode", "EpisodeEvent"],
  },
  genetic_mutations: {
    kind: "authored",
    writable: false,
    target: "genomics",
  },
  languages_skills: {
    kind: "unmapped",
    writable: false,
    reason: "Derived from PersonLanguageSkill; write via the language-skills API.",
  },
  staging_modalities: {
    kind: "direct",
    writable: true,
    target: "patient_record",
    value_kind: "string",
  },
  treatment_refractory_status: {
    kind: "direct",
    writable: true,
    target: "patient_record",
    value_kind: "string",
  },
  structured_json: {
    kind: "direct",
    writable: true,
    target: "patient_record",
    value_kind: "json",
  },
});

const context = { surface: "row" as const };

function decision(result: EditorDecision, state: EditorDecision["state"]) {
  expect(result.state).toBe(state);
  return result;
}

describe("resolveEditor", () => {
  it("is unavailable, not unsupported, before the descriptor arrives", () => {
    const result = decision(resolveEditor("anc_thousand_per_ul", undefined, context), "unavailable");
    if (result.state === "unavailable") {
      expect(result.diagnostic).toBe("descriptor_unavailable");
    }
  });

  it("rejects a row that names no patient field", () => {
    const result = decision(resolveEditor(null, described(), context), "unsupported");
    if (result.state === "unsupported") expect(result.diagnostic).toBe("missing_patient_field");
  });

  it("reports a field the descriptor does not describe instead of a silent dash", () => {
    const result = decision(resolveEditor("unknown_field", described(), context), "unsupported");
    if (result.state === "unsupported") {
      expect(result.diagnostic).toBe("unknown_field:unknown_field");
      expect(result.owner).toBe("descriptor");
    }
  });

  it("resolves the legacy cytogenic spelling to cytogenetic_markers", () => {
    const result = decision(resolveEditor("cytogenic_markers", described(), context), "handled");
    if (result.state === "handled") {
      expect(result.field).toBe("cytogenetic_markers");
      expect(result.editor.kind).toBe("scalar");
      if (result.editor.kind === "scalar") {
        expect(result.editor.control).toBe("multiselect");
        expect(result.editor.multiple).toBe(true);
      }
    }
  });

  it("lets editabilityOf resolve the legacy cytogenic spelling to the descriptor entry", () => {
    const editable = editabilityOf("cytogenic_markers", described());
    expect(editable.can).toBe("edit");
    if (editable.can === "edit") {
      expect(editable.field).toBe("cytogenetic_markers");
      expect(editable.control).toBe("multiselect");
    }
  });

  it("normalizes a legacy alias target to the canonical write field", () => {
    const fields = described();
    fields.legacy_cytogenetics = {
      kind: "alias",
      writable: false,
      canonical: "cytogenic_markers",
    };
    const result = decision(resolveEditor("legacy_cytogenetics", fields, context), "handled");
    if (result.state === "handled") {
      expect(result.field).toBe("cytogenetic_markers");
      expect(result.editor.field).toBe("cytogenetic_markers");
    }
  });

  it("follows an alias to the canonical field", () => {
    const fields = described();
    fields.refractory_status = {
      kind: "alias",
      writable: false,
      canonical: "treatment_refractory_status",
    };
    const result = decision(resolveEditor("refractory_status", fields, context), "handled");
    if (result.state === "handled") {
      expect(result.field).toBe("treatment_refractory_status");
    }
  });

  it("refuses an alias cycle", () => {
    const fields = described();
    fields.a = { kind: "alias", writable: false, canonical: "b" };
    fields.b = { kind: "alias", writable: false, canonical: "a" };
    const result = decision(resolveEditor("a", fields, context), "unsupported");
    if (result.state === "unsupported") expect(result.diagnostic).toContain("alias_cycle");
  });

  it("turns a negated descriptor into a scalar editor over the canonical field", () => {
    const result = decision(
      resolveEditor("no_hiv_status", described(), {
        ...context,
        negativeSource: {
          upatientField: "hiv_status",
          available: true,
          value: false,
        },
      }),
      "handled",
    );
    if (result.state === "handled") {
      expect(result.field).toBe("hiv_status");
      expect(result.editor.kind).toBe("negated");
      if (result.editor.kind === "negated") {
        expect(result.editor.invert).toBe(true);
        expect(result.editor.control).toBe("boolean");
        expect(result.editor.positive).toBe("HIV Positive");
        expect(result.editor.negative).toBe("No HIV");
        expect(result.editor.source).toMatchObject({ upatientField: "hiv_status", available: true });
      }
    }
  });

  it("handles an explicit-answer entry through its endpoint, never a PATCH", () => {
    const result = decision(
      resolveEditor("pre_existing_condition_categories", described(), context),
      "handled",
    );
    if (result.state === "handled") {
      expect(result.editor.kind).toBe("explicit-answer");
      if (result.editor.kind === "explicit-answer") {
        expect(result.editor.endpoint).toBe("preexisting-conditions-answer");
        expect(result.editor.actions).toEqual([
          "confirm_none",
          "set_categories",
          "withdraw",
        ]);
      }
    }
  });

  it("refuses a negated source with no value key", () => {
    const result = decision(
      resolveEditor("no_hiv_status", described(), {
        ...context,
        negativeSource: { upatientField: "hiv_status", available: true },
      }),
      "unsupported",
    );
    if (result.state === "unsupported") {
      expect(result.diagnostic).toBe("negated_source_value_missing");
    }
  });

  it("accepts an explicit null or false negated source value", () => {
    for (const value of [null, false]) {
      decision(
        resolveEditor("no_hiv_status", described(), {
          ...context,
          negativeSource: { upatientField: "hiv_status", available: true, value },
        }),
        "handled",
      );
    }
  });

  it("refuses a negated row without provenance", () => {
    const result = decision(resolveEditor("no_hiv_status", described(), context), "unsupported");
    if (result.state === "unsupported") {
      expect(result.diagnostic).toBe("negated_missing_provenance");
    }
  });

  it("does not treat answerable=false as a permission to write", () => {
    const fields = described();
    fields.pre_existing_condition_categories = {
      ...fields.pre_existing_condition_categories,
      answerable: false,
    };
    decision(resolveEditor("pre_existing_condition_categories", fields, context), "unsupported");
  });

  it("offers the inputs of a computed row even when the row is recomputed", () => {
    const result = decision(
      resolveEditor("estimated_glomerular_filtration_rate", described(), {
        ...context,
        row: { upatientRecomputed: true },
      }),
      "handled",
    );
    if (result.state === "handled") {
      expect(result.editor.kind).toBe("computed-subform");
      if (result.editor.kind === "computed-subform") {
        expect(result.editor.inputs).toContain("serum_creatinine_level");
      }
    }
  });

  it("uses the child entry metadata, not the computed parent row", () => {
    const result = decision(
      resolveEditor("serum_creatinine_level", described(), {
        surface: "subform",
        row: { upatientRecomputed: true, utype: "multiselect", uoptions: [{ value: "x", label: "X" }] },
        entry: { name: "serumCreatinineLevel", label: "Serum Creatinine", type: "float", value: 1 },
      }),
      "handled",
    );
    if (result.state === "handled" && result.editor.kind === "scalar") {
      expect(result.editor.control).toBe("number");
      expect(result.editor.options).toEqual([]);
    }
  });

  it("refuses a recomputed subform entry before reaching its editor", () => {
    const result = decision(
      resolveEditor("serum_creatinine_level", described(), {
        surface: "subform",
        entry: { name: "serumCreatinineLevel", label: "Serum Creatinine", type: "float", value: 1, upatientRecomputed: true },
      }),
      "unsupported",
    );
    if (result.state === "unsupported") {
      expect(result.diagnostic).toBe("recomputed:true");
    }
  });

  it("does not offer a computed row inside its own subform", () => {
    const result = decision(
      resolveEditor("estimated_glomerular_filtration_rate", described(), {
        surface: "subform",
      }),
      "unsupported",
    );
    if (result.state === "unsupported") expect(result.diagnostic).toBe("computed_inside_subform");
  });

  it("routes a computed value whose inputs are resources to the host, not a subform", () => {
    const result = decision(resolveEditor("lines_of_therapy", described(), context), "unsupported");
    if (result.state === "unsupported") {
      expect(result.owner).toBe("host");
      expect(result.diagnostic).toContain("computed_resource_inputs");
    }
  });

  it("offers authored resources through their own editor", () => {
    const result = decision(resolveEditor("genetic_mutations", described(), context), "handled");
    if (result.state === "handled") {
      expect(result.editor.kind).toBe("authored");
      if (result.editor.kind === "authored") expect(result.editor.target).toBe("genomics");
    }
  });

  it("returns the authored recipe when the descriptor publishes one", () => {
    const fields = described();
    fields.authored_therapy = {
      kind: "authored",
      writable: false,
      authored_via: { target: "episode", endpoint: "/api/v1/episodes/" },
    };
    const result = decision(resolveEditor("authored_therapy", fields, context), "handled");
    if (result.state === "handled" && result.editor.kind === "authored") {
      expect(result.editor.target).toBe("episode");
      expect(result.editor.recipe).toEqual({ target: "episode", endpoint: "/api/v1/episodes/" });
    }
  });

  it("refuses an unmapped or non-writable entry with an explicit reason", () => {
    const result = decision(resolveEditor("languages_skills", described(), context), "unsupported");
    if (result.state === "unsupported") expect(result.diagnostic).toBe("not_writable:unmapped");
  });

  it("keeps the unmapped-vocabulary guard even when the descriptor says writable", () => {
    const fields = described();
    fields.languages_skills = {
      kind: "direct",
      writable: true,
      target: "patient_record",
      value_kind: "string",
    };
    const result = decision(resolveEditor("languages_skills", fields, context), "unsupported");
    if (result.state === "unsupported") {
      expect(result.diagnostic).toBe("unmapped_vocabulary:languages_skills");
    }
  });

  it("refuses an unknown kind with a diagnostic instead of guessing", () => {
    const fields = described();
    fields.future_kind = { kind: "telepathic", writable: true, target: "patient_record" };
    const result = decision(resolveEditor("future_kind", fields, context), "unsupported");
    if (result.state === "unsupported") {
      expect(result.diagnostic).toBe("unknown_kind:telepathic");
    }
  });

  it("takes the row vocabulary when the descriptor has no options", () => {
    const fields = described();
    fields.stage = { kind: "direct", writable: true, target: "patient_record", value_kind: "string" };
    const result = decision(
      resolveEditor("stage", fields, {
        ...context,
        row: { utype: "select", uoptions: [{ value: "III", label: "Stage III" }] },
      }),
      "handled",
    );
    if (result.state === "handled" && result.editor.kind === "scalar") {
      expect(result.editor.control).toBe("select");
      expect(result.editor.options).toEqual([{ value: "III", label: "Stage III" }]);
      expect(result.editor.joined).toBe(false);
    }
  });

  it("marks a multiselect over comma-joined text as joined", () => {
    const fields = described();
    const result = decision(
      resolveEditor("staging_modalities", fields, {
        ...context,
        row: { utype: "multiselect", uoptions: [{ value: "CT", label: "CT" }] },
      }),
      "handled",
    );
    if (result.state === "handled" && result.editor.kind === "scalar") {
      expect(result.editor.control).toBe("multiselect");
      expect(result.editor.joined).toBe(true);
    }
  });

  it("treats a profile kind as person-scoped even without projection_target", () => {
    const fields = described();
    fields.sex_at_birth = {
      kind: "profile",
      writable: true,
      target: "patient_record",
      value_kind: "string",
      options: [{ value: "Female", label: "Female" }],
    };
    const result = decision(
      resolveEditor("sex_at_birth", fields, { surface: "subform" }),
      "unsupported",
    );
    if (result.state === "unsupported") {
      expect(result.diagnostic).toBe("person_scope_in_subform");
    }
  });

  it("refuses an entry with no kind", () => {
    const fields = described();
    fields.no_kind = { writable: true, target: "patient_record" } as never;
    const result = decision(resolveEditor("no_kind", fields, context), "unsupported");
    if (result.state === "unsupported") expect(result.diagnostic).toBe("unknown_kind:none");
  });

  it("offers person demographics on a row but not inside a threshold subform", () => {
    const row = decision(resolveEditor("sex_at_birth", described(), context), "handled");
    if (row.state === "handled" && row.editor.kind === "scalar") {
      expect(row.editor.scope).toBe("person");
    }
    const subform = decision(
      resolveEditor("sex_at_birth", described(), { surface: "subform" }),
      "unsupported",
    );
    if (subform.state === "unsupported") {
      expect(subform.diagnostic).toBe("person_scope_in_subform");
    }
  });

  it("refuses a recomputed value unless the matcher supports an override", () => {
    const row = { upatientRecomputed: true, upatientRecomputedWhen: { when: "sometimes" as const, condition: "when inputs change" } };
    const refused = decision(
      resolveEditor("treatment_refractory_status", described(), { ...context, row }),
      "unsupported",
    );
    if (refused.state === "unsupported") expect(refused.owner).toBe("matcher");
    const allowed = decision(
      resolveEditor("treatment_refractory_status", described(), {
        ...context,
        row,
        matcherOverride: true,
      }),
      "handled",
    );
    if (allowed.state === "handled") expect(allowed.editor.kind).toBe("scalar");
  });

  it("adapts a scalar decision to the editor shape the page consumes", () => {
    const fields = described();
    const decision = resolveEditor("anc_thousand_per_ul", fields, context);
    const editable = toEditability(decision, fields);
    expect(editable.can).toBe("edit");
    if (editable.can === "edit") {
      expect(editable.field).toBe("anc_thousand_per_ul");
      expect(editable.control).toBe("number");
    }
  });

  it("adapts a negated decision with its source and labels", () => {
    const fields = described();
    const decision = resolveEditor("no_hiv_status", fields, {
      ...context,
      negativeSource: { upatientField: "hiv_status", available: true, value: false },
    });
    const editable = toEditability(decision, fields);
    expect(editable.can).toBe("edit");
    if (editable.can === "edit") {
      expect(editable.field).toBe("hiv_status");
      expect(editable.invert).toBe(true);
      expect(editable.negative?.positive).toBe("HIV Positive");
    }
  });

  it("adapts an unsupported decision to a refusal with the reason", () => {
    const fields = described();
    const editable = toEditability(
      resolveEditor("structured_json", fields, context),
      fields,
    );
    expect(editable.can).toBe("no");
    if (editable.can === "no") {
      expect(editable.why).toContain("structured");
    }
  });

  it("honours an overridable descriptor flag for a recomputed value", () => {
    const fields = described();
    fields.treatment_refractory_status = {
      ...fields.treatment_refractory_status,
      overridable: true,
    };
    decision(
      resolveEditor("treatment_refractory_status", fields, {
        ...context,
        row: { upatientRecomputed: true },
      }),
      "handled",
    );
  });

  it("refuses a structured value", () => {
    const result = decision(resolveEditor("structured_json", described(), context), "unsupported");
    if (result.state === "unsupported") expect(result.diagnostic).toBe("structured_value");
  });

  it("respects a row marked read-only by the trial template", () => {
    const result = decision(
      resolveEditor("anc_thousand_per_ul", described(), {
        ...context,
        row: { ureadonly: true },
      }),
      "unsupported",
    );
    if (result.state === "unsupported") expect(result.diagnostic).toBe("row_readonly");
  });
});
