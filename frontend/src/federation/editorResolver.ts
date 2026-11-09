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
import {
  answerLabels,
  canonicalFieldName,
  controlFor,
  optionsOf,
  unmappedVocabulary,
} from "./writable";
import type { NegativeSource, SubformEntry, TrialDetailField } from "./types";

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

export interface NegatedEditor {
  kind: "negated";
  /** The canonical boolean the dialog writes. The displayed row is its
   *  opposite; the dialog itself saves the positive answer. */
  field: string;
  control: "boolean";
  invert: true;
  /** Provenance from the row (`negative_source`). */
  source: NegativeSource;
  /** The two answer labels the dialog shows. */
  positive: string;
  negative: string;
  options: [];
  multiple: false;
  joined: false;
  scope: "record";
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
  /** The descriptor's authoring recipe, when it publishes one
   *  (`authored_via`). The host uses it to choose the resource editor. */
  recipe?: unknown;
  reason?: string;
}

export type EditorSpec =
  | ScalarEditor
  | NegatedEditor
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
  /** Provenance for a negated row's canonical field (#5667). The dedicated
   *  dialog requires it; without it the resolver refuses the editor. */
  negativeSource?: NegativeSource | null;
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

  // Metadata belongs to the surface being resolved. A subform call may carry
  // both the parent row and the child entry; taking the parent's flags would
  // mark an editable input as recomputed and would let the parent's
  // vocabulary override the child's picker.
  const rowMeta = context.surface === "row" ? context.row : null;
  const entryMeta = context.surface === "subform" ? context.entry : null;

  // A row may be read-only in the trial template even when the record would
  // accept the write (a ×ULN ratio, a composed value). The row wins.
  if (rowMeta?.ureadonly === true) {
    return unsupported(
      current,
      "This row is shown for information and is not edited here.",
      "row_readonly",
      "exact",
    );
  }

  const kind = typeof entry.kind === "string" ? entry.kind : "";

  // Recompute handling. A write PROMOP accepts but EXACT replaces is an edit
  // that silently undoes itself; only an explicitly supported override is
  // allowed through. A computed row is the exception: its value is derived,
  // but its INPUTS are the edit path — refusing here would make every
  // computed subform unreachable. Subform entries carry their own flag.
  const entryRecomputed = entryMeta?.upatientRecomputed === true;
  const recomputed = rowMeta?.upatientRecomputed === true || entryRecomputed;
  if (recomputed && kind !== "computed") {
    const overridable = entry.overridable === true;
    const supported = context.matcherOverride === true || overridable;
    if (!supported) {
      return unsupported(
        current,
        "This value is recomputed from other fields.",
        `recomputed:${
          (entryRecomputed
            ? entryMeta?.upatientRecomputedWhen?.when
            : rowMeta?.upatientRecomputedWhen?.when) ?? "true"
        }`,
        "matcher",
      );
    }
  }

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
    const canonical = canonicalFieldName(
      typeof entry.canonical === "string" ? entry.canonical : "",
    );
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
    const canonical = canonicalFieldName(
      typeof entry.canonical === "string" ? entry.canonical : "",
    );
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
    const inputs = Array.isArray(entry.inputs) ? entry.inputs : [];
    if (inputs.length !== 1 || inputs[0] !== canonical) {
      return unsupported(
        field,
        "This row's inputs do not name the field it writes.",
        "negated_inputs_mismatch",
        "descriptor",
      );
    }
    const source = context.negativeSource;
    if (!source || source.upatientField !== canonical
        || typeof source.available !== "boolean") {
      return unsupported(
        field,
        "This row carries no provenance for the field it writes.",
        "negated_missing_provenance",
        "exact",
      );
    }
    if (!source.available) {
      return unsupported(
        field,
        "The positive answer is not part of this patient's context.",
        "negated_source_unavailable",
        "exact",
      );
    }
    if (!("value" in source)) {
      return unsupported(
        field,
        "This row carries no value for the field it writes.",
        "negated_source_value_missing",
        "exact",
      );
    }
    if (source.type != null && source.type !== "boolean") {
      return unsupported(
        field,
        "This row's provenance names a type it cannot carry.",
        "negated_source_type",
        "exact",
      );
    }
    if ("value" in source
        && source.value !== null && typeof source.value !== "boolean") {
      return unsupported(
        field,
        "This row's provenance names an answer it cannot carry.",
        "negated_source_value",
        "exact",
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
    if (target.kind !== "direct" || target.writable !== true
        || target.target !== "patient_record" || target.value_kind !== "boolean") {
      return unsupported(
        field,
        "The field behind this row is not a writable yes/no value.",
        "negated_target_contract",
        "descriptor",
      );
    }
    if (!answerLabels(target)) {
      return unsupported(
        field,
        "This row's answers are not named, so the dialog cannot ask them.",
        "negated_answer_labels",
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
    const labels = answerLabels(target)!;
    return {
      state: "handled",
      field: canonical,
      editor: {
        kind: "negated",
        field: canonical,
        control: "boolean",
        invert: true,
        source,
        positive: labels.positive,
        negative: labels.negative,
        options: [],
        multiple: false,
        joined: false,
        scope: "record",
      },
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
    const recipe = entry.authored_via;
    const recipeTarget =
      recipe && typeof recipe === "object" && !Array.isArray(recipe)
        && typeof (recipe as { target?: unknown }).target === "string"
        ? String((recipe as { target: string }).target)
        : "";
    const target = typeof entry.target === "string" ? entry.target : recipeTarget;
    if (!target && !recipe) {
      return unsupported(
        field,
        "This value is authored elsewhere, but the descriptor names no resource.",
        "authored_missing_recipe",
        "descriptor",
      );
    }
    return {
      state: "handled",
      field,
      editor: {
        kind: "authored",
        field,
        target: target || "resource",
        recipe,
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

  if (!kind || (kind !== "direct" && kind !== "profile" && kind !== "editable"
      && kind !== "selectable" && kind !== "unmapped")) {
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

  const rowMeta = context.surface === "row" ? context.row : null;
  const entryMeta = context.surface === "subform" ? context.entry : null;
  const rowVocabulary = {
    type: rowMeta?.utype ?? entryMeta?.type,
    options: rowMeta?.uoptions ?? entryMeta?.options,
  };
  const control = controlFor(entry, rowVocabulary);

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
  const unaccepted = unmappedVocabulary(field);
  const unmapped = unaccepted !== undefined
    && !(unaccepted.releasedByAControl && control === "multiselect");
  if (unmapped) {
    return unsupported(
      field,
      unaccepted.why,
      `unmapped_vocabulary:${field}`,
      "descriptor",
    );
  }

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
