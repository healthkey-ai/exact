// The filter panel, mirroring CB's `TrialFiltersPanel` (ui.v2). Same field
// set, same responsive grid, same Reset — built out of native controls
// because the remote ships no component library and must not pull one into
// whichever host mounts it.
//
// One field is deliberately absent: CB's trial-purpose control is a
// multiselect (CB #4663) and this line's backend parses `trialPurpose` as a
// single value (`_str`, not `_str_list`, in study_preferences.py), so a
// multiselect here would send several values and the server would keep one
// without saying which. Single-select until that parser is ported — #428.
import type { AxiosInstance } from "axios";

import { DISTANCE_UNITS, isActiveDistance } from "./filters";
import { useFormSettings } from "./hooks";
import type { FilterState } from "./types";

interface Props {
  apiClient: AxiosInstance;
  filters: FilterState;
  onChange: (next: FilterState) => void;
  onReset: () => void;
  canReset: boolean;
  /** Drives the per-disease overrides in `/form-settings/` (#44 / #63), so
   *  the trial-type list matches the patient's disease. */
  diseaseCode?: string;
}

interface Option {
  value: string;
  label: string;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="exact-filter">
      <span className="exact-filter__label">{label}</span>
      {children}
    </label>
  );
}

function TextFilter({
  label,
  placeholder,
  value,
  onChange,
}: {
  label: string;
  placeholder: string;
  value: string | undefined;
  onChange: (next: string | undefined) => void;
}) {
  return (
    <Field label={label}>
      <input
        type="text"
        className="exact-filter__input"
        placeholder={placeholder}
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || undefined)}
      />
    </Field>
  );
}

function SelectFilter({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string | undefined;
  options: Option[];
  onChange: (next: string | undefined) => void;
}) {
  // The server's option lists carry their own "all" entry with an empty
  // value; when one is missing we supply the placeholder ourselves, so the
  // control always offers a way back to no filter.
  const hasEmpty = options.some((option) => option.value === "");
  return (
    <Field label={label}>
      <select
        className="exact-filter__input"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || undefined)}
      >
        {hasEmpty ? null : <option value="">Any</option>}
        {options.map((option) => (
          <option key={option.value || "__any__"} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </Field>
  );
}

export function FilterPanel({
  apiClient,
  filters,
  onChange,
  onReset,
  canReset,
  diseaseCode,
}: Props) {
  const formSettings = useFormSettings(apiClient, diseaseCode);
  const options = (key: string): Option[] =>
    formSettings.data?.[key]?.options ?? [];

  const set = (patch: Partial<FilterState>) => onChange({ ...filters, ...patch });

  return (
    <div className="exact-filters">
      <div className="exact-filters__grid">
        <TextFilter
          label="Title"
          placeholder="Search by title…"
          value={filters.searchTitle}
          onChange={(v) => set({ searchTitle: v })}
        />

        <SelectFilter
          label="Trial purpose"
          value={filters.trialPurpose}
          options={options("trialPurpose")}
          onChange={(v) => set({ trialPurpose: v })}
        />

        <SelectFilter
          label="Trial type"
          value={filters.trialType}
          options={options("trialType")}
          onChange={(v) => set({ trialType: v })}
        />

        <TextFilter
          label="Treatment"
          placeholder="Search by treatment…"
          value={filters.searchTreatment}
          onChange={(v) => set({ searchTreatment: v })}
        />

        <TextFilter
          label="Sponsor"
          placeholder="Search by sponsor…"
          value={filters.sponsor}
          onChange={(v) => set({ sponsor: v })}
        />

        <SelectFilter
          label="Recruitment status"
          value={filters.recruitmentStatus}
          // Added to `/form-settings/` in #417. The neighbouring `statuses`
          // key is the patient-invitation enum and must not be used here.
          options={options("recruitmentStatuses")}
          onChange={(v) => set({ recruitmentStatus: v })}
        />

        <SelectFilter
          label="Phase (this or later)"
          value={filters.phase}
          options={options("phases")}
          onChange={(v) => set({ phase: v })}
        />

        {/* A years count. The backend takes an ISO date too as of #429 — it
            used to run every value through `cast_str_to_int` and drop
            anything that was not digits, which is why CB's own date picker
            has never filtered anything — so a date control is now possible
            here and is deliberately not this change: it needs `filters.ts`,
            which is open in another branch. The count is what this control
            has always sent and it keeps working. */}
        <SelectFilter
          label="Updated within"
          value={filters.lastUpdate}
          options={[
            { value: "1", label: "the last year" },
            { value: "2", label: "the last 2 years" },
            { value: "3", label: "the last 3 years" },
            { value: "5", label: "the last 5 years" },
          ]}
          onChange={(v) => set({ lastUpdate: v })}
        />

        <SelectFilter
          label="Register"
          value={filters.register}
          options={options("register")}
          onChange={(v) => set({ register: v })}
        />

        <Field label="Max distance">
          <div className="exact-filter__pair">
            <input
              type="number"
              min={0}
              className="exact-filter__input"
              placeholder="Any"
              // An unusable value renders as empty rather than being echoed
              // back: a host-supplied `-1` would otherwise sit visible in the
              // box while the badge counted nothing, the units select stayed
              // disabled and nothing went on the wire. Empty is what "no
              // distance filter" actually looks like.
              value={isActiveDistance(filters.distance) ? filters.distance : ""}
              onChange={(e) => {
                // `> 0`, not just "is a number": the backend gates on
                // `if study_info.distance:`, so a zero radius applies no
                // limit at all — while the badge would have counted it and
                // the units select would have sat there enabled, both
                // claiming a filter that is not running.
                const parsed = Number(e.target.value);
                const next = isActiveDistance(parsed) ? parsed : undefined;
                set({
                  distance: next,
                  // Units alone filter nothing, so they arrive with a
                  // distance and leave with it.
                  distanceUnits:
                    next == null ? undefined : filters.distanceUnits ?? "km",
                });
              }}
            />
            <select
              className="exact-filter__input exact-filter__units"
              value={filters.distanceUnits ?? "km"}
              onChange={(e) =>
                set({ distanceUnits: e.target.value as FilterState["distanceUnits"] })
              }
              disabled={!isActiveDistance(filters.distance)}
            >
              {DISTANCE_UNITS.map((unit) => (
                <option key={unit.value} value={unit.value}>
                  {unit.label}
                </option>
              ))}
            </select>
          </div>
        </Field>

      </div>

      <div className="exact-filters__footer">
        <button
          type="button"
          className="exact-filters__reset"
          onClick={onReset}
          disabled={!canReset}
        >
          Reset filters
        </button>
      </div>
    </div>
  );
}
