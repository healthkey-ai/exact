// Sort dropdown, mirroring CB's `TrialsSortControl`. A native `<select>`
// rather than a styled listbox: the remote ships no component library, and
// a native control is keyboard- and screen-reader-correct for free.
import { ActionTooltip } from "./bits";
import { sortOptionsFor } from "./listChrome";
import { SORT_TOOLTIPS } from "./tooltips";

interface Props {
  value: string;
  onChange: (next: string) => void;
}

export function SortControl({ value, onChange }: Props) {
  // CB puts a tooltip on each sort option; a native <select> cannot, so the
  // control describes the order that is currently applied.
  const select = (tipId?: string) => (
    <select
      className="exact-sort__select"
      value={value}
      aria-describedby={tipId}
      onChange={(e) => onChange(e.target.value)}
    >
      {sortOptionsFor(value).map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
  // Always wrapped, even for an order without a tooltip, so picking one
  // does not re-mount the select out from under the keyboard.
  return (
    <label className="exact-sort">
      <span className="exact-sort__label">Sort</span>
      <ActionTooltip text={SORT_TOOLTIPS[value]} align="start">
        {select}
      </ActionTooltip>
    </label>
  );
}
