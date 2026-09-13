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
import { useEffect, useMemo, useState } from "react";

import {
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
import { FIELD_TOOLTIPS } from "./tooltips";
import type { AdvancedStatus } from "./state";
import type { FilterState, PatientInfo, TrialDetailField } from "./types";
import { FieldEdit } from "./FieldEdit";
import { editabilityOf } from "./writable";
import type { WritableFields } from "./writable";

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

function formatValue(value: unknown, options?: TrialDetailField["options"]): string {
  if (value == null || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (Array.isArray(value)) {
    const parts = value.filter((v) => v != null && v !== "").map((v) => labelOf(v, options));
    return parts.length ? parts.join(", ") : "—";
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
function editableRowNames(fields: TrialDetailField[]): Set<string> {
  const taken = new Set<string>();
  const rows = new Set<string>();
  for (const field of fields) {
    const attribute = field.upatientField;
    // `upatientRecomputed` is the one refusal PROMOP cannot give: it will
    // take the write, and EXACT will replace the value from its inputs before
    // the next match is scored. Accepted, undone, no error — which reads to
    // the patient as an edit that did nothing.
    if (!attribute || field.ureadonly || field.upatientRecomputed) continue;
    if (taken.has(attribute)) continue;
    taken.add(attribute);
    rows.add(field.name);
  }
  return rows;
}

function EligibilityRow({
  field,
  editing,
  editableHere,
}: {
  field: TrialDetailField;
  editing?: RowEditing;
  editableHere?: boolean;
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
  // The reader's own value wins over the record's while a write is out. Not
  // cosmetic: the record genuinely does not hold it yet, and showing the old
  // one for as long as that takes reads as a save that did not take.
  // Only the row that carries the control carries its optimistic value. The
  // others name the same attribute — a min, a max, and a "× upper limit of
  // normal" pair — and the ×ULN row shows a RATIO, so painting the written
  // number there would put a clinically false figure on screen, briefly, in a
  // table a patient reads.
  const attribute = editableHere ? field.upatientField : null;
  const pendingValue =
    attribute && editing && attribute in editing.outstanding
      ? editing.outstanding[attribute]
      : undefined;
  const writePending = attribute != null && editing != null
    && attribute in editing.outstanding;
  const writeFailed = Boolean(attribute && editing && attribute in editing.failed);
  const yours = formatValue(
    writePending ? pendingValue : field.uvalue,
    field.uoptions ?? field.options,
  );
  const tooltip = FIELD_TOOLTIPS[field.ufield as string] ?? FIELD_TOOLTIPS[field.name];
  // Only `edit` puts anything on screen. `no` carries a reason, but PROMOP's
  // reasons are written for whoever is integrating — one of them points at
  // `docs/omop_to_patientrecord.md` — so showing them raw to a patient would
  // be worse than the silence. Surfacing them needs curated wording, and that
  // is a decision, not an oversight.
  const [editorOpen, setEditorOpen] = useState(false);
  const editable =
    editing && editableHere
      ? editabilityOf(field.upatientField, editing.fields)
      : ({ can: "unknown" } as const);

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
        <span className="exact-elig__val">{renderMd(required)}</span>
        {field.units ? <span className="exact-elig__units">{field.units}</span> : null}
        {matched ? (
          <span className="exact-elig__check" aria-label="matches">
            <CheckIcon />
          </span>
        ) : null}
        {notEvaluated ? (
          <span className="exact-elig__note">not a requirement of this trial</span>
        ) : null}
      </div>
      <div
        className={`exact-elig__cell${notMatched ? " is-mismatch" : ""}`}
        data-col="yours"
      >
        <span className="exact-elig__colhdr">Your Value</span>
        {/* The editor stands in for the value while it is open, rather than
            beside it: this column can be a few characters wide, and showing
            the old value next to the box that is replacing it is a question
            nobody should have to answer. */}
        {editorOpen ? null : (
          <>
            <span className="exact-elig__val">{renderMd(yours)}</span>
            {field.uunits ?? field.units ? (
              <span className="exact-elig__units">{field.uunits ?? field.units}</span>
            ) : null}
          </>
        )}
        {/* A mismatch was marked in red and nowhere else, while the register
            card below tells the reader the mismatches are marked above. For
            anyone not seeing the colour that was a promise the page did not
            keep.

            After the units, where the matched cell puts its tick: before
            them it split the value from its unit — "12 ✕ years", read out
            as "12, does not match, years". */}
        {notMatched && !editorOpen ? (
          <span className="exact-elig__mismatch" aria-label="does not match">
            ✕
          </span>
        ) : null}
        {writePending ? (
          <span className="exact-elig__saving">Saving…</span>
        ) : null}
        {writeFailed ? (
          <span className="exact-elig__error" role="alert">
            Couldn't save that. Your value is not in the record.
          </span>
        ) : null}
        {editable.can === "edit" && editing ? (
          <FieldEdit
            field={editable.field}
            label={field.label}
            entry={editable.entry}
            control={editable.control}
            // A refused value wins over the record's: the reader is about to
            // try again, and the thing they want in the box is what they
            // typed.
            value={
              writeFailed && attribute
                ? editing.failed[attribute]
                : writePending
                  ? pendingValue
                  : field.uvalue
            }
            units={field.uunits ?? field.units}
            onSave={(value) => editing.save(editable.field, value)}
            onOpenChange={setEditorOpen}
          />
        ) : null}
      </div>
    </div>
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
      ? " One or more of this trial's requirements does not match your profile — the ones listed above are marked."
      : " One or more of this trial's requirements does not match your profile.";

  return (
    <section
      className={`exact-panel exact-register${notEligible ? " is-warning" : ""}${
        isRegistered ? " is-registered" : ""
      }`}
    >
      <h2 className="exact-panel__title">{heading}</h2>
      <p className="exact-register__text">
        {state}
        {mismatch} Nothing is sent to the trial's coordinators from here.
      </p>
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
        onClick={() => onToggle(!isRegistered)}
      >
        {pending ? "Saving…" : isRegistered ? "Withdraw" : "I'm Interested"}
      </button>
      {failed ? (
        <p className="exact-register__error" role="alert">
          Couldn't save that. Please try again.
        </p>
      ) : null}
    </section>
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
}: Props) {
  // Idempotent: ensures the scoped stylesheet is present even if this page is
  // mounted without `TrialMatches` having run (it already injects on mount).
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
  const editableRows = useMemo(() => editableRowNames(eligibility), [eligibility]);
  const summary = data
    ? data.laySummary || data.briefSummary || data.participationCriteria || ""
    : "";

  // CB's own rule (#4669), and the same field: the detail endpoint scores
  // the trial with the conflict-aware matcher and can answer not_eligible.
  const notEligible = data?.matchingType === "not_eligible";
  const canRegister =
    trialState?.onToggleRegistered != null && trialState.isRegistered !== undefined;

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
        <p style={{ color: "var(--exact-color-not-eligible)" }}>
          Failed to load trial: {(query.error as Error)?.message ?? "unknown error"}
        </p>
      ) : null}

      {data ? (
        <>
          <div className="exact-detail__head">
            <h1 className="exact-detail__title">{data.briefTitle}</h1>
            <FavoriteToggle
              title={data.briefTitle}
              isFavorite={trialState?.isFavorite}
              onToggle={trialState?.onToggleFavorite}
              busy={trialState?.favoriteBusy}
            />
          </div>

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
            <ScorePill score={data.matchScore} label="Matching Score" />
            <ScorePill
              score={data.goodnessScore}
              label="Suitability Score"
              href={SUITABILITY_HREF}
            />
          </div>

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
                {data.link ? (
                  <a
                    className="exact-detail__nct"
                    href={data.link}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    {data.studyId}
                  </a>
                ) : (
                  <span className="exact-field__value">{data.studyId}</span>
                )}
              </div>
              <Field label="Sponsor" value={asText(data.sponsorName)} />
            </div>
          </div>

          <div className="exact-detail__grid">
            {summary ? (
              <section className="exact-panel exact-detail__summary">
                <h2 className="exact-panel__title">Summary</h2>
                <p className="exact-detail__summary-text">{summary}</p>
              </section>
            ) : null}

            <section className="exact-panel exact-detail__elig">
              <h2 className="exact-panel__title">Trial Eligibility Attributes</h2>
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
                      editing={editing}
                      editableHere={editableRows.has(field.name)}
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
            <HighRiskMclPanel breakdown={mcl} titleOf={mclTitle} />
          ) : null}

          {canRegister ? (
            <RegisterInterest
              advancedStatus={trialState!.advancedStatus}
              notEligible={notEligible}
              mismatchesShown={eligibility.some(
                (f) => f.matchingType === "not_matched",
              )}
              isRegistered={trialState!.isRegistered!}
              pending={trialState?.registerPending ?? false}
              failed={trialState?.registerFailed ?? false}
              onToggle={trialState!.onToggleRegistered!}
            />
          ) : null}

          {trialState?.registeredUnavailable ? (
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
