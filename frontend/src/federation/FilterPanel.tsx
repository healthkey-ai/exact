// The filter panel, mirroring CB's `TrialFiltersPanel` (ui.v2). Same field
// set, same responsive grid, same Reset — built out of native controls
// because the remote ships no component library and must not pull one into
// whichever host mounts it.
//
// Trial purpose is a multiselect, matching CB #4663: the backend's `_str_list`
// parser takes several codes and `by_trial_purpose` answers with their union
// (#428). It is `<details>` + checkboxes rather than a `<select multiple>`,
// which is unusable on touch and renders as a fixed-height scroll box on the
// desktop — CB's own control is a popover with checkboxes, and `<details>` is
// the native element that behaves like one without a library.
import { useEffect, useId, useRef } from "react";
import type { AxiosInstance } from "axios";

import { DISTANCE_UNITS, LAST_UPDATE_OPTIONS, isActiveDistance } from "./filters";
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
  // A value the option list does not contain — one stored by another
  // client, one from a host's `initialFilters`, one whose list belongs to
  // a different disease — otherwise collapses this control to its
  // placeholder: the reader is shown "Any" over a list the value is
  // narrowing, with the badge counting a filter they can neither see nor
  // clear from here. Showing it, even under its raw code, is what makes it
  // clearable. (Deciding whether a value is legal is #444; this is the
  // half that needs no answer from the server.)
  //
  // It also covers a legal value while the catalog is still on its way, so
  // a saved filter reads as its own code for that moment and then as its
  // label. That is the honest order: the code is what the request is
  // carrying either way.
  const shown =
    value && !options.some((option) => option.value === value)
      ? [...options, { value, label: value }]
      : options;
  return (
    <Field label={label}>
      <select
        className="exact-filter__input"
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value || undefined)}
      >
        {hasEmpty ? null : <option value="">Any</option>}
        {/* Keys are prefixed so the key space is the values themselves:
            keyed on `option.value || "__any__"`, a stored value of literally
            `"__any__"` collided with the server's own empty-value entry. */}
        {shown.map((option) => (
          <option key={`opt:${option.value}`} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </Field>
  );
}

/** Several codes at once, for a field where the server answers with the union.
 *
 *  No selection means no filter, so there is no "Any" entry to pick: clearing
 *  every box IS Any, and an explicit one would be a fourth state to keep in
 *  sync with the other three. The server's own empty-valued "ALL" option is
 *  dropped for the same reason — as a checkbox it would read as a purpose.
 */
function MultiSelectFilter({
  label,
  values,
  options,
  onChange,
}: {
  label: string;
  values: string[] | undefined;
  options: Option[];
  onChange: (next: string[] | undefined) => void;
}) {
  const labelId = useId();
  const selected = values ?? [];
  const choices = options.filter((option) => option.value !== "");
  const detailsRef = useRef<HTMLDetailsElement>(null);

  // `/form-settings/` may still be in flight, or have failed, in which case
  // `choices` is empty. A selected code the options do not describe still gets
  // a row, labelled by its own value: showing it only in the summary would
  // name a filter the reader cannot untick, leaving Reset — which clears every
  // other filter too — as the only way out of it.
  const rows = [
    ...choices,
    ...selected
      .filter((value) => !choices.some((option) => option.value === value))
      .map((value) => ({ value, label: value })),
  ];
  const chosen = selected.map(
    (value) => rows.find((option) => option.value === value)?.label ?? value,
  );
  const summary =
    chosen.length === 0
      ? "Any"
      : chosen.length <= 2
        ? chosen.join(", ")
        : `${chosen.length} selected`;

  // `<details>` is not a popover: it has no light dismiss, so without these
  // the open list sits over the two filters below it until the summary is
  // clicked again.
  useEffect(() => {
    const element = detailsRef.current;
    if (!element) return;
    const closeIfOutside = (event: MouseEvent) => {
      if (!element.contains(event.target as Node)) element.open = false;
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !element.open) return;
      element.open = false;
      // Focus goes back to the control the reader opened, not to the document.
      element.querySelector("summary")?.focus();
    };
    document.addEventListener("pointerdown", closeIfOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeIfOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, []);

  const toggle = (value: string) => {
    const next = new Set(selected);
    if (!next.delete(value)) next.add(value);
    // Canonical order — the server's own option order, with anything it does
    // not offer kept in the order it arrived. Click order would put the same
    // selection on the wire two ways, which is two react-query cache entries
    // and two round-trips for one answer, while `sameValue` treats them as
    // the same search.
    const known = choices.map((option) => option.value).filter((v) => next.has(v));
    const unknown = selected.filter(
      (v) => next.has(v) && !choices.some((option) => option.value === v),
    );
    const codes = [...known, ...unknown];
    // `undefined` rather than `[]` when the last box is cleared: the filter
    // helpers treat both as inactive, but `undefined` is what every other
    // control emits and what a cleared field is persisted as.
    onChange(codes.length ? codes : undefined);
  };

  return (
    <div className="exact-filter">
      <span className="exact-filter__label" id={labelId}>
        {label}
      </span>
      <details className="exact-filter__multi" ref={detailsRef}>
        {/* `aria-labelledby` on the summary, not only on the group: the
            summary is the focusable control, and without it the whole field
            is announced as just its own value. */}
        <summary
          className="exact-filter__input exact-filter__multi-summary"
          aria-labelledby={`${labelId} ${labelId}-value`}
        >
          <span id={`${labelId}-value`}>{summary}</span>
        </summary>
        <div
          className="exact-filter__multi-list"
          role="group"
          aria-labelledby={labelId}
        >
          {rows.map((option) => (
            <label key={option.value} className="exact-filter__multi-option">
              <input
                type="checkbox"
                checked={selected.includes(option.value)}
                onChange={() => toggle(option.value)}
              />
              <span>{option.label}</span>
            </label>
          ))}
        </div>
      </details>
    </div>
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

        <MultiSelectFilter
          label="Trial purpose"
          values={filters.trialPurpose}
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

        {/* A years count, not a date — `by_date_since` runs the value
            through `cast_str_to_int`, which takes digits only. CB renders
            this as a date picker and PATCHes an ISO string, which that
            helper drops on the floor, so CB's own control filters nothing
            (#429). Offer what the backend actually implements. */}
        <SelectFilter
          label="Updated within"
          value={filters.lastUpdate}
          options={[...LAST_UPDATE_OPTIONS]}
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
