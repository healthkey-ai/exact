// One decision per row (and per subform entry): may it be edited, through
// which editor, and if not, why exactly.
//
// The page currently answers that question in three places — `editableRowNames`
// for the pencil, `editabilityOf` for the editor, `SubformDialog.verdictFor`
// for the inputs — and each drops a different class of row. This module is the
// single policy those layers are meant to share. It is deliberately pure: no
// React, no fetches, no formatting. The caller supplies the trial row, the
// PROMOP descriptor entry and the surface (row or subform).
//
// Four dimensions are kept apart, because folding them into one `kind` is what
// caused the silent refusals:
//
//   * source      — who owns the canonical value (`direct`, `alias`, …);
//   * operation   — which editor can change it (scalar, subform, attestation);
//   * permission  — `writable` for a record write, `answerable` for the
//                   explicit-answer actions, never interchangeable;
//   * recompute   — whether EXACT overwrites the value after the write, and
//                   whether an override is supported.
//
// `unknown` is not a decision. A descriptor that has not arrived is
// `unavailable`; a field the descriptor does not name is `unsupported` with a
// diagnostic. Neither is allowed to render as a silent dash.

import type {
  EditControl,
  FieldOption,
  WritableFieldEntry,
  WritableFields,
} from "./writable";
import { canonicalFieldName, controlFor, optionsOf } from "./writable";
import type { SubformEntry, TrialDetailField } from "./types";

export type ResolverSurface = "row" | "subform";

export interface ScalarEditor {
  kind: "scalar";
  field: string;
  control: EditControl;
  options: FieldOption[];
  multiple: boolean;
  /** A multiselect over comma-joined text, not a JSON array. */
  joined: boolean;
  invert: boolean;
  /** `person` fields describe the patient, not a measurement; a threshold
   *  subform must not offer them. */
  scope: "record" | "person";
}

export interface ExplicitAnswerEditor {
  kind: "explicit-answer";
  field: string;
  endpoint: string;
  actions: string[];
  actionLabels: Record<string, string>;
  multiple: boolean;
  options: FieldOption[];
}

export interface ComputedSubformEditor {
  kind: "computed-subform";
  field: string;
  inputs: string[];
}

export interface AuthoredEditor {
  kind: "authored";
  field: string;
  target: string;
  reason?: string;
}

export type EditorSpec =
  | ScalarEditor
  | ExplicitAnswerEditor
  | ComputedSubformEditor
  | AuthoredEditor;

export type EditorDecision =
  | {
      state: "handled";
      field: string;
      editor: EditorSpec;
      /** Display-safe note, when the editor is not the obvious one. */
      note?: string;
    }
  | {
      state: "unsupported";
      field: string;
      /** Display-safe wording. Never PROMOP's integration `reason` verbatim. */
      reason: string;
      /** Machine code for tests and diagnostics. */
      diagnostic: string;
      /** Which side owns the gap: `descriptor`, `matcher`, `host`, `exact`. */
      owner: "descriptor" | "matcher" | "host" | "exact";
    }
  | {
      state: "unavailable";
      field: string;
      reason: string;
      diagnostic: string;
    };

export interface ResolveContext {
  surface: ResolverSurface;
  /** The trial row. Only the fields that affect the decision are required. */
  row?: Pick<
    TrialDetailField,
    | "upatientField"
    | "ureadonly"
    | "upatientRecomputed"
    | "upatientRecomputedWhen"
    | "subform_details"
    | "utype"
    | "uoptions"
  > | null;
  /** The subform entry, when resolving one. */
  entry?: SubformEntry | null;
  /** Whether the matcher has a supported override for a recomputed value.
   *  PROMOP accepting the write is not enough: EXACT must not overwrite it
   *  on the next match. */
  matcherOverride?: boolean;
}

/** Fields whose trial-side spelling is a legacy typo of the patient column.
 *  Read and write go through the canonical name; the alias is one-way and has
 *  no cycle. */
const FIELD_ALIASES: Readonly<Record<string, string>> = {
  cytogenic_markers: "cytogenetic_markers",
};

export function aliasTarget(name: string): string | null {
  return FIELD_ALIASES[name] ?? null;
}

function rawEntry(name: string, fields: WritableFields): WritableFieldEntry | undefined {
  return fields[name] ?? fields[canonicalFieldName(name)];
}

function isResourceInput(input: string): boolean {
  // Descriptor `inputs` may name model resources rather than PatientRecord
  // columns. Those are authored elsewhere (Episode/EpisodeEvent, genomics,
  // language skills), so a threshold subform must not pretend to edit them.
  return /^(Episode|EpisodeEvent|PersonLanguageSkill|Person|Measurement|Observation)\b/.test(
    input,
  );
}

function normalizeActions(entry: WritableFieldEntry): string[] {
  return Array.isArray(entry.actions)
    ? entry.actions.filter((a): a is string => typeof a === "string" && a !== "")
    : [];
}

function normalizeActionLabels(entry: WritableFieldEntry): Record<string, string> {
  const raw = entry.action_labels;
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(raw)) {
    if (typeof value === "string" && value !== "") out[key] = value;
  }
  return out;
}

function unsupported(
  field: string,
  reason: string,
  diagnostic: string,
  owner: "descriptor" | "matcher" | "host" | "exact",
): EditorDecision {
  return { state: "unsupported", field, reason, diagnostic, owner };
}

function unavailable(field: string, reason: string, diagnostic: string): EditorDecision {
  return { state: "unavailable", field, reason, diagnostic };
}

/** Resolve one row or subform entry. Pure; safe to call from render. */
export function resolveEditor(
  patientField: string | null | undefined,
  fields: WritableFields | undefined,
  context: ResolveContext,
): EditorDecision {
  const requested = (patientField ?? "").trim();
  if (!requested) {
    return unsupported(
      "",
      "This row does not name a patient field.",
      "missing_patient_field",
      "exact",
    );
  }
  if (!fields) {
    // Not a refusal: the descriptor has not arrived (or the host supplied no
    // writer). The row stays read-only and the page does not guess.
    return unavailable(
      requested,
      "The patient record is still loading.",
      "descriptor_unavailable",
    );
  }

  const seen = new Set<string>();
  // Apply the trial-side typo map before the first lookup: `rawEntry` would
  // find the canonical entry, but the decision would still carry the legacy
  // spelling.
  let current = canonicalFieldName(requested);
  let entry = rawEntry(current, fields);
  // Follow aliases and descriptors' own `canonical` links in one loop so a
  // cycle cannot recurse forever. `kind: alias`/`negated` are handled below;
  // here we only resolve the name the descriptor is keyed by.
  while (!entry) {
    const alias = aliasTarget(current);
    const next = alias ?? null;
    if (!next) break;
    if (seen.has(next)) {
      return unsupported(
        requested,
        "This field points at itself.",
        `alias_cycle:${[...seen, next].join("->")}`,
        "descriptor",
      );
    }
    seen.add(next);
    current = canonicalFieldName(next);
    entry = rawEntry(current, fields);
  }
  if (!entry) {
    return unsupported(
      requested,
      "This row names a patient field the record does not describe.",
      `unknown_field:${requested}`,
      "descriptor",
    );
  }

  // A row may be read-only in the trial template even when the record would
  // accept the write (a ×ULN ratio, a composed value). The row wins.
  if (context.row?.ureadonly === true) {
    return unsupported(
      current,
      "This row is shown for information and is not edited here.",
      "row_readonly",
      "exact",
    );
  }

  // Recompute handling. A write PROMOP accepts but EXACT replaces is an edit
  // that silently undoes itself; only an explicitly supported override is
  // allowed through.
  const recomputed = context.row?.upatientRecomputed === true;
  if (recomputed) {
    const overridable = entry.overridable === true;
    const supported = context.matcherOverride === true || overridable;
    if (!supported) {
      return unsupported(
        current,
        "This value is recomputed from other fields.",
        `recomputed:${context.row?.upatientRecomputedWhen?.when ?? "true"}`,
        "matcher",
      );
    }
  }

  const kind = typeof entry.kind === "string" ? entry.kind : "";
  return resolveEntry(current, entry, kind, fields, context, new Set(seen));
}

function resolveEntry(
  field: string,
  entry: WritableFieldEntry,
  kind: string,
  fields: WritableFields,
  context: ResolveContext,
  seen: Set<string>,
): EditorDecision {
  if (seen.has(field)) {
    return unsupported(
      field,
      "This field points at itself.",
      `alias_cycle:${[...seen, field].join("->")}`,
      "descriptor",
    );
  }
  seen.add(field);

  if (kind === "alias") {
    const canonical = typeof entry.canonical === "string" ? entry.canonical : "";
    if (!canonical || !rawEntry(canonical, fields)) {
      return unsupported(
        field,
        "This row mirrors a field the record does not describe.",
        `alias_missing_canonical:${canonical || "<none>"}`,
        "descriptor",
      );
    }
    return resolveEditorWithSeen(canonical, fields, context, seen);
  }

  if (kind === "negated") {
    const canonical = typeof entry.canonical === "string" ? entry.canonical : "";
    if (!canonical) {
      return unsupported(
        field,
        "This row does not name the field it writes.",
        "negated_missing_canonical",
        "descriptor",
      );
    }
    if (entry.inverted !== true || entry.value_kind !== "boolean") {
      return unsupported(
        field,
        "This row is not a yes/no view of another field.",
        "negated_not_boolean",
        "descriptor",
      );
    }
    const target = rawEntry(canonical, fields);
    if (!target) {
      return unsupported(
        field,
        "The field behind this row is not described.",
        `negated_missing_target:${canonical}`,
        "descriptor",
      );
    }
    const resolved = resolveEntry(canonical, target, String(target.kind ?? ""), fields, context, seen);
    if (resolved.state !== "handled" || resolved.editor.kind !== "scalar") {
      return resolved.state === "handled"
        ? unsupported(
            field,
            "This row is a yes/no view, but the field behind it is not a yes/no value.",
            "negated_target_not_scalar_boolean",
            "descriptor",
          )
        : resolved;
    }
    if (resolved.editor.control !== "boolean") {
      return unsupported(
        field,
        "This row is a yes/no view, but the field behind it is not a yes/no value.",
        "negated_target_not_boolean",
        "descriptor",
      );
    }
    return {
      state: "handled",
      field: canonical,
      editor: { ...resolved.editor, invert: true },
    };
  }

  if (kind === "explicit-answer" || entry.answerable === true) {
    const endpoint = typeof entry.endpoint === "string" ? entry.endpoint : "";
    const actions = normalizeActions(entry);
    if (entry.answerable !== true || !endpoint || actions.length === 0) {
      return unsupported(
        field,
        "This answer is not available for this patient.",
        "explicit_answer_incomplete",
        "descriptor",
      );
    }
    return {
      state: "handled",
      field,
      editor: {
        kind: "explicit-answer",
        field,
        endpoint,
        actions,
        actionLabels: normalizeActionLabels(entry),
        multiple: entry.multiple === true,
        options: optionsOf(entry),
      },
    };
  }

  if (kind === "authored") {
    const target = typeof entry.target === "string" ? entry.target : "resource";
    return {
      state: "handled",
      field,
      editor: {
        kind: "authored",
        field,
        target,
        reason: typeof entry.reason === "string" ? entry.reason : undefined,
      },
    };
  }

  if (kind === "computed") {
    const inputs = Array.isArray(entry.inputs)
      ? entry.inputs.filter((i): i is string => typeof i === "string" && i !== "")
      : [];
    if (inputs.length === 0) {
      return unsupported(
        field,
        "This value is computed and names no editable input.",
        "computed_no_inputs",
        "descriptor",
      );
    }
    if (inputs.some(isResourceInput)) {
      return unsupported(
        field,
        "This value is computed from a resource that is edited elsewhere.",
        `computed_resource_inputs:${inputs.filter(isResourceInput).join(",")}`,
        "host",
      );
    }
    if (context.surface === "subform") {
      return unsupported(
        field,
        "This value is computed from the row above it.",
        "computed_inside_subform",
        "exact",
      );
    }
    return {
      state: "handled",
      field,
      editor: { kind: "computed-subform", field, inputs },
      note: "This value is worked out from the fields below.",
    };
  }

  // Person-scoped demographics are legitimately editable from a row, but are
  // a different question when reached through a threshold dialog: changing
  // gender/race from the bilirubin row is a door into the wrong room.
  const scope: ScalarEditor["scope"] =
    kind === "profile"
    || entry.projection_target === "person"
    || entry.projection_target === "location"
      ? "person"
      : "record";
  if (context.surface === "subform" && scope === "person") {
    return unsupported(
      field,
      "This value describes the patient, not the threshold.",
      "person_scope_in_subform",
      "exact",
    );
  }

  const elsewhere =
    entry.writable && entry.target != null && entry.target !== "patient_record";
  if (elsewhere) {
    return unsupported(
      field,
      "This value is edited through another resource.",
      `authored_target:${String(entry.target)}`,
      "host",
    );
  }
  if (entry.writable !== true) {
    return unsupported(
      field,
      kind === "unmapped"
        ? "This value has no approved way to be edited here."
        : "This value is not editable here.",
      `not_writable:${kind || "unknown"}`,
      "descriptor",
    );
  }
  if (entry.value_kind === "json") {
    return unsupported(
      field,
      "This value is structured and cannot be edited as plain text here.",
      "structured_value",
      "descriptor",
    );
  }
  if (!kind || (kind !== "direct" && kind !== "profile" && kind !== "editable"
      && kind !== "selectable")) {
    // An unknown kind is a contract gap, not a decision. Fail with a
    // diagnostic; the UI may show the row as read-only, but the contract test
    // fails on the same code.
    return unsupported(
      field,
      "This value's editor is not supported by this version of the trial page.",
      `unknown_kind:${kind || "none"}`,
      "descriptor",
    );
  }

  const rowVocabulary = {
    type: context.row?.utype ?? context.entry?.type,
    options: context.row?.uoptions ?? context.entry?.options,
  };
  const control = controlFor(entry, rowVocabulary);
  const fromDescriptor = optionsOf(entry);
  const picker = control === "select" || control === "multiselect";
  return {
    state: "handled",
    field,
    editor: {
      kind: "scalar",
      field,
      control,
      options: fromDescriptor.length > 0
        ? fromDescriptor
        : picker
          ? optionsOf(rowVocabulary)
          : [],
      multiple: entry.multiple === true || control === "multiselect",
      joined: control === "multiselect" && entry.multiple !== true,
      invert: false,
      scope,
    },
  };
}

function resolveEditorWithSeen(
  field: string,
  fields: WritableFields,
  context: ResolveContext,
  seen: Set<string>,
): EditorDecision {
  const entry = rawEntry(field, fields);
  if (!entry) {
    return unsupported(
      field,
      "This row mirrors a field the record does not describe.",
      `alias_missing_canonical:${field}`,
      "descriptor",
    );
  }
  return resolveEntry(field, entry, String(entry.kind ?? ""), fields, context, seen);
}
