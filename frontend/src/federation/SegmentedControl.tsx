// CB's `TrialsViewMode` and `TrialsSortControl` are the same control with
// different contents: joined buttons in one bordered box, exactly one chosen.
// This is that control, once.
//
// It is a radiogroup, not a row of buttons. It replaces a native `<select>`
// (sort) and a toggle button (view mode), and the radio pattern is what keeps
// the keyboard as good as the `<select>` was: one tab stop for the whole
// group, arrows moving between the options and choosing as they go.
import { useRef } from "react";
import { ActionTooltip } from "./bits";

export interface Segment {
  value: string;
  /** The accessible name; also the visible text unless `icon` is given. */
  label: string;
  /** An icon-only segment, as CB's view mode is. `label` still names it. */
  icon?: React.ReactNode;
  tooltip?: string;
}

/** Which key or click chose the option. The arrows choose as they move —
 *  that is the radiogroup pattern and what a `<select>` does — so a caller
 *  whose change is expensive (a request, a re-rank) can hold the keyboard's
 *  run of them back without giving up the pattern. */
export type SegmentSource = "pointer" | "keyboard";

interface Props {
  /** Names the group to a screen reader; CB shows no visible label. */
  label: string;
  options: Segment[];
  value: string;
  onChange: (next: string, source: SegmentSource) => void;
  /** `exact-seg--grow` makes the segments share the width evenly. */
  className?: string;
}

export function SegmentedControl({ label, options, value, onChange, className }: Props) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const selected = options.findIndex((option) => option.value === value);

  const pick = (index: number) => {
    const next = options[index];
    if (!next) return;
    buttons.current[index]?.focus();
    onChange(next.value, "keyboard");
  };

  // Arrows wrap, as the radiogroup pattern asks, and choose on arrival: this
  // stands in for a `<select>`, where an arrow also changes the value.
  const onKeyDown = (event: React.KeyboardEvent, index: number) => {
    const step =
      event.key === "ArrowRight" || event.key === "ArrowDown"
        ? 1
        : event.key === "ArrowLeft" || event.key === "ArrowUp"
          ? -1
          : 0;
    if (step !== 0) {
      event.preventDefault();
      pick((index + step + options.length) % options.length);
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      pick(event.key === "Home" ? 0 : options.length - 1);
    }
  };

  return (
    <div className={`exact-seg${className ? ` ${className}` : ""}`} role="radiogroup" aria-label={label}>
      {options.map((option, index) => {
        const isOn = option.value === value;
        return (
          <ActionTooltip
            key={option.value}
            text={option.tooltip}
            align="start"
            className="exact-seg__item"
            // The group owns the radios; the wrap between them is not part of
            // the picture a screen reader builds of the set.
            wrapRole="none"
          >
            {(tipId) => (
              <button
                type="button"
                ref={(element) => {
                  buttons.current[index] = element;
                }}
                role="radio"
                aria-checked={isOn}
                // Icon-only segments have no text to read out.
                aria-label={option.icon ? option.label : undefined}
                aria-describedby={tipId}
                // One tab stop for the group. A value the options do not hold
                // would leave the group unreachable by tab, so the first
                // segment takes the stop until something is chosen.
                tabIndex={isOn || (selected < 0 && index === 0) ? 0 : -1}
                className={`exact-seg__btn${isOn ? " is-on" : ""}`}
                onClick={() => onChange(option.value, "pointer")}
                onKeyDown={(event) => onKeyDown(event, index)}
              >
                {option.icon ?? <span className="exact-seg__label">{option.label}</span>}
              </button>
            )}
          </ActionTooltip>
        );
      })}
    </div>
  );
}
