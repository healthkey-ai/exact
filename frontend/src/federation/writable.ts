// What the patient may edit, asked rather than assumed.
//
// EXACT knows which patient attribute a row is about (`upatientField`, #421).
// It does not know whether that attribute can be written: PROMOP's
// `PatientRecord` is a read-only projection re-derived from OMOP facts, so a
// write is a write into OMOP, and whether one exists depends on a reviewed
// concept set, on the field's kind, and on who is asking. EXACT cannot answer
// any of those — 19 of its fields have no PatientRecord column at all.
//
// So the descriptor is fetched at runtime from
// `GET /api/v1/patient-records/writable-fields/?person_id=N` and it decides.
// Passing `person_id` matters: without it the answer describes the deployment
// ("could someone write this"), and an analyst with read-only access to every
// patient would be shown a typeable box whose every save is refused.

/** The kinds PROMOP resolves every field to. Exactly one each. */
export type WritableKind =
  | "editable"    // write an OMOP fact; derivation follows
  | "selectable"  // choose from a bounded set, carried on the fact
  | "computed"    // derived from other fields; never authored alone
  | "alias"       // mirrors a canonical field; edit that one instead
  | "profile"     // a Person attribute, written at the persons endpoint
  | "unmapped"    // no write path yet
  | "authored"    // written by authoring a different resource entirely
  | "direct";     // write to PatientRecord; no OMOP fact required yet

export interface WritableFieldEntry {
  kind: WritableKind | (string & {});
  writable: boolean;
  /** Why, in PROMOP's words — which are not always fit to show a reader.
   *
   *  Some are written for whoever is integrating: one points at
   *  `docs/omop_to_patientrecord.md`. Showing them raw to a patient would be
   *  worse than saying nothing, so this is carried, not rendered.
   *
   *  It is also NOT only on refusals. PROMOP attaches a `reason` to plenty of
   *  writable entries — "Written directly to PatientRecord. No OMOP mapping
   *  yet.", or `relapse_count`'s "Inferred by default; enter a value to
   *  override". Treating its presence as an error would withhold controls
   *  that work. */
  reason?: string;
  /** The field this one mirrors. Usually an `alias`, but not only: PROMOP
   *  sets it on `refractory_status`, which is `direct` and writable. */
  canonical?: string;
  value_kind?: string;
  unit?: string;
  multiple?: boolean;
  /** Where the value ends up in OMOP when the write is to the record.
   *  `person` and `location` are the demographics — gender, ethnicity, the
   *  address — which describe the PATIENT rather than any one measurement.
   *  PROMOP takes them through the same PATCH, which is why they are writable
   *  here at all. */
  projection_target?: string;
  /** Where the write goes. `patient_record` is the one this seam can reach;
   *  `genomics` and `episode` are edited through their own resources, and
   *  those entries carry a `reason` saying where. */
  target?: string;
  options?: unknown;
  [key: string]: unknown;
}

/** Keyed by the canonical patient attribute name (`hemoglobin_g_dl`), which is
 *  what EXACT puts in `upatientField`. */
export type WritableFields = Record<string, WritableFieldEntry>;

export interface FieldOption {
  value: string;
  label: string;
}

/** An option's `value` is the string to write. Everything beside it is coding.
 *
 *  PROMOP sends objects, in four widths: `{value}` alone, `{value, code}` for
 *  curator-managed choices and demographics, and for `cytogenetic_markers`
 *  `{value, code, vocabulary, concept_id, display}`. What matters is that only
 *  `value` is the thing the record stores — `FieldChoice.display` is
 *  documented as "one allowed value for a PatientRecord field", and the `code`
 *  beside it is vocabulary metadata for the OMOP projection. Sending the code
 *  would put `254837009` where the record wants "Invasive ductal".
 *
 *  `display` is a trap of its own: on cytogenetics options it holds the OMOP
 *  CONCEPT NAME ("Deletion of short arm of chromosome 17"), not a label for
 *  the value. Only an explicit `label` is used as one.
 *
 *  The array form is not something PROMOP sends. It is kept because the tuple
 *  it would come from still exists one function inside PROMOP
 *  (`_field_choice_options` returns `(display, code)` pairs, dict-ified by its
 *  only caller), so the order is worth pinning: display first.
 */
export function optionsOf(entry: WritableFieldEntry | undefined): FieldOption[] {
  const raw = entry?.options;
  if (!Array.isArray(raw)) return [];
  const out: FieldOption[] = [];
  for (const item of raw) {
    if (typeof item === "string") {
      if (item !== "") out.push({ value: item, label: item });
      continue;
    }
    if (Array.isArray(item)) {
      const display = item[0];
      if (typeof display === "string" && display !== "") {
        out.push({ value: display, label: display });
      }
      continue;
    }
    if (item && typeof item === "object") {
      const value = (item as { value?: unknown }).value;
      const label = (item as { label?: unknown }).label;
      if (value == null || value === "") continue;
      out.push({
        value: String(value),
        label: typeof label === "string" && label !== "" ? label : String(value),
      });
    }
  }
  return out;
}

/** The control a writable field wants. `select` is decided by the presence of
 *  options rather than by `kind`, because `direct` fields carry them too. */
export type EditControl =
  | "select" | "multiselect" | "number" | "boolean" | "date" | "datetime" | "text";

export function controlFor(entry: WritableFieldEntry): EditControl {
  if (optionsOf(entry).length > 0) return entry.multiple ? "multiselect" : "select";
  switch (entry.value_kind) {
    // PROMOP maps every numeric model field to `number`; `integer` and
    // `float` are here so a future widening lands on a number box rather
    // than a text one.
    case "number":
    case "integer":
    case "float":
      return "number";
    case "boolean":
      return "boolean";
    case "date":
      return "date";
    // Kept apart from `date`: a control that drops the time silently rewrites
    // the value it was given the moment the reader saves an unrelated edit.
    case "datetime":
      return "datetime";
    default:
      return "text";
  }
}

/** The answer for one eligibility row.
 *
 *  `unknown` is deliberately not folded into `no`. "PROMOP will not accept this
 *  write" and "nobody has told us either way" look the same on screen if both
 *  render a disabled control with no explanation, and they are not the same
 *  thing: the first has a reason worth showing, the second is a gap in our own
 *  plumbing (#449) and should simply leave the row as it is today.
 */
export type Editability =
  | { can: "edit"; field: string; entry: WritableFieldEntry; control: EditControl }
  | {
      can: "no";
      field: string;
      entry: WritableFieldEntry;
      why: string;
      /** Whether to say `why` on screen.
       *
       *  True for one case: a row that HAD an editor in a previous release and
       *  no longer does. A control that vanishes with no word reads as a bug,
       *  so that one is explained.
       *
       *  Not "the descriptor allowed it and EXACT withheld it", which an
       *  earlier version of this comment claimed — `elsewhere` and
       *  `structured` fit that description too and are deliberately silent.
       *  They never offered a control, so nothing went missing. Printing a
       *  sentence on each of those, and on the 114 the descriptor itself
       *  refuses, would bury the page in "Unit of albumin_level; selected
       *  alongside that value" without answering a question anybody had.
       *
       *  `genetic_mutations` is the case worth revisiting: `elsewhere`, with a
       *  reason naming the tab to edit it in, which is actionable in a way the
       *  others are not. Left silent here rather than widened without a look
       *  at how it reads on the page. */
      announce: boolean;
    }
  | { can: "unknown" };

const NOT_IN_RECORD =
  "This attribute is not part of the patient record, so there is nowhere to save it.";

/** Attributes PROMOP reports as writable whose value this page cannot collect.
 *
 *  Both are described as writable strings with NO options, so the editor drew a
 *  free text box — and the matcher reads each value as a list of codes
 *  (`trials/querysets/trial.py`), shaped `write__en` and `age,stage,ldh`. A
 *  reader typing "English" stored something that matches nothing, silently, on
 *  attributes that gate eligibility. A box that cannot produce a usable answer
 *  is worse than no box.
 *
 *  `flipi_score_options` was taken off this list once and put back. The
 *  argument for removing it was that EXACT already ships the vocabulary on the
 *  row, in `uoptions`, so the row wanted a multiselect rather than a block.
 *  The vocabulary claim is true — and irrelevant here, because `uoptions` has
 *  exactly one consumer in this app, `formatValue` on the detail page, which
 *  DISPLAYS a stored code as a label. `controlFor` below reads the PROMOP
 *  descriptor and nothing else, so a row carrying `utype: "multiselect"` and a
 *  full option list still gets a text box. Measured against the live
 *  descriptor: `{kind: "direct", writable: true, value_kind: "string"}`, no
 *  options. Teaching the editor to fall back to `uoptions` is the right fix for
 *  both fields and is a change of its own; until it exists, the box is the bug.
 *
 *  `gelf_criteria_options` was also on this list and is gone for a different
 *  reason: EXACT never passes that field name. (`gelf_criteria_status` is a
 *  real EXACT column with its own vocabulary — if PROMOP's inventory meant that
 *  field under another spelling, the binding is misfiled and this list is one
 *  entry short. Not chased.)
 *
 *  Before releasing either field, check how a multiselect's value round-trips.
 *  Measured on the PROMOP side today: `validate_flipi_score_options` stores
 *  `','.join(selected)` with no space, and EXACT strips anyway
 *  (`PatientInfoFlipyScore.scope_by_options`); `languages_skills` has no
 *  validator, and the matcher splits it with `_csv`, which does NOT strip
 *  (`_csv_stripped` exists beside it and is not used). A writer that joined
 *  with ", " would therefore produce `" write__fr"` and match nothing —
 *  silently, which is this commit's whole subject. The one `', '.join` in that
 *  serializer belongs to `cytogenetic_markers` and is read-only, so nothing
 *  does it today.
 *
 *  `languages_skills` would stay here even with a working editor: PROMOP
 *  denormalizes that column from `PersonLanguageSkill` and says of the eight
 *  booleans beside it "derived, never written directly"; its migration 0186
 *  gives the field no choices ON PURPOSE, pinned by a test. The write is wrong
 *  on PROMOP's terms, not only uncollectable on ours.
 *
 *  WHERE THIS BELONGS: in PROMOP's descriptor, as `writable: false` with a
 *  reason, beside the 114 fields already marked that way. It is here because
 *  the same team owns both services and a change to EXACT ships without waiting
 *  on a second approver in another repository.
 *
 *  IT DOES NOT RETIRE ITSELF, and the two entries do not even retire the same
 *  way. `flipi_score_options` lifts on its own the moment the descriptor can
 *  produce a multiselect, because a picker is the only thing it lacks.
 *  `languages_skills` does not lift at all: a good control would still be
 *  writing down a path PROMOP says nothing should write, and its descriptor
 *  branch never attaches options anyway. Someone deletes that entry by hand
 *  when the write path lands.
 */
interface Unaccepted {
  why: string;
  /** Whether a usable control is enough to lift the block.
   *
   *  It is for a field whose only problem is that this page cannot collect
   *  the value. It is NOT for one whose write is wrong at the far end: a
   *  multiselect over `languages_skills` would be a perfectly good control
   *  sending its answer down a path PROMOP says nothing should write. The
   *  first version of this had one condition for both and a comment saying
   *  otherwise, which is how you end up shipping the comment's opposite. */
  releasedByAControl: boolean;
}

const UNMAPPED_VOCABULARY: Record<string, Unaccepted> = {
  languages_skills: {
    why:
      "This answer cannot be recorded yet: the language vocabulary is still " +
      "moving between services, so anything entered here could not be " +
      "matched against trials.",
    // PROMOP denormalizes this column from `PersonLanguageSkill`. A control
    // appearing changes nothing about that, so this one is released by hand
    // or not at all.
    releasedByAControl: false,
  },
  flipi_score_options: {
    why:
      "This answer cannot be recorded yet: these criteria are stored as a " +
      "list of codes and there is no picker for them here, so anything typed " +
      "could not be matched against trials.",
    // The only thing wrong here is the missing picker: PROMOP accepts these
    // five codes and EXACT reads them back.
    releasedByAControl: true,
  },
};

/** Decide whether the row named by `patientField` may be edited.
 *
 *  `fields` being undefined means the descriptor has not arrived (or the host
 *  supplied no writer): every row is `unknown`, and the page stays exactly as
 *  it is today. That is the safe direction — a control drawn before the answer
 *  is known is a control that may have to be taken away.
 */
export function editabilityOf(
  patientField: string | null | undefined,
  fields: WritableFields | undefined,
): Editability {
  if (!patientField || !fields) return { can: "unknown" };
  const entry = fields[patientField];
  // A name the descriptor does not carry is the ORDINARY case, not drift: the
  // descriptor covers PROMOP's record, and 19 of EXACT's 133 mapped
  // attributes have no column in it — `mipi_risk`, `high_risk_mcl_criteria`,
  // `p53_ihc` and others. So this is `unknown` and not `no`: PROMOP has not
  // refused the write, it has never been asked about the field, and there is
  // no reason worth showing. The row stays as it reads today.
  if (!entry) return { can: "unknown" };
  // Writable, but not through this endpoint. `genetic_mutations` is
  // `writable: true` with `target: "genomics"` and a reason naming the tab to
  // edit it in; PATCHing the record would write nothing. Its own reason is
  // the right thing to show, so this joins the `no` branch rather than
  // pretending the field does not exist.
  const elsewhere = entry.writable && entry.target != null && entry.target !== "patient_record";
  // A structured value has no control here. `value_kind: "json"` covers
  // findings blobs; offering a text box would let a reader type over one.
  const structured = entry.writable && entry.value_kind === "json";
  // Released only by a descriptor that can actually produce a usable control,
  // which is exactly "`controlFor` would say multiselect". Asked by CALLING it
  // rather than by restating its branch: the first version spelled out
  // `optionsOf(entry).length > 0 && entry.multiple`, character for character
  // the same condition, and a copy parts company the day `controlFor` grows a
  // rule. Options alone are not enough — they give a single `select` over a
  // column both matchers read as a comma-separated list, so saving one
  // language would silently drop the others.
  //
  // `!elsewhere && !structured` because those two are refusals of their own
  // with reasons of their own. Unreachable today (both fields are
  // `target: patient_record`, `value_kind: string`), but without it a field
  // that became genomics-targeted would be announced as a vocabulary problem
  // and lose the descriptor's actionable "edit it in the Genomics tab".
  const unaccepted = Object.prototype.hasOwnProperty.call(
    UNMAPPED_VOCABULARY,
    patientField,
  )
    ? UNMAPPED_VOCABULARY[patientField]
    : undefined;
  const unmapped =
    entry.writable &&
    !elsewhere &&
    !structured &&
    unaccepted !== undefined &&
    !(unaccepted.releasedByAControl && controlFor(entry) === "multiselect");
  if (!entry.writable || elsewhere || structured || unmapped) {
    const why = unmapped
      ? // Ahead of the descriptor's own reason, which for these fields says
        // "Written directly to PatientRecord. No OMOP mapping yet." — true,
        // and no use to a reader wondering why the box is gone.
        unaccepted!.why
      : typeof entry.reason === "string" && entry.reason !== ""
        ? entry.reason
        : entry.kind === "alias" && typeof entry.canonical === "string"
          ? `Mirrors ${entry.canonical}; edit that field instead.`
          : structured
            ? "This value is structured; it cannot be edited as plain text here."
            : elsewhere
              ? "This field is edited elsewhere in the patient record."
              : NOT_IN_RECORD;
    return { can: "no", field: patientField, entry, why, announce: Boolean(unmapped) };
  }
  return { can: "edit", field: patientField, entry, control: controlFor(entry) };
}

/** Split a multi-valued text column back into its values.
 *
 *  PROMOP stores the one field that takes several answers — `cytogenetic_markers`
 *  — as a TextField, and its serializer ALWAYS reads it back comma-joined:
 *  `to_representation` returns `", ".join(...)`, never a list, whatever the
 *  write sent. A multiselect seeded from the raw string would therefore show
 *  one option nobody offers and nothing selected.
 *
 *  Commas inside parentheses do not separate, mirroring PROMOP's own
 *  `re.split(r',\s*(?![^()]*\))', ...)`. Marker names carry them:
 *  `inv(3)(q21,q26)` is one marker, and splitting it would produce two the
 *  record has never heard of.
 */
export function splitJoined(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((v) => String(v ?? "")).filter((v) => v !== "");
  if (value == null || value === "") return [];
  return String(value)
    .split(/,\s*(?![^()]*\))/)
    .map((part) => part.trim())
    .filter((part) => part !== "");
}
