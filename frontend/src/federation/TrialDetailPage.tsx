// Full trial-detail view, mirroring CancerBot UI v2's `/t/:id` page
// (back link, header + score pills, meta, summary, and the read-only
// "Trial Eligibility Attributes" Required/Your-Value table). Rendered
// in-place by `TrialMatches` when a trial is selected — the remote owns the
// detail view rather than re-using the host's router. Data: `GET /trials/{id}/`
// (or `POST /trials/{id}/match/` for the inline-payload path) via
// `useTrialDetail`. v1 is read-only: editing a patient value (CB's pencil
// controls) and the host action buttons (I'm Interested / bookmark / share /
// SoC) are deliberately out of scope.
import { useEffect } from "react";

import { Field, FieldTooltip, ScorePill, SUITABILITY_HREF, asText, renderMd } from "./bits";
import { useTrialDetail } from "./hooks";
import { injectStyles } from "./injectStyles";
import { FIELD_TOOLTIPS } from "./tooltips";
import type { FilterState, PatientInfo, TrialDetailField } from "./types";

interface Props {
  apiClient: import("axios").AxiosInstance;
  trialId: number | string;
  patientInfo?: PatientInfo | null;
  personId?: string | number;
  /** Same study preferences the list used, so detail scores/units agree. */
  filters?: FilterState;
  onBack: () => void;
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

const CheckIcon = () => (
  <svg
    width="16"
    height="16"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M21.801 10A10 10 0 1 1 17 3.335" />
    <path d="m9 11 3 3L22 4" />
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

/** Shown in place of a therapy concept the vocab mirror could not resolve to a
 *  name (e.g. a concept EXACT's projection references but promop's snapshot does
 *  not ship yet — see promop#390). A raw concept_id is meaningless to a user, so
 *  we render a hint instead. The criterion is still shown (never dropped). */
export const UNRESOLVED_CONCEPT_LABEL = "Unknown";

/** Resolve OMOP-mapped therapy `value` concept_ids to their mirror titles (drug
 *  names). For any concept the server did not resolve, render a placeholder hint
 *  rather than the raw id — so the criterion is still visible but not shown as a
 *  meaningless number. Used for the OMOP therapy regimen/component levels, whose
 *  `value` is concept_ids with no `options` map. */
export function formatOmopConcepts(
  value: unknown,
  concepts: NonNullable<TrialDetailField["omopConcepts"]>,
): string {
  const byCode = new Map(concepts.map((c) => [String(c.code), c.title]));
  const ids = Array.isArray(value) ? value : value == null || value === "" ? [] : [value];
  const parts = ids
    .filter((v) => v != null && v !== "")
    .map((v) => byCode.get(String(v)) ?? UNRESOLVED_CONCEPT_LABEL);
  return parts.length ? parts.join(", ") : "—";
}

function EligibilityRow({ field }: { field: TrialDetailField }) {
  const matched = field.matchingType === "matched";
  const notMatched = field.matchingType === "not_matched";
  const required = field.omopConcepts?.length
    ? formatOmopConcepts(field.value, field.omopConcepts)
    : formatValue(field.value, field.options);
  const yours = formatValue(field.uvalue, field.uoptions ?? field.options);
  const tooltip = FIELD_TOOLTIPS[field.ufield as string] ?? FIELD_TOOLTIPS[field.name];

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
      </div>
      <div
        className={`exact-elig__cell${notMatched ? " is-mismatch" : ""}`}
        data-col="yours"
      >
        <span className="exact-elig__colhdr">Your Value</span>
        <span className="exact-elig__val">{renderMd(yours)}</span>
        {field.uunits ?? field.units ? (
          <span className="exact-elig__units">{field.uunits ?? field.units}</span>
        ) : null}
      </div>
    </div>
  );
}

export function TrialDetailPage({
  apiClient,
  trialId,
  patientInfo,
  personId,
  filters,
  onBack,
}: Props) {
  // Idempotent: ensures the scoped stylesheet is present even if this page is
  // mounted without `TrialMatches` having run (it already injects on mount).
  useEffect(() => {
    injectStyles();
  }, []);

  const query = useTrialDetail({ apiClient, trialId, patientInfo, personId, filters });
  const data = query.data;

  const eligibility = data?.details?.trialEligibilityAttributes ?? [];
  const summary = data
    ? data.laySummary || data.briefSummary || data.participationCriteria || ""
    : "";

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
          <h1 className="exact-detail__title">{data.briefTitle}</h1>

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
                    <EligibilityRow key={field.name} field={field} />
                  ))}
                </div>
              ) : (
                <p style={{ color: "var(--exact-color-text-muted)", margin: 0 }}>
                  No eligibility attributes to show for this trial.
                </p>
              )}
            </section>
          </div>
        </>
      ) : null}
    </div>
  );
}

export default TrialDetailPage;
