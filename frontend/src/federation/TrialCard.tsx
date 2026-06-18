// Trial card — mirrors CancerBot UI v2's `TrialCard` so the federated list
// reads as native inside CB. Layout/structure live in `exact.css`
// (`.exact-card*`); shared bits (score pills, fields) come from `bits.tsx`.
// Both the card body and the "View Trial" button select the trial, which the
// host-agnostic `TrialMatches` turns into the in-remote detail page (CB shows
// its own `/t/:id`; the remote owns the detail view itself).
import type { KeyboardEvent } from "react";

import { EyeIcon, Field, ScorePill, SUITABILITY_HREF, asText } from "./bits";
import type { TrialMatch } from "./types";

interface Props {
  trial: TrialMatch;
  onSelect?: (trial: TrialMatch) => void;
  isSelected?: boolean;
}

export function TrialCard({ trial, onSelect, isSelected }: Props) {
  const distance =
    trial.distance != null
      ? `${trial.distance} ${trial.distanceUnits ?? ""}`.trim()
      : "";

  const handleSelect = () => onSelect?.(trial);
  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!onSelect) return;
    // Ignore Enter/Space that bubbled up from a nested control (the
    // "View Trial" button, a score link, or the Field `[more]` toggle) so
    // activating one of those doesn't also re-fire card selection.
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handleSelect();
    }
  };

  // Card is a `<div role="button">`, not a real `<button>`: it contains its
  // own interactive controls (the "View Trial" button, score links), and
  // nesting interactive elements inside a `<button>` is invalid HTML.
  return (
    <div
      className={`exact-card${isSelected ? " is-selected" : ""}`}
      style={onSelect ? undefined : { cursor: "default" }}
      role={onSelect ? "button" : undefined}
      tabIndex={onSelect ? 0 : undefined}
      aria-pressed={onSelect ? isSelected : undefined}
      onClick={onSelect ? handleSelect : undefined}
      onKeyDown={onSelect ? handleKeyDown : undefined}
    >
      <div className="exact-card__body">
        <div className="exact-card__main">
          <div className="exact-card__head">
            <h3 className="exact-card__title">{trial.briefTitle}</h3>
            {trial.studyId ? (
              <span className="exact-card__studyid">{trial.studyId}</span>
            ) : null}

            <div className="exact-card__fields">
              <Field label="Location" value={asText(trial.location)} collapsible />
              <Field label="Distance" value={distance} />
            </div>

            <div className="exact-card__fields">
              <Field
                label="Intervention/Treatment"
                value={asText(trial.interventionTreatments)}
              />
              <Field label="Trial Type" value={asText(trial.trialType)} />
              <Field label="Phase" value={asText(trial.phase, " / ")} />
              <Field label="Status" value={asText(trial.recruitingStatus)} />
            </div>
          </div>

          <div className="exact-card__scores">
            <ScorePill score={trial.matchScore} label="Matching Score" />
            <ScorePill
              score={trial.goodnessScore}
              label="Suitability Score"
              href={SUITABILITY_HREF}
            />
          </div>
        </div>

        <div className="exact-card__action">
          {onSelect ? (
            <button
              type="button"
              className="exact-btn-view"
              onClick={(e) => {
                e.stopPropagation();
                handleSelect();
              }}
            >
              <EyeIcon />
              <span>View Trial</span>
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
