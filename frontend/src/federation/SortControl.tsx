// CB's `TrialsSortControl`: the three orders as a segmented control, each
// segment carrying its own tooltip. It was a native `<select>` here, which
// could hold only one tooltip — the one for the order already applied — so
// the reader could not read what the other two would do before picking one.
import { SegmentedControl } from "./SegmentedControl";
import { sortOptionsFor } from "./listChrome";
import { SORT_TOOLTIPS } from "./tooltips";

interface Props {
  value: string;
  onChange: (next: string) => void;
}

export function SortControl({ value, onChange }: Props) {
  const options = sortOptionsFor(value).map((option) => ({
    value: option.value,
    label: option.label,
    tooltip: SORT_TOOLTIPS[option.value],
  }));
  return (
    <SegmentedControl
      label="Sort trials by"
      className="exact-seg--grow exact-sort"
      options={options}
      value={value}
      onChange={onChange}
    />
  );
}
