// Trial card — restyled to mirror CancerBot UI v2's `TrialCard`
// (CB `client/components/trials/TrialCard.tsx`) so the federated list reads
// as native inside CB. Layout/structure live in `exact.css` (`.exact-card*`,
// `.exact-pill*`); tier colors are applied inline from the `--exact-color-*`
// token set so the host can still re-theme. Eligibility is conveyed by the
// list's group headers (CB uses tabs for that), so — unlike the old card —
// there's no per-card eligibility badge here.
import type { CSSProperties, KeyboardEvent } from "react";
import { useState } from "react";

import type { TrialMatch } from "./types";

interface Props {
  trial: TrialMatch;
  onSelect?: (trial: TrialMatch) => void;
  isSelected?: boolean;
}

const COLLAPSE_THRESHOLD = 80;

type Tier = "green" | "yellow" | "red" | "neutral";

const TIER_TOKENS: Record<Tier, { border: string; bg: string; text: string }> = {
  green: {
    border: "var(--exact-color-success-200)",
    bg: "var(--exact-color-success-50)",
    text: "var(--exact-color-success-700)",
  },
  yellow: {
    border: "var(--exact-color-warning-200)",
    bg: "var(--exact-color-warning-50)",
    text: "var(--exact-color-warning-700)",
  },
  red: {
    border: "var(--exact-color-error-200)",
    bg: "var(--exact-color-error-50)",
    text: "var(--exact-color-error-700)",
  },
  neutral: {
    border: "var(--exact-color-border)",
    bg: "var(--exact-color-surface)",
    text: "var(--exact-color-text-tertiary)",
  },
};

// CB `getScoreColor`: ≥80 green, ≥60 yellow, else red. Exported for unit tests.
export function scoreTier(score: number): Tier {
  if (score >= 80) return "green";
  if (score >= 60) return "yellow";
  return "red";
}

function ScorePill({
  score,
  label,
  href,
}: {
  score: number | null | undefined;
  label: string;
  href?: string;
}) {
  // A missing score (`matchScore` is null on the `/trials/` list when there's
  // no patient context) renders neutral/gray rather than green, so an absent
  // score doesn't read as a good match. CB always has patient context so it
  // never hits this; the remote's host-agnostic list does.
  const tier = TIER_TOKENS[score != null ? scoreTier(score) : "neutral"];
  const display = score != null ? `${Math.round(score)}%` : "N/A";

  const pill = (
    <span
      className="exact-pill"
      style={{ borderColor: tier.border, background: tier.bg }}
    >
      <span
        className="exact-pill__val"
        style={{ borderColor: tier.border, color: tier.text }}
      >
        {display}
      </span>
      <span className="exact-pill__label" style={{ color: tier.text }}>
        {label}
      </span>
    </span>
  );

  if (href) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        style={{ textDecoration: "none" }}
        onClick={(e) => e.stopPropagation()}
      >
        {pill}
      </a>
    );
  }
  return pill;
}

function Field({
  label,
  value,
  collapsible,
}: {
  label: string;
  value: string;
  collapsible?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const trimmed = value.trim() || "—";
  const truncatable = !!collapsible && trimmed.length > COLLAPSE_THRESHOLD;
  const displayed =
    truncatable && !expanded
      ? trimmed.slice(0, COLLAPSE_THRESHOLD).trimEnd() + "…"
      : trimmed;

  return (
    <div className="exact-field">
      <span className="exact-field__label">{label}: </span>
      <span className="exact-field__value">{displayed}</span>
      {truncatable ? (
        <button
          type="button"
          className="exact-field__toggle"
          onClick={(e) => {
            e.stopPropagation();
            setExpanded((v) => !v);
          }}
        >
          {expanded ? "[less]" : "[more]"}
        </button>
      ) : null}
    </div>
  );
}

/** Coerce the permissive list/array-ish trial fields into a display string.
 *  Exported for unit tests. */
export function asText(value: unknown, sep = ", "): string {
  if (value == null) return "";
  if (Array.isArray(value)) return value.filter(Boolean).join(sep);
  return String(value);
}

const EyeIcon = () => (
  <svg
    width="20"
    height="20"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);

export function TrialCard({ trial, onSelect, isSelected }: Props) {
  const distance =
    trial.distance != null
      ? `${trial.distance} ${trial.distanceUnits ?? ""}`.trim()
      : "";

  const handleSelect = () => onSelect?.(trial);
  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!onSelect) return;
    // Ignore Enter/Space that bubbled up from a nested control (the
    // "View Trial" link, a score link, or the Field `[more]` toggle) so
    // activating one of those doesn't also select the card. `onClick`
    // stopPropagation on those controls covers mouse; this covers keyboard.
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      handleSelect();
    }
  };

  const cardStyle: CSSProperties | undefined = onSelect
    ? undefined
    : { cursor: "default" };

  // Card is a `<div role="button">`, not a real `<button>`: it contains its
  // own interactive controls (the "View Trial" link, score links), and
  // nesting `<a>`/`<button>` inside a `<button>` is invalid HTML.
  return (
    <div
      className={`exact-card${isSelected ? " is-selected" : ""}`}
      style={cardStyle}
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
              href="https://medium.com/cancerbot/what-makes-a-trial-good-for-a-patient-a43e5b651754"
            />
          </div>
        </div>

        <div className="exact-card__action">
          {trial.link ? (
            <a
              className="exact-btn-view"
              href={trial.link}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(e) => e.stopPropagation()}
            >
              <EyeIcon />
              <span>View Trial</span>
            </a>
          ) : null}
        </div>
      </div>
    </div>
  );
}
