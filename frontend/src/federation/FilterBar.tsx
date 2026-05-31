import type { CSSProperties } from "react";
import type { AxiosInstance } from "axios";

import { useCountries, useFormSettings } from "./hooks";
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
  const countries = useCountries(apiClient);
  const formSettings = useFormSettings(apiClient, diseaseCode);

  // `/form-settings/` exposes the recruitment-status enum under the key
  // `statuses` (see `value_options.py:907`), not `recruitmentStatus`.
  // Same dict also surfaces `trialType` for the trial-type dropdown.
  const recruitmentOptions = formSettings.data?.statuses?.options ?? [];
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
      <label style={LABEL}>
        Country
        <select
          style={INPUT}
          value={filters.country ?? ""}
          onChange={(e) => onChange({ ...filters, country: e.target.value || undefined })}
        >
          <option value="">All</option>
          {countries.data?.results.map((c) => (
            // The backend's `by_location` does
            // `Country.objects.filter(title__iexact=country)` — so we
            // send the country title (e.g. "United States"), not the
            // ISO code. Send-code would silently match nothing.
            <option key={c.id} value={c.title}>
              {c.title}
            </option>
          ))}
        </select>
      </label>
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
