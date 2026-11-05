// Trial card — mirrors CancerBot UI v2's `TrialCard` so the federated list
// reads as native inside CB. Layout/structure live in `exact.css`
// (`.exact-card*`); shared bits (score pills, fields) come from `bits.tsx`.
// Both the card body and the "View Trial" button select the trial, which the
// host-agnostic `TrialMatches` turns into the in-remote detail page (CB shows
// its own `/t/:id`; the remote owns the detail view itself).
import type { KeyboardEvent } from "react";

import {
  ActionTooltip,
  EyeIcon,
  FavoriteToggle,
  Field,
  ScorePill,
  SUITABILITY_HREF,
  asText,
} from "./bits";
import { ACTION_TOOLTIPS } from "./tooltips";
import type { TrialMatch } from "./types";

interface Props {
  trial: TrialMatch;
  onSelect?: (trial: TrialMatch) => void;
  isSelected?: boolean;
  /** Whether this trial is bookmarked. `undefined` means not known yet —
   *  the ids are still loading — and renders nothing rather than an
   *  un-bookmarked star that would flip under the reader's eye. */
  isFavorite?: boolean;
  /** Omitted when the host supplied no state adapter, in which case no
   *  control is drawn: a bookmark button with nowhere to write is a button
   *  that forgets. */
  onToggleFavorite?: (next: boolean) => void;
  /** A bookmark write for this trial is on the wire. */
  busy?: boolean;
}

export function TrialCard({
  trial,
  onSelect,
  isSelected,
  isFavorite,
  onToggleFavorite,
  busy,
}: Props) {
  const distance =
    trial.distance != null
      ? `${trial.distance} ${trial.distanceUnits ?? ""}`.trim()
      : "";

  // See the mark below, and the Matching Score beside it.
  const notEligible = trial.matchingType === "not_eligible";

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
            {/* Only the state tabs can ever paint this. The corpus search
                drops a trial the patient no longer qualifies for, so
                `not_eligible` never reaches a card there; the saved-ids
                path keeps it, because a trial the reader bookmarked
                themselves should not vanish from the tab whose badge
                counts it (#568). The hedge is the detail page's — the
                matcher answers over mapped attributes, not a clinician. */}
            {notEligible ? (
              <p className="exact-card__mismatch">
                You may not meet this trial's eligibility criteria
              </p>
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
            {/* No special case for a marked row. The server sends
                `matchScore: 0` with `not_eligible` — the pair the matcher
                returns for that verdict, and the one the detail page this
                card opens shows for the same trial. Without that the score
                was the raw SQL annotation, which counts criteria it could
                EVALUATE and never compares values, so it read 100 beside the
                mismatch line. Guarding it a second time here would put the
                same judgement in two places and let them drift. */}
            <ScorePill
              score={trial.matchScore}
              label="Matching Score"
              tooltip={ACTION_TOOLTIPS.matchingScore}
            />
            <ScorePill
              score={trial.goodnessScore}
              label="Suitability Score"
              href={SUITABILITY_HREF}
              tooltip={ACTION_TOOLTIPS.suitabilityScore}
            />
          </div>
        </div>

        <div className="exact-card__action">
          <FavoriteToggle
            title={trial.briefTitle}
            isFavorite={isFavorite}
            onToggle={onToggleFavorite}
            busy={busy}
          />
          {onSelect ? (
            <ActionTooltip text={ACTION_TOOLTIPS.viewTrial} className="exact-card__view-tip">
              {(tipId) => (
                <button
                  type="button"
                  className="exact-btn-view"
                  aria-describedby={tipId}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleSelect();
                  }}
                >
                  <EyeIcon />
                  <span>View Trial</span>
                </button>
              )}
            </ActionTooltip>
          ) : null}
        </div>
      </div>
    </div>
  );
}
