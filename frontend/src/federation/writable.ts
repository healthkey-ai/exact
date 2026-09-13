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
  /** Why, in words fit to show a reader.
   *
   *  NOT only on refusals. PROMOP attaches a `reason` to plenty of writable
   *  entries too — "Written directly to PatientRecord. No OMOP mapping yet.",
   *  or `relapse_count`'s "Inferred by default; enter a value to override".
   *  Rendering it as an error message would be wrong; it is a note. */
  reason?: string;
  /** The field this one mirrors. Usually an `alias`, but not only: PROMOP
   *  sets it on `refractory_status`, which is `direct` and writable. */
  canonical?: string;
  value_kind?: string;
  unit?: string;
  multiple?: boolean;
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
  | { can: "no"; field: string; entry: WritableFieldEntry; why: string }
  | { can: "unknown" };

const NOT_IN_RECORD =
  "This attribute is not part of the patient record, so there is nowhere to save it.";

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
  if (!entry.writable || elsewhere || structured) {
    const why =
      typeof entry.reason === "string" && entry.reason !== ""
        ? entry.reason
        : entry.kind === "alias" && typeof entry.canonical === "string"
          ? `Mirrors ${entry.canonical}; edit that field instead.`
          : structured
            ? "This value is structured; it cannot be edited as plain text here."
            : elsewhere
              ? "This field is edited elsewhere in the patient record."
              : NOT_IN_RECORD;
    return { can: "no", field: patientField, entry, why };
  }
  return { can: "edit", field: patientField, entry, control: controlFor(entry) };
}
