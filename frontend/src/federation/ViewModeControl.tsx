// CB's `TrialsViewMode`: list or map, as two icon segments at the start of
// the controls row. It was a single button here that said what pressing it
// would do ("Map" while the list showed), which named the action but never
// the state — the reader could not tell which view they were in without
// looking at the page below it.
import { ListIcon, MapPinIcon } from "./bits";
import { SegmentedControl } from "./SegmentedControl";
import { ACTION_TOOLTIPS } from "./tooltips";

export type ViewMode = "list" | "map";

const OPTIONS = [
  { value: "list", label: "List view", icon: <ListIcon />, tooltip: ACTION_TOOLTIPS.list },
  { value: "map", label: "Map view", icon: <MapPinIcon />, tooltip: ACTION_TOOLTIPS.map },
];

export function ViewModeControl({
  value,
  onChange,
}: {
  value: ViewMode;
  onChange: (next: ViewMode) => void;
}) {
  return (
    <SegmentedControl
      label="View"
      options={OPTIONS}
      value={value}
      onChange={(next) => onChange(next as ViewMode)}
    />
  );
}
