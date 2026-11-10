// Full trial-detail view, mirroring CancerBot UI v2's `/t/:id` page
// (back link, header + score pills, meta, summary, and the read-only
// "Trial Eligibility Attributes" Required/Your-Value table). Rendered
// in-place by `TrialMatches` when a trial is selected — the remote owns the
// detail view rather than re-using the host's router. Data: `GET /trials/{id}/`
// (or `POST /trials/{id}/match/` for the inline-payload path) via
// `useTrialDetail`.
//
// The two controls that write live here too — the bookmark and CB's
// "I'm Interested" — but only their rendering: everything they write goes
// through the `state` adapter `TrialMatches` holds, which is why they arrive
// as a `trialState` bundle of values and callbacks rather than as an adapter
// this page talks to itself. Without one, neither is drawn.
//
// Still out of scope: editing a patient value (CB's pencil controls, phase 4)
// and CB's share / Standard-of-Care buttons.
import { useEffect, useId, useMemo, useState } from "react";

import { safeHref } from "../lib/safeUrl";
import {
  ActionTooltip,
  CheckIcon,
  FavoriteToggle,
  Field,
  FieldTooltip,
  ScorePill,
  SUITABILITY_HREF,
  asText,
  renderMd,
} from "./bits";
import { HighRiskMclPanel } from "./HighRiskMclPanel";
import { useFormSettings, useTrialDetail } from "./hooks";
import { injectStyles } from "./injectStyles";
import { ACTION_TOOLTIPS, FIELD_TOOLTIPS } from "./tooltips";
import type { AdvancedStatus, GenomicFinding, GenomicsAdapter } from "./state";
import type {
  FilterState,
  PatientInfo,
  SubformEntry,
  TrialDetailField,
  TrialMatchesProps,
} from "./types";
import { FieldEdit, PencilIcon } from "./FieldEdit";
import { NegatedBooleanDialog } from "./NegatedBooleanDialog";
import { SubformDialog, subformCanBeEdited } from "./SubformDialog";
import { GenomicsFindingsDialog } from "./GenomicsFindingsDialog";
import { ExplicitAnswerDialog } from "./ExplicitAnswerDialog";
import { splitJoined } from "./writable";
import { invertBoolean } from "./writable";
import { resolveEditor, toEditability } from "./editorResolver";
import type { WritableFields } from "./writable";
import type { ExplicitAnswerAdapter } from "./state";

/** Everything the two writing controls need, as plain values and callbacks.
 *
 *  The page never sees the `state` adapter itself: `TrialMatches` owns the
 *  queries and the mutations, so it is the only place that can keep the
 *  card's star and this one showing the same thing. Each field here is a
 *  distinct state the reader can be in, and every one of them has to be
 *  distinguishable on screen — "not loaded", "failed to load" and "false"
 *  all render as an empty star otherwise. */
export interface TrialStateControls {
  /** `undefined` = not known (still loading, or the read failed), which
   *  draws no control at all rather than a star that flips later. */
  isFavorite?: boolean;
  onToggleFavorite?: (next: boolean) => void;
  /** A bookmark write for this trial is on the wire. */
  favoriteBusy?: boolean;
  /** A bookmark write was rejected. The star is painted from the server's
   *  list, so a failed write leaves it exactly where it was — which is what
   *  a click that never registered looks like. */
  favoriteFailed?: boolean;
  /** The bookmark list could not be read, so there is no star to draw and
   *  the reader is owed a reason. */
  favoritesUnavailable?: boolean;
  isRegistered?: boolean;
  onToggleRegistered?: (next: boolean) => void;
  /** A registration write is on the wire. The button says so and ignores
   *  further clicks: the second one would race the first. */
  registerPending?: boolean;
  registerFailed?: boolean;
  registeredUnavailable?: boolean;
  /** Set when a study team has already moved this trial's enrollment past
   *  "registered". The control then becomes a statement rather than a
   *  button: `listRegisteredIds` asks for `status=registered` exactly, so
   *  such a patient reads back as not registered, and "I'm Interested"
   *  would write `registered` over `entered` (#434). */
  advancedStatus?: AdvancedStatus;
}

interface Props {
  apiClient: import("axios").AxiosInstance;
  trialId: number | string;
  patientInfo?: PatientInfo | null;
  personId?: string | number;
  /** Same study preferences the list used, so detail scores/units agree. */
  filters?: FilterState;
  onBack: () => void;
  /** Absent when the host supplied no state adapter — then neither the
   *  bookmark nor the interest control is rendered. */
  trialState?: TrialStateControls;
  /** Absent when the host supplied no writer, or while the descriptor is on
   *  its way — then no row draws an edit control and the table reads exactly
   *  as it does today. */
  editing?: RowEditing;
  /** Host-owned genomic-findings editor; replaces EXACT's dialog when given. */
  renderGenomicsEditor?: TrialMatchesProps["renderGenomicsEditor"];
  /** Host-owned patient-value editor, rendered per eligibility row. */
  renderFieldEditor?: TrialMatchesProps["renderFieldEditor"];
  /** Whether that node renders the value and units too (see the prop's doc). */
  fieldEditorOwnsValue?: TrialMatchesProps["fieldEditorOwnsValue"];
  /** Host-owned detail actions; each affordance is drawn only when supplied. */
  onCompareToSoc?: TrialMatchesProps["onCompareToSoc"];
  onAuthorHighRiskMcl?: TrialMatchesProps["onAuthorHighRiskMcl"];
  onShareTrial?: TrialMatchesProps["onShareTrial"];
  onOpenMissingAttributes?: TrialMatchesProps["onOpenMissingAttributes"];
  /** Host-owned register-interest affordance; replaces EXACT's control and
   *  card when given. */
  renderRegisterInterest?: TrialMatchesProps["renderRegisterInterest"];
}

const BackArrow = () => (
  <svg
    width="18"
    height="18"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M19 12H5" />
    <path d="m12 19-7-7 7-7" />
  </svg>
);


/** Resolve a field value to its display label, honouring select options.
 *  Option values may be numbers while the field value arrives as a string
 *  (or vice-versa) over the wire, so compare stringified. */
function labelOf(value: unknown, options?: TrialDetailField["options"]): string {
  if (value == null || value === "") return "—";
  const match = options?.find((o) => String(o.value) === String(value));
  return match ? match.label : String(value);
}

export function formatValue(value: unknown, options?: TrialDetailField["options"]): string {
  if (value == null || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) {
    const parts = value.filter((v) => v != null && v !== "").map((v) => labelOf(v, options));
    return parts.length ? parts.join(", ") : "—";
  }
  // A COMMA-JOINED value is a list the column happens to store as text, and
  // it used to read as raw codes here: `labelOf` matches the WHOLE string,
  // so "age,stage" matched nothing and fell through to itself — while the
  // editor opened on the same row showed "Age: Greater than 60 years". The
  // cell is where a reader checks their record, and codes there make a
  // correctly stored value look wrong (#586).
  //
  // Only when EVERY part is a known value. Not every string with a comma is
  // a list — a free-text field can hold "Smith, John" — and labelling one
  // part of somebody's prose because it happened to collide with an option
  // would be worse than the codes. All-or-nothing keeps the fallback exactly
  // where it was: a value the vocabulary does not recognise still reads
  // verbatim, which is what a legacy spelling needs.
  //
  // `splitJoined`, so the split here and the split the editor seeds from are
  // the same one. Two different splits of the same string is how
  // `inv(3)(q21,q26)` becomes two markers nobody has heard of.
  //
  // THERE IS A THIRD RULE, and it is the one that decides eligibility:
  // EXACT's matcher uses a bare `value.split(",")` with no trimming and no
  // bracket awareness (`trials/querysets/trial.py`). PROMOP reads
  // `cytogenetic_markers` back `", "`-joined, so the matcher already drops
  // every marker after the first — before this change and independently of
  // it. What this DOES do is remove the last on-screen hint: the cell used
  // to show raw text and now shows recognised labels for values the match
  // is not using. Filed as #588, which is where the three rules are made
  // one; this is not the place to paper over it.
  if (typeof value === "string" && options?.length) {
    const parts = splitJoined(value);
    const known = parts.length > 1
      && parts.every((part) => options.some((o) => String(o.value) === part));
    if (known) return parts.map((part) => labelOf(part, options)).join(", ");
  }
  return labelOf(value, options);
}

/** What the page knows about editing, handed down to every row.
 *
 *  `fields` undefined means the answer has not arrived (or the host supplied
 *  no writer): no row draws a control, and the table reads exactly as it does
 *  today. */
export interface RowEditing {
  fields?: WritableFields;
  /** Queue it. Returns at once and never rejects — what happened arrives
   *  through `outstanding` and `failed`. */
  save: (field: string, value: unknown) => void;
  /** What the reader chose, for fields whose write has not come back. The row
   *  shows this instead of the record's value, because the record does not
   *  hold it yet and showing the old one reads as an edit that did nothing. */
  outstanding: Record<string, unknown>;
  /** The value that was refused, by field — so the editor can open on what
   *  the reader typed rather than making them find it again. */
  failed: Record<string, unknown>;
  /** The structured genomics editor. Separate from `save` because a finding
   *  is not a scalar PatientRecord field. */
  genomics?: GenomicsAdapter;
  /** Re-read the trial (and the list) after a finding is written, with the
   *  new findings so an inline-profile host can update its own copy. */
  onGenomicsChanged?: (findings: GenomicFinding[]) => void;
  /** Explicit attestations (pre-existing conditions, derived negatives) go
   *  through their own endpoints, never `save`. */
  explicitAnswers?: ExplicitAnswerAdapter;
  /** Ask the host to re-read the patient payload after an attestation. */
  onExplicitAnswerChanged?: () => void;
}

/** The fields whose last write was refused, named the way the reader saw them.
 *
 *  Only the ones no row will speak for. A row that carries the control paints
 *  its own refusal in place, and saying it twice is worse than saying it once.
 *  But a subform input is by construction a DIFFERENT attribute from the row
 *  that opened the dialog, so a refusal there had nowhere to appear at all
 *  once the dialog was closed — and a write that vanishes with nothing said is
 *  the failure this whole phase was built to prevent, reached through the one
 *  surface the reader can shut.
 *
 *  Labels come from the payload rather than the canonical names, because
 *  `serum_calcium_level` is not what the reader was looking at when they
 *  typed. A field with no label to find is still named: leaving it out would
 *  under-report the failure to keep the sentence tidy.
 */
function failedFieldLabels(
  fields: TrialDetailField[],
  failed: Record<string, unknown>,
  editableRows: Set<string>,
  openHere: Set<string>,
  descriptor?: WritableFields,
): string[] {
  const names = Object.keys(failed);
  if (names.length === 0) return [];
  const labels = new Map<string, string>();
  const spokenFor = new Set<string>();
  for (const field of fields) {
    if (field.upatientField) {
      labels.set(field.upatientField, field.label);
      // The write lands in another field for two shapes: an alias names its
      // canonical, and a negated view names the positive status. The queue
      // reports a refusal under THAT name, so the row's label has to answer
      // for both or the sentence loses the only name the reader recognises.
      const entry = descriptor?.[field.upatientField];
      const target =
        (entry?.kind === "alias" || entry?.kind === "negated")
        && typeof entry.canonical === "string"
          ? entry.canonical
          : null;
      if (target) labels.set(target, field.label);
      // A row that carries the control says so itself, in place. Repeating it
      // up here would put two alerts on screen about one refusal.
      if (editableRows.has(field.name)) {
        spokenFor.add(field.upatientField);
        if (target) spokenFor.add(target);
      }
    }
    for (const entry of field.subform_details ?? []) {
      if (entry.upatientField) labels.set(entry.upatientField, entry.label);
    }
  }
  return names
    .filter((name) => !spokenFor.has(name) && !openHere.has(name))
    // A name with no label is not on this page. `failed` belongs to the
    // PATIENT's queue, which outlives any one trial, so a refusal from the
    // trial the reader was looking at a minute ago would otherwise be
    // announced on this one — under a canonical name nothing here shows.
    .filter((name) => labels.has(name))
    .map((name) => labels.get(name)!);
}

/** Which rows may carry an edit control.
 *
 *  Two rows in three can name the same patient attribute, because `ufield` is
 *  copied verbatim onto every row a config entry produces — a min and a max,
 *  and a "× upper limit of normal" pair beside them. Left alone that draws
 *  several pencils for one value, and the ×ULN one is actively wrong: the
 *  number on screen is a RATIO, so editing "2.5" would write 2.5 into the
 *  absolute lab column.
 *
 *  `ureadonly` marks exactly the rows that must not take a plain box — the
 *  ×ULN pair sets it outright, and it is otherwise true for a computed row or
 *  one whose value is composed in a subform (those need the dialog, which is
 *  the next slice). Of what is left, the first row to name an attribute keeps
 *  the control and the rest go back to reading as they do today; a min and a
 *  max show the same value, so which one wins does not matter, only that one
 *  does.
 */
function editableRowNames(
  fields: TrialDetailField[],
  writable: WritableFields | undefined,
): Set<string> {
  const taken = new Set<string>();
  const rows = new Set<string>();
  for (const field of fields) {
    const attribute = field.upatientField;
    if (!attribute || field.ureadonly) continue;
    // One resolver decides whether the row may carry a control. It owns the
    // recompute/override rule, aliases and the unmapped-vocabulary guard;
    // `editableRowNames` only keeps the one-control-per-attribute rule for
    // min/max and ULN pairs.
    const decision = resolveEditor(attribute, writable, {
      surface: "row",
      row: field,
      negativeSource: field.negative_source,
    });
    if (decision.state !== "handled") continue;
    if (
      decision.editor.kind !== "scalar"
      && decision.editor.kind !== "negated"
      && decision.editor.kind !== "explicit-answer"
    ) {
      continue;
    }
    // One control per ROW attribute, as before: an alias and its canonical
    // are different rows here, and collapsing them by the canonical name would
    // silently move the control to whichever row happens to come first.
    if (taken.has(attribute)) continue;
    taken.add(attribute);
    rows.add(field.name);
  }
  return rows;
}

/** Which rows may offer the subform door.
 *
 *  One per attribute, for the reason the pencils are: a ULN group is three
 *  rows — the pair and the range — carrying the SAME `subform_details`, so
 *  left alone the reader is offered the same dialog three times in a column
 *  and has to wonder which one is theirs.
 */
function subformRowNames(fields: TrialDetailField[]): Set<string> {
  // Every row that carries entries gets its own trigger. Two rows can carry the
  // same dialog (a threshold and the therapy group both list the therapy
  // history), and hiding one of them leaves the reader looking at the row they
  // clicked with no way in. The trigger is per ROW, not per group: it opens what
  // that row is worked out from.
  return new Set(
    fields.filter((field) => field.subform_details?.length).map((field) => field.name),
  );
}

/** Build the inputs of a computed row the server did not send a subform for.
 *
 *  The descriptor names the inputs; eligibility rows already carry the label,
 *  control type and current value for most of them. An input with no row of
 *  its own (Date of birth) falls back to a minimal entry from the descriptor.
 */
function computedSubformEntries(
  inputs: string[],
  allFields: TrialDetailField[],
  writable: WritableFields | undefined,
  patientInfo: PatientInfo | null | undefined,
): SubformEntry[] {
  const entries: SubformEntry[] = [];
  for (const input of inputs) {
    // A ULN row carries the RATIO in `uvalue`, not the absolute lab value.
    // Picking it as the input would seed a ratio into the absolute column.
    // Read-only rows are the ULN/display rows; the writable sibling is the
    // canonical input.
    const row = allFields.find(
      (candidate) => candidate.upatientField === input
        && candidate.ureadonly !== true,
    );
    if (row) {
      entries.push({
        name: row.name,
        label: row.label,
        type: row.utype ?? row.type,
        value: row.uvalue,
        options: row.uoptions ?? row.options,
        upatientField: input,
        upatientRecomputed: row.upatientRecomputed,
        units: row.units,
        uunits: row.uunits,
      });
      continue;
    }
    const entry = writable?.[input];
    if (!entry) continue;
    const kind = entry.value_kind;
    const camel = input.replace(/_([a-z])/g, (_match, letter: string) =>
      letter.toUpperCase(),
    );
    const current = patientInfo?.[input] ?? patientInfo?.[camel] ?? null;
    entries.push({
      name: input,
      label: input
        .replace(/_/g, " ")
        .replace(/\b\w/g, (letter) => letter.toUpperCase()),
      type: kind === "date" ? "date" : kind === "number" ? "int" : "string",
      value: current,
      options: Array.isArray(entry.options)
        ? entry.options as SubformEntry["options"]
        : null,
      upatientField: input,
    });
  }
  return entries;
}

function EligibilityRow({
  field,
  trialId,
  editing,
  allFields,
  patientInfo,
  editableHere,
  subformHere,
  openSubform,
  onSubformOpen,
  renderGenomicsEditor,
  renderFieldEditor,
  fieldEditorOwnsValue,
}: {
  field: TrialDetailField;
  trialId?: string | number;
  editing?: RowEditing;
  allFields: TrialDetailField[];
  patientInfo?: PatientInfo | null;
  editableHere?: boolean;
  subformHere?: boolean;
  openSubform?: string | null;
  onSubformOpen?: (name: string | null) => void;
  renderGenomicsEditor?: TrialMatchesProps["renderGenomicsEditor"];
  renderFieldEditor?: TrialMatchesProps["renderFieldEditor"];
  fieldEditorOwnsValue?: TrialMatchesProps["fieldEditorOwnsValue"];
}) {
  const matched = field.matchingType === "matched";
  const notMatched = field.matchingType === "not_matched";
  // The trial placed no constraint on this attribute, so it was never
  // checked. Said out loud rather than left blank: a row with no tick and no
  // mismatch mark reads the same as one the reader simply has no data for,
  // and those are different — one is a gap they could close, the other is
  // not a requirement at all.
  const notEvaluated = field.matchingType === "not_evaluated";
  const required = formatValue(field.value, field.options);
  // `edit` puts a control on screen; `no` may put a sentence there. PROMOP's
  // own `reason` strings never reach it — they are written for whoever is
  // integrating, and one points at `docs/omop_to_patientrecord.md`. What the
  // reader sees is curated wording from two tables in `writable.ts`, and a
  // refusal with no entry in either stays silent.
  const [editorOpen, setEditorOpen] = useState(false);
  // Kept apart from `editorOpen`: the inline editor stands in for the cell's
  // value, while the negated dialog sits over a row that keeps reading as it
  // did. One flag for both would blank the row behind a modal.
  const [dialogOpen, setDialogOpen] = useState(false);
  const [genomicsOpen, setGenomicsOpen] = useState(false);
  const [explicitOpen, setExplicitOpen] = useState(false);
  // A different trial is a different question. Without this a dialog left open
  // across a trial switch keeps the previous requirement on screen and seeds
  // from the previous patient's provenance.
  useEffect(() => {
    setEditorOpen(false);
    setDialogOpen(false);
    setGenomicsOpen(false);
    setExplicitOpen(false);
  }, [trialId]);
  // One decision for the row. The resolver owns kinds, permissions, aliases
  // and recompute; `toEditability` adapts scalar/negated decisions to the
  // editors this page already draws. A resolved refusal carries its reason;
  // it is not rendered yet (the next slice adds the note), but the row no
  // longer depends on three separate gates.
  const decision = editing
    ? resolveEditor(field.upatientField, editing.fields, {
      surface: "row",
      row: field,
      negativeSource: field.negative_source,
    })
    : null;
  const subformOpen = openSubform === field.name;
  const computedEditor = decision?.state === "handled"
    && decision.editor.kind === "computed-subform"
    ? decision.editor
    : null;
  const subformEntries = field.subform_details?.length
    ? field.subform_details
    : computedEditor
      ? computedSubformEntries(
        computedEditor.inputs,
        allFields,
        editing?.fields,
        patientInfo,
      )
      : null;
  const canOpenSubform =
    Boolean(subformHere || computedEditor)
    && subformCanBeEdited(subformEntries, editing);
  // `genetic_mutations` is structured, so the generic dialog refuses it. This
  // is the same source, opened through the endpoint that owns it — and only
  // when the descriptor says this caller may write it. The adapter exists for
  // read-only hosts too; the pencil must not promise a save they will refuse.
  const rowHasGenomics = Boolean(
    // CB names the row by its own attribute (`ufield`); the subform entry it
    // sends carries `name`, not necessarily `upatientField`. Accept all three
    // spellings so the host slot does not depend on one payload revision.
    field.ufield === "geneticMutations" ||
      field.subform_details?.some(
        (entry) =>
          entry.upatientField === "genetic_mutations" ||
          entry.name === "geneticMutations",
      ),
  );
  // A host slot owns the writer, so it does not need EXACT's `genomics`
  // adapter; without one, keep the descriptor gate so a read-only host never
  // draws a pencil whose save nothing would accept.
  const genomicsHere = Boolean(
    rowHasGenomics &&
      (renderGenomicsEditor ||
        (editing?.genomics &&
          editing.fields?.genetic_mutations?.writable === true)),
  );
  const resolved = decision
    ? toEditability(decision, editing?.fields)
    : ({ can: "unknown" } as const);
  // A duplicate row does not get a second control, but a refusal still
  // belongs to the row it was made for: `editableRowNames` excludes refused
  // rows, so gating the whole decision on `editableHere` would take their
  // explanation away.
  const editable = editableHere || resolved.can === "no"
    ? resolved
    : ({ can: "unknown" } as const);
  const explicitEditor = decision?.state === "handled"
    && decision.editor.kind === "explicit-answer"
    ? decision.editor
    : null;
  // Resolved once, not inline in the cell: the host widget renders the
  // patient's value as well as its control, so EXACT must not paint the same
  // value beside it. Deciding twice would let the two disagree.
  //
  // Resolved BEFORE the built-in gates below, because it is what they key on:
  // the slot is per ROW and returns null for a row the host does not own. An
  // empty node must not take EXACT's scalar, negated or explicit-answer
  // control away from that row — a host that covers therapy lines only would
  // otherwise leave every other row with no editor at all.
  const hostEditor = renderFieldEditor
    ? renderFieldEditor({
        field: field.ufield ?? field.name,
        label: field.label,
        trialId,
      })
    : null;
  const explicitAvailable = Boolean(
    explicitEditor
    && editing?.explicitAnswers
    && editableHere
    && !hostEditor,
  );
  // The queue is keyed by the field the write LANDS IN, not by the row's own
  // attribute: an alias writes its canonical field and a negated view writes
  // the positive one, so a pending or refused value under the row's name
  // would never be found. `editable.field` is that name, and for a negated
  // view the value crosses a polarity boundary in both directions.
  const attribute =
    editableHere && editable.can === "edit" ? editable.field : null;
  const inverted = editableHere && editable.can === "edit" && editable.invert;
  const pendingValue =
    attribute && editing && attribute in editing.outstanding
      ? inverted
        ? invertBoolean(editing.outstanding[attribute])
        : editing.outstanding[attribute]
      : undefined;
  const writePending = attribute != null && editing != null
    && attribute in editing.outstanding;
  const writeFailed = Boolean(attribute && editing && attribute in editing.failed);
  // The reader's own value wins over the record's while a write is out. Not
  // cosmetic: the record genuinely does not hold it yet, and showing the old
  // one for as long as that takes reads as a save that did not take.
  // Only the row that carries the control carries its optimistic value. The
  // others name the same attribute — a min, a max, and a "× upper limit of
  // normal" pair — and the ×ULN row shows a RATIO, so painting the written
  // number there would put a clinically false figure on screen, briefly, in a
  // table a patient reads.
  const yours = formatValue(
    writePending ? pendingValue : field.uvalue,
    field.uoptions ?? field.options,
  );
  const tooltip = FIELD_TOOLTIPS[field.ufield as string] ?? FIELD_TOOLTIPS[field.name];
  // Only when the host says THIS row's node renders the value too. Asked per
  // row because one slot can carry two shapes: a PROMOP field that draws the
  // value, and a therapy pencil that does not.
  const hostOwnsValue =
    Boolean(hostEditor) &&
    fieldEditorOwnsValue?.({
      field: field.ufield ?? field.name,
      label: field.label,
      trialId,
    }) === true;

  return (
    <div className="exact-elig__row">
      <div className="exact-elig__label">
        {field.label}
        {tooltip ? <FieldTooltip text={tooltip} /> : null}
      </div>
      <div
        className={`exact-elig__cell${matched ? " is-matched" : ""}`}
        data-col="required"
      >
        <span className="exact-elig__colhdr">Required</span>
        <span className={`exact-elig__val${notMatched ? " is-mismatch" : ""}`}>
          {renderMd(required)}
        </span>
        {field.units ? <span className="exact-elig__units">{field.units}</span> : null}
        {matched ? (
          <span className="exact-elig__check" role="img" aria-label="matches">
            <CheckIcon />
          </span>
        ) : null}
        {/* The mismatch mark lives beside the REQUIREMENT, not in the value
            cell: a host widget owns that cell, and a mark rendered there would
            sit in front of its control. It is the only non-colour signal —
            the cell tint alone is not announced — so it stays visible and
            labelled. */}
        {notMatched ? (
          <span className="exact-elig__mismatch" role="img" aria-label="does not match">
            ✕
          </span>
        ) : null}
        {notEvaluated ? (
          <span className="exact-elig__note">not a requirement of this trial</span>
        ) : null}
      </div>
      <div
        className={`exact-elig__cell${notMatched && !hostOwnsValue ? " is-mismatch" : ""}`}
        data-col="yours"
      >
        <span className="exact-elig__colhdr">Your Value</span>
        {/* The editor stands in for the value while it is open, rather than
            beside it: this column can be a few characters wide, and showing
            the old value next to the box that is replacing it is a question
            nobody should have to answer. */}
        {editorOpen || hostOwnsValue ? null : (
          <>
            <span className="exact-elig__val">{renderMd(yours)}</span>
            {field.uunits ?? field.units ? (
              <span className="exact-elig__units">{field.uunits ?? field.units}</span>
            ) : null}
          </>
        )}
        {/* Said out loud, not left to silence, in the two cases `announce`
            names: a control EXACT took away, and a write PROMOP routes to
            another resource. Both sentences are EXACT's own. The rows the
            descriptor merely refuses stay quiet — they never offered a
            control, so nothing went missing, and a sentence on each of the
            114 is noise.

            No `!editorOpen` guard. `FieldEdit` only renders under
            `can === "edit"`, so the editor cannot be open on a row that shows
            this — except after a verdict flips edit→no while it is open, and
            `FieldEdit` never clears the flag on unmount. In exactly that case
            the value and units above are suppressed too, so guarding here
            would leave the cell showing its column header and nothing else. */}
        {editable.can === "no" && editable.announce ? (
          <span className="exact-elig__note">{editable.why}</span>
        ) : null}
        {writePending ? (
          <span className="exact-elig__saving">Saving…</span>
        ) : null}
        {writeFailed ? (
          <span className="exact-elig__error" role="alert">
            Couldn't save that. Your value is not in the record.
          </span>
        ) : null}
        {/* The host's patient-value editor. It is per ROW, not per canonical
            attribute: PROMOP's slot is keyed by the row's own name (its
            `ufield`), and the host's widget owns saving. Returning null
            leaves the row to EXACT, which draws its own control above. */}
        {hostEditor}
        {/* A computed row cannot be written, but what it is computed FROM
            can — and the payload names those values. Offered only where at
            least one of them is actually writable: a dialog listing four
            values none of which can be changed is a door onto a wall, which
            is what the therapy groups would be. */}
        {canOpenSubform ? (
          <button
            type="button"
            className="exact-elig__edit"
            aria-label={`Change what ${field.label} is worked out from`}
            title={`Change what ${field.label} is worked out from`}
            onClick={() => onSubformOpen?.(field.name)}
          >
            <PencilIcon />
          </button>
        ) : null}
        {subformOpen && editing && subformEntries ? (
          <SubformDialog
            field={field}
            entries={subformEntries}
            editing={editing}
            onClose={() => onSubformOpen?.(null)}
          />
        ) : null}
        {genomicsHere ? (
          <button
            type="button"
            className="exact-elig__edit"
            aria-label={`Edit ${field.label} genetic findings`}
            title={`Edit ${field.label} genetic findings`}
            onClick={() => setGenomicsOpen(true)}
          >
            <PencilIcon />
          </button>
        ) : null}
        {explicitAvailable && editing ? (
          <>
            <button
              type="button"
              className="exact-elig__edit"
              aria-label={`Answer ${field.label}`}
              title={`Answer ${field.label}`}
              onClick={() => setExplicitOpen(true)}
            >
              <PencilIcon />
            </button>
            {explicitOpen && explicitEditor ? (
              <ExplicitAnswerDialog
                label={field.label}
                editor={explicitEditor}
                editing={editing}
                onClose={() => setExplicitOpen(false)}
              />
            ) : null}
          </>
        ) : null}
        {genomicsOpen && renderGenomicsEditor ? (
          renderGenomicsEditor({
            label: field.label,
            trialId,
            onClose: () => setGenomicsOpen(false),
          })
        ) : genomicsOpen && editing?.genomics ? (
          <GenomicsFindingsDialog
            label={field.label}
            editing={editing}
            onClose={() => setGenomicsOpen(false)}
          />
        ) : null}
        {editable.can === "edit" && editing && !editable.negative && !hostEditor ? (
          <FieldEdit
            field={editable.field}
            label={field.label}
            entry={editable.entry}
            options={editable.options}
            joined={editable.joined}
            control={editable.control}
            // A refused value wins over the record's: the reader is about to
            // try again, and the thing they want in the box is what they
            // typed.
            value={
              writeFailed && attribute
                ? inverted
                  ? invertBoolean(editing.failed[attribute])
                  : editing.failed[attribute]
                : writePending
                  ? pendingValue
                  : field.uvalue
            }
            // The record's own value, with neither of those in front of it.
            recordValue={field.uvalue}
            units={field.uunits ?? field.units}
            // A negated view crosses the polarity boundary here: the box holds
            // the row's answer ("No HIV: Yes"), and the record stores the
            // positive status with the opposite value.
            onSave={(value) =>
              editing.save(
                editable.field,
                editable.invert ? invertBoolean(value) : value,
              )
            }
            onOpenChange={setEditorOpen}
          />
        ) : null}
        {editable.can === "edit" && editing && editable.negative && !hostEditor ? (
          <>
            <button
              type="button"
              className="exact-elig__edit"
              aria-label={`Answer ${field.label}`}
              title={`Answer ${field.label}`}
              onClick={() => setDialogOpen(true)}
            >
              <PencilIcon />
            </button>
            {dialogOpen ? (
              <NegatedBooleanDialog
                field={field}
                canonical={editable.field}
                positive={editable.negative.positive}
                negative={editable.negative.negative}
                source={editable.negative.source}
                editing={editing}
                onClose={() => setDialogOpen(false)}
              />
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}

/** The interest button, drawn twice as CB draws it: in the header beside the
 *  bookmark (CB's `TrialActions`) and in the card at the foot of the page.
 *  One component, so the two cannot disagree about what a click does. */
function RegisterButton({
  isRegistered,
  pending,
  onToggle,
  describedBy,
}: {
  isRegistered: boolean;
  pending: boolean;
  onToggle: (next: boolean) => void;
  /** The header copy points at the card's explanation, so a screen reader's
   *  list of buttons does not show two bare "I'm Interested" entries. */
  describedBy?: string;
}) {
  return (
    // CB's button carries a "?" and a hover tooltip; the tooltip is read out
    // through `aria-describedby`, and the glyph is decoration.
    <ActionTooltip text={isRegistered ? ACTION_TOOLTIPS.registered : ACTION_TOOLTIPS.registerInterest}>
      {(tipId) => (
        <button
          type="button"
          className={`exact-register__btn${isRegistered ? " is-on" : ""}`}
          // `aria-disabled`, not `disabled`: a control that disables itself
          // under the pointer is blurred by the browser, dropping a keyboard
          // user to the document body in the middle of the action they just
          // took. Announced here, enforced in one place — `TrialMatches`'s
          // `write` drops a click for a trial whose write is still on the
          // wire, and two PATCHes in flight are applied in whatever order
          // they arrive.
          aria-disabled={pending || undefined}
          aria-busy={pending || undefined}
          aria-describedby={[tipId, describedBy].filter(Boolean).join(" ")}
          onClick={() => onToggle(!isRegistered)}
        >
          {pending ? "Saving…" : isRegistered ? "Withdraw" : "I'm Interested"}
          <span className="exact-register__help" aria-hidden="true">
            ?
          </span>
        </button>
      )}
    </ActionTooltip>
  );
}

/** CB's RegisterInterestCard, with CB's promise removed.
 *
 *  CB tells the reader "a study coordinator will reach out to discuss next
 *  steps". Here that would be false: registering writes a status onto the
 *  patient's enrollment row in PROMOP and nothing else happens — no mail,
 *  no queue, nobody notified. So the copy says what the click actually does
 *  and says explicitly what it does not do. Whether it should eventually do
 *  more is a product decision that has not been taken.
 *
 *  What IS kept from CB is #4669: when the matcher says not_eligible, the
 *  card must not open by telling someone they may be eligible — and it goes
 *  on saying so after they register, which is when it matters most.
 */
function RegisterInterest({
  advancedStatus,
  notEligible,
  mismatchesShown,
  isRegistered,
  pending,
  failed,
  onToggle,
  textId,
}: {
  advancedStatus?: AdvancedStatus;
  notEligible: boolean;
  /** Whether the table above actually lists a mismatched row.
   *
   *  It often does not: `matchingType` is decided over every mapped
   *  attribute, while the table is the filtered "potential attributes"
   *  view, which drops admin and general groups, blank values and select
   *  values absent from their own options. Pointing at rows that are not
   *  there — or at an empty table, which the endpoint does return — was a
   *  claim the page could not keep. */
  mismatchesShown: boolean;
  isRegistered: boolean;
  pending: boolean;
  failed: boolean;
  onToggle: (next: boolean) => void;
  /** Id for the explanation, which the header's copy of the button cites. */
  textId?: string;
}) {
  if (advancedStatus) {
    return (
      <section className="exact-panel exact-register is-registered">
        <h2 className="exact-panel__title">
          {advancedStatus === "completed"
            ? "Your participation in this trial is recorded as completed"
            : "You are recorded as taking part in this trial"}
        </h2>
        <p className="exact-register__text">
          Your study team keeps this up to date, so it is not something to
          change here. Speak to them if it looks wrong.
        </p>
      </section>
    );
  }

  const heading = isRegistered
    ? "You registered interest in this trial"
    : notEligible
      ? "You may not meet this trial's eligibility criteria"
      : "Interested in this trial?";

  // Deliberately NOT "listed on the Registered tab": that tab asks for
  // `status=registered` exactly, so a patient a coordinator has advanced to
  // `entered` is no longer on it (#434). Note that the same narrow query
  // decides `isRegistered`, so such a patient does not reach this branch at
  // all — they are invited to register for a trial they are already in.
  // That is #434 itself and is not fixed here; what is fixed is this
  // sentence not adding a second false claim on top of it.
  const state = isRegistered
    ? "It is marked in your record, and you can withdraw at any time."
    : "Registering marks this trial in your record, where you can find it again.";
  const mismatch = !notEligible
    ? ""
    : mismatchesShown
      ? " One or more of this trial's requirements does not match your profile — the ones in the eligibility table are marked."
      : " One or more of this trial's requirements does not match your profile.";

  return (
    <section
      className={`exact-panel exact-register${notEligible ? " is-warning" : ""}${
        isRegistered ? " is-registered" : ""
      }`}
    >
      <h2 className="exact-panel__title">{heading}</h2>
      <p className="exact-register__text" id={textId}>
        {state}
        {mismatch} Nothing is sent to the trial's coordinators from here.
      </p>
      <RegisterButton isRegistered={isRegistered} pending={pending} onToggle={onToggle} />
      {/* Not an alert: the header says it too, where the reader most likely
          clicked, and one failure should be announced once. */}
      {failed ? (
        <p className="exact-register__error">Couldn't save that. Please try again.</p>
      ) : null}
    </section>
  );
}

/**
 * The registry link, or plain text when the corpus value is not a safe href.
 *
 * A separate component so the guard can be rendered and asserted rather than
 * pattern-matched in the source (#406). An earlier test read this file with
 * regexes and passed on `href={data.link || ""}` -- it recognised one spelling
 * of the fix, not the property.
 *
 * Gated on the SANITISED value: gating on the raw one would render an `<a>`
 * with no `href` for a rejected URL, which paints link styling over something
 * that does nothing.
 *
 * `safeHref` rather than the stricter `safeRedirect`: `data.link` is always an
 * absolute registry URL, so `safeRedirect`'s `^https://` would fit better --
 * except that it would also drop the legacy `http://clinicaltrials.gov/ct2/...`
 * links the corpus still carries. The looser check therefore also admits
 * `mailto:`/`tel:` and relative paths, which this field never holds; a relative
 * value would resolve against the HOST application's origin, since this is a
 * federation remote. Not exploitable, but wider than the data warrants.
 */
export function NctLink({ link, studyId }: { link?: string | null; studyId?: string | null }) {
  const href = safeHref(link);

  if (!href) {
    return <span className="exact-field__value">{studyId}</span>;
  }

  return (
    <a className="exact-detail__nct" href={href} target="_blank" rel="noopener noreferrer">
      {studyId}
    </a>
  );
}

export function TrialDetailPage({
  apiClient,
  trialId,
  patientInfo,
  personId,
  filters,
  onBack,
  trialState,
  editing,
  renderGenomicsEditor,
  renderFieldEditor,
  fieldEditorOwnsValue,
  onCompareToSoc,
  onAuthorHighRiskMcl,
  onShareTrial,
  onOpenMissingAttributes,
  renderRegisterInterest,
}: Props) {
  // Idempotent: ensures the scoped stylesheet is present even if this page is
  // mounted without `TrialMatches` having run (it already injects on mount).
  // No options on purpose: this inherits the mode `TrialMatches` chose from
  // its `styles` prop, rather than imposing the default on a host that asked
  // for the unlayered sheet.
  useEffect(() => {
    injectStyles();
  }, []);

  const query = useTrialDetail({ apiClient, trialId, patientInfo, personId, filters });
  const data = query.data;

  // Only MCL trials that gate on the criteria carry one; for everything else
  // the server sends null and none of the below runs.
  const mcl = data?.highRiskMclCriteriaBreakdown ?? null;
  const formSettings = useFormSettings(
    apiClient,
    typeof patientInfo?.disease === "string" ? patientInfo.disease : undefined,
    mcl != null,
  );
  const mclTitle = useMemo(() => {
    const byCode = new Map<string, string>();
    for (const o of formSettings.data?.highRiskMclCriteria?.options ?? []) {
      byCode.set(String(o.value), o.label);
    }
    // Falls back to the CODE, not to a prettified version of it. `tp53_mutation`
    // is at least honest about being an identifier; "Tp53 Mutation" reads as a
    // clinical label the catalog never wrote, and the catalog is the one place
    // allowed to name these.
    return (code: string) => byCode.get(code) ?? code;
  }, [formSettings.data]);
  // Held until the catalog answers — success or failure. The fetch is gated on
  // the breakdown having already arrived, so without this the code fallback is
  // the NORMAL first paint rather than the edge case it was written for: every
  // reader would see `tp53_mutation` for one round-trip before it became
  // "TP53 mutation". A failed fetch still settles, so the fallback keeps its
  // intended meaning — the catalog does not name this code.
  const mclNamesSettled = formSettings.isFetched;

  const eligibility = data?.details?.trialEligibilityAttributes ?? [];
  const editableRows = useMemo(
    () => editableRowNames(eligibility, editing?.fields),
    [eligibility, editing?.fields],
  );
  const subformRows = useMemo(() => subformRowNames(eligibility), [eligibility]);
  // Which row's dialog is open, if any. Held here rather than in the row so
  // the notice below can stay quiet about what an open dialog is already
  // saying to the reader's face.
  const [openSubform, setOpenSubform] = useState<string | null>(null);
  const openSubformFields = useMemo(() => {
    const row = eligibility.find((field) => field.name === openSubform);
    return new Set(
      (row?.subform_details ?? [])
        .map((entry) => entry.upatientField)
        .filter((name): name is string => Boolean(name)),
    );
  }, [eligibility, openSubform]);
  const failedLabels = useMemo(
    () =>
      failedFieldLabels(
        eligibility,
        editing?.failed ?? {},
        editableRows,
        openSubformFields,
        editing?.fields,
      ),
    [eligibility, editing?.failed, editing?.fields, editableRows, openSubformFields],
  );
  const summary = data
    ? data.laySummary || data.briefSummary || data.participationCriteria || ""
    : "";

  // CB's own rule (#4669), and the same field: the detail endpoint scores
  // the trial with the conflict-aware matcher and can answer not_eligible.
  const notEligible = data?.matchingType === "not_eligible";
  const canRegister =
    trialState?.onToggleRegistered != null && trialState.isRegistered !== undefined;
  // Worked out once for both copies of the interest button, so the header and
  // the card cannot be handed different answers.
  const registerButton = canRegister
    ? {
        isRegistered: trialState!.isRegistered!,
        pending: trialState?.registerPending ?? false,
        onToggle: trialState!.onToggleRegistered!,
      }
    : null;
  // When the host owns registration, it decides what an advanced trial looks
  // like too: `advancedStatus` travels with the slot instead of hiding it.
  const hostRegister = renderRegisterInterest != null;
  const showHeaderRegister =
    hostRegister || (registerButton != null && !trialState!.advancedStatus);
  // Whether the table actually lists a mismatch; see `RegisterInterest`.
  const mismatchesShown = eligibility.some((f) => f.matchingType === "not_matched");
  // The trial asks for something the patient has not supplied. That is what
  // the host's Missing Attributes panel exists for; with nothing missing the
  // affordance would open an empty list.
  const hasMissingAttributes = eligibility.some(
    (f) => f.matchingType === "unknown",
  );
  const registerTextId = useId();

  return (
    <div className="exact-root exact-detail">
      <button type="button" className="exact-detail__back" onClick={onBack}>
        <BackArrow />
        <span>Back to all trials</span>
      </button>

      {query.isLoading ? (
        <p style={{ color: "var(--exact-color-text-muted)" }}>Loading trial…</p>
      ) : null}

      {query.isError ? (
        /* A 404 is not a failure to report as one. With the host keeping the
         * trial in its URL (#552), this page is reachable by typing — and
         * `/trials/nonsense` answering "Failed to load trial: Request failed
         * with status code 404" reads as something broken here rather than
         * an address that names no trial. Anything else keeps the message
         * it came with, because that one IS about us. */
        <p role="alert" style={{ color: "var(--exact-color-error-700)" }}>
          {(query.error as { response?: { status?: number } })?.response?.status === 404
            ? "We could not find that trial. It may have been withdrawn, or the address may be wrong."
            : `Failed to load trial: ${(query.error as Error)?.message ?? "unknown error"}`}
        </p>
      ) : null}

      {data ? (
        <>
          <div className="exact-detail__head">
            <h1 className="exact-detail__title">{data.briefTitle}</h1>
            {/* CB's `TrialActions`: bookmark, then the interest button. A
                trial the study team has advanced gets no button here; the
                card below says why. */}
            <div className="exact-detail__actions">
              <FavoriteToggle
                title={data.briefTitle}
                isFavorite={trialState?.isFavorite}
                onToggle={trialState?.onToggleFavorite}
                busy={trialState?.favoriteBusy}
                boxed
              />
              {hostRegister ? (
                renderRegisterInterest({
                  trialId,
                  variant: "header",
                  isRegistered: registerButton?.isRegistered ?? false,
                  pending: registerButton?.pending ?? false,
                  advancedStatus: trialState?.advancedStatus,
                })
              ) : showHeaderRegister ? (
                <RegisterButton {...registerButton!} describedBy={registerTextId} />
              ) : null}
              {onShareTrial ? (
                <button
                  type="button"
                  className="exact-detail__host-action"
                  onClick={() => onShareTrial(trialId)}
                >
                  Share trial
                </button>
              ) : null}
            </div>
          </div>
          {/* CB's rule (#4669) followed up here as well: the header's green
              button must not read as "you may be eligible" when the matcher
              says otherwise, and the card saying so is at the foot of the
              page. A statement, not an alert — it is there on load. */}
          {!hostRegister && showHeaderRegister && notEligible ? (
            <p className="exact-detail__eligibility-warning">
              You may not meet this trial's eligibility criteria
              {mismatchesShown
                ? " — the requirements that do not match are marked in the table below."
                : "."}
            </p>
          ) : null}
          {/* Not gated on the button: an advanced status that arrives after a
              refused write swaps the card for a statement that shows no
              failure, and this line is then the only place it is said. */}
          {!hostRegister && registerButton && trialState?.registerFailed ? (
            <p className="exact-register__error exact-detail__register-error" role="alert">
              Couldn't save that. Please try again.
            </p>
          ) : null}

          {/* Same three failures the list reports, and for the same reason:
              painted from a server list, a rejected write and a rejected read
              both look exactly like a control that does nothing. */}
          {trialState?.favoritesUnavailable ? (
            <p style={{ color: "var(--exact-color-not-eligible)" }}>
              Couldn't load your favorites, so bookmarking is unavailable right
              now.
            </p>
          ) : null}
          {trialState?.favoriteFailed ? (
            <p style={{ color: "var(--exact-color-not-eligible)" }} role="alert">
              Couldn't update your favorites. Please try again.
            </p>
          ) : null}

          <div className="exact-detail__scores">
            <ScorePill
              score={data.matchScore}
              label="Matching Score"
              tooltip={ACTION_TOOLTIPS.matchingScore}
            />
            <ScorePill
              score={data.goodnessScore}
              label="Suitability Score"
              href={SUITABILITY_HREF}
              tooltip={ACTION_TOOLTIPS.suitabilityScore}
            />
          </div>

          {onCompareToSoc ? (
            <div className="exact-detail__host-actions">
              <button
                type="button"
                className="exact-detail__host-action"
                onClick={() => onCompareToSoc(trialId)}
              >
                Compare to Standard of Care
              </button>
            </div>
          ) : null}

          <div className="exact-detail__meta">
            <div className="exact-card__fields">
              <Field label="Location" value={asText(data.locationsName)} collapsible />
              <Field label="Status" value={asText(data.recruitmentStatus)} />
            </div>
            <div className="exact-card__fields">
              <Field
                label="Intervention/Treatment"
                value={asText(data.interventionTreatments)}
              />
              <Field label="Phase" value={asText(data.phases, " / ")} />
            </div>
            <div className="exact-card__fields">
              <Field label="Trial Type" value={asText(data.trialType)} />
              <div className="exact-field">
                <span className="exact-field__label">NCT Number: </span>
                <NctLink link={data.link} studyId={data.studyId} />
              </div>
              <Field label="Sponsor" value={asText(data.sponsorName)} />
            </div>
          </div>

          {/* OUTSIDE the grid, which is a flex ROW from 768px up. As a child
              it became a third column, taking most of the page width from the
              eligibility table beside it and pushing the document into
              horizontal overflow at every breakpoint. Deliberately no pixel
              figures here: three separate measurements of the before-state
              disagreed, because each harness carried different page chrome,
              and a number in a comment outlives the harness that produced it.
              The mechanism is what reproduces. jsdom does no layout, so no
              test in either suite can see any of this. */}
          {failedLabels.length ? (
            <p className="exact-detail__write-error" role="alert">
              {failedLabels.length === 1
                ? `Your ${failedLabels[0]} could not be saved.`
                : `These could not be saved: ${failedLabels.join(", ")}.`}{" "}
              The record still holds what it had. Open the field and try again.
            </p>
          ) : null}

          <div className="exact-detail__grid">
            {summary ? (
              <section className="exact-panel exact-detail__summary">
                <div className="exact-panel__head">
                  <h2 className="exact-panel__title">Summary</h2>
                  <FieldTooltip text={ACTION_TOOLTIPS.summary} />
                </div>
                <p className="exact-detail__summary-text">{summary}</p>
              </section>
            ) : null}

            <section className="exact-panel exact-detail__elig">
              <div className="exact-panel__head">
                <h2 className="exact-panel__title">Trial Eligibility Attributes</h2>
                <FieldTooltip text={ACTION_TOOLTIPS.eligibilityAttributes} />
                {onOpenMissingAttributes && hasMissingAttributes ? (
                  <button
                    type="button"
                    className="exact-detail__host-action"
                    onClick={() => onOpenMissingAttributes(trialId)}
                  >
                    Missing attributes
                  </button>
                ) : null}
              </div>
              {eligibility.length ? (
                <div className="exact-elig">
                  <div className="exact-elig__thead" aria-hidden="true">
                    <div />
                    <div className="exact-elig__thead-col">Required</div>
                    <div className="exact-elig__thead-col">Your Value</div>
                  </div>
                  {eligibility.map((field) => (
                    <EligibilityRow
                      key={field.name}
                      field={field}
                      trialId={trialId}
                      editing={editing}
                      allFields={eligibility}
                      patientInfo={patientInfo}
                      editableHere={editableRows.has(field.name)}
                      subformHere={subformRows.has(field.name)}
                      openSubform={openSubform}
                      onSubformOpen={setOpenSubform}
                      renderGenomicsEditor={renderGenomicsEditor}
                      renderFieldEditor={renderFieldEditor}
                      fieldEditorOwnsValue={fieldEditorOwnsValue}
                    />
                  ))}
                </div>
              ) : (
                <p style={{ color: "var(--exact-color-text-muted)", margin: 0 }}>
                  No eligibility attributes to show for this trial.
                </p>
              )}
            </section>
          </div>

          {mcl && mclNamesSettled ? (
            <>
              <HighRiskMclPanel breakdown={mcl} titleOf={mclTitle} />
              {onAuthorHighRiskMcl ? (
                <div className="exact-detail__host-actions">
                  <button
                    type="button"
                    className="exact-detail__host-action"
                    onClick={() => onAuthorHighRiskMcl(trialId)}
                  >
                    Author high-risk MCL criteria
                  </button>
                </div>
              ) : null}
            </>
          ) : null}

          {hostRegister ? (
            renderRegisterInterest({
              trialId,
              variant: "card",
              isRegistered: registerButton?.isRegistered ?? false,
              pending: registerButton?.pending ?? false,
              advancedStatus: trialState?.advancedStatus,
            })
          ) : registerButton ? (
            <RegisterInterest
              {...registerButton}
              advancedStatus={trialState!.advancedStatus}
              notEligible={notEligible}
              mismatchesShown={mismatchesShown}
              failed={trialState?.registerFailed ?? false}
              textId={registerTextId}
            />
          ) : null}

          {!hostRegister && trialState?.registeredUnavailable ? (
            <p style={{ color: "var(--exact-color-not-eligible)" }}>
              Couldn't load whether you have registered interest in this trial,
              so that control is unavailable right now.
            </p>
          ) : null}
        </>
      ) : null}
    </div>
  );
}

export default TrialDetailPage;
