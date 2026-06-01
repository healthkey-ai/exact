import type { CSSProperties } from "react";
import type { AxiosInstance } from "axios";

import { useFormSettings } from "./hooks";
import type { FilterState } from "./types";

interface Props {
  apiClient: AxiosInstance;
  filters: FilterState;
  onChange: (next: FilterState) => void;
  /** Disease code drives the per-disease overrides in `/form-settings/`
   *  (#44 / #63). Pass the patient's disease when known so the
   *  trialType / recruitmentStatus dropdowns match the patient context. */
  diseaseCode?: string;
}

const ROW: CSSProperties = {
  display: "flex",
  gap: "0.75rem",
  flexWrap: "wrap",
  marginBottom: "1rem",
};

const LABEL: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  fontSize: "0.75rem",
  color: "var(--exact-color-text-muted)",
  gap: "0.25rem",
};

const INPUT: CSSProperties = {
  padding: "0.375rem 0.5rem",
  border: "1px solid var(--exact-color-border)",
  borderRadius: "0.25rem",
  font: "inherit",
};

export function FilterBar({ apiClient, filters, onChange, diseaseCode }: Props) {
  const formSettings = useFormSettings(apiClient, diseaseCode);

  // Recruitment status isn't actually exposed by `/form-settings/` —
  // the `statuses` key there is the patient-invitation enum
  // ("Looking for trial", "Waiting for patient acceptance", etc.),
  // not the trial recruitment state. The PR that originally wired
  // `recruitmentStatus` to `statuses` (#114) made the dropdown surface
  // semantically wrong values.
  //
  // The backend filter `by_recruitment_status` (`querysets/trial.py`)
  // treats `RECRUITING` and `RECRUITING_AND_NOT_YET_RECRUITING` as
  // special cases and falls through to a case-insensitive exact match
  // on the raw `Trial.recruitment_status` field for anything else.
  // The canonical CT.gov values are below; `RECRUITING_AND_NOT_YET_…`
  // is the combined convenience value the queryset documents.
  const recruitmentOptions = [
    { value: "RECRUITING", label: "Recruiting" },
    { value: "RECRUITING_AND_NOT_YET_RECRUITING", label: "Recruiting + not yet recruiting" },
    { value: "NOT_YET_RECRUITING", label: "Not yet recruiting" },
    { value: "ENROLLING_BY_INVITATION", label: "Enrolling by invitation" },
    { value: "ACTIVE_NOT_RECRUITING", label: "Active, not recruiting" },
    { value: "COMPLETED", label: "Completed" },
    { value: "SUSPENDED", label: "Suspended" },
    { value: "TERMINATED", label: "Terminated" },
    { value: "WITHDRAWN", label: "Withdrawn" },
  ];
  const trialTypeOptions = formSettings.data?.trialType?.options ?? [];

  return (
    <div style={ROW}>
      <label style={LABEL}>
        Search
        <input
          type="text"
          style={INPUT}
          placeholder="Title…"
          value={filters.searchTitle ?? ""}
          onChange={(e) => onChange({ ...filters, searchTitle: e.target.value || undefined })}
        />
      </label>
      <label style={LABEL}>
        Recruitment
        <select
          style={INPUT}
          value={filters.recruitmentStatus ?? ""}
          onChange={(e) =>
            onChange({ ...filters, recruitmentStatus: e.target.value || undefined })
          }
        >
          <option value="">All</option>
          {recruitmentOptions.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      {/* Country is intentionally NOT user-controlled — `TrialMatches`
       *  derives `filters.country` from `patientInfo.country` so the
       *  trial list scopes to the patient's geography without an extra
       *  click. Hosts that need a cross-border search experience can
       *  add their own override above this component. */}
      <label style={LABEL}>
        Trial type
        <select
          style={INPUT}
          value={filters.trialType ?? ""}
          onChange={(e) => onChange({ ...filters, trialType: e.target.value || undefined })}
        >
          <option value="">All</option>
          {trialTypeOptions.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      <label style={LABEL}>
        Distance (km)
        <input
          type="number"
          min={0}
          style={{ ...INPUT, width: "6rem" }}
          value={filters.distance ?? ""}
          onChange={(e) => {
            const n = Number(e.target.value);
            onChange({
              ...filters,
              distance: Number.isFinite(n) && n > 0 ? n : undefined,
              distanceUnits: Number.isFinite(n) && n > 0 ? "km" : undefined,
            });
          }}
        />
      </label>
      <label style={{ ...LABEL, alignSelf: "end" }}>
        <span style={{ display: "flex", alignItems: "center", gap: "0.375rem" }}>
          <input
            type="checkbox"
            checked={filters.validatedOnly ?? false}
            onChange={(e) =>
              onChange({ ...filters, validatedOnly: e.target.checked || undefined })
            }
          />
          Validated only
        </span>
      </label>
    </div>
  );
}
