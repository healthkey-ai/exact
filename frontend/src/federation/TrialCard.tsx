import type { CSSProperties } from "react";

import type { TrialMatch } from "./types";

interface Props {
  trial: TrialMatch;
  onSelect?: (trial: TrialMatch) => void;
  isSelected?: boolean;
}

const BADGE_BASE: CSSProperties = {
  padding: "0.125rem 0.5rem",
  borderRadius: "0.25rem",
  fontSize: "0.75rem",
  border: "1px solid transparent",
};

const ELIGIBLE_BADGE: CSSProperties = {
  ...BADGE_BASE,
  background: "#dcfce7",
  color: "#166534",
  borderColor: "#86efac",
};

const POTENTIAL_BADGE: CSSProperties = {
  ...BADGE_BASE,
  background: "#fef3c7",
  color: "#92400e",
  borderColor: "#fcd34d",
};

const NOT_ELIGIBLE_BADGE: CSSProperties = {
  ...BADGE_BASE,
  background: "#fee2e2",
  color: "#991b1b",
  borderColor: "#fca5a5",
};

function matchingStyles(t: TrialMatch): { style: CSSProperties; label: string } {
  if (t.matchingType === "eligible") {
    return { style: ELIGIBLE_BADGE, label: "Eligible" };
  }
  if (t.matchingType === "not_eligible") {
    return { style: NOT_ELIGIBLE_BADGE, label: "Not Eligible" };
  }
  return { style: POTENTIAL_BADGE, label: "Potential" };
}

export function TrialCard({ trial, onSelect, isSelected }: Props) {
  const m = matchingStyles(trial);
  const card: CSSProperties = {
    border: `1px solid var(--exact-color-border)`,
    borderRadius: "var(--exact-border-radius)",
    padding: "1rem",
    background: isSelected ? "#f9fafb" : "var(--exact-color-surface)",
    cursor: onSelect ? "pointer" : "default",
    width: "100%",
    textAlign: "left",
    font: "inherit",
  };
  const meta: CSSProperties = {
    color: "var(--exact-color-text-muted)",
    fontSize: "0.875rem",
    marginTop: "0.5rem",
  };

  const body = (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem" }}>
        <strong style={{ color: "var(--exact-color-text)" }}>{trial.briefTitle}</strong>
        <span style={m.style}>{m.label}</span>
      </div>
      <div style={meta}>
        {[
          trial.studyId,
          trial.recruitingStatus,
          trial.phase && trial.phase.length ? trial.phase.join(" / ") : null,
        ]
          .filter(Boolean)
          .join(" · ")}
      </div>
      {(() => {
        // Build the score/distance line by filtering out missing pieces
        // before joining — a naive concatenation puts a stray leading
        // separator on the line when matchScore is null but
        // goodnessScore is present (the common case for the `/trials/`
        // list when patient context is missing).
        const pieces: string[] = [];
        if (trial.matchScore != null) pieces.push(`Match score: ${trial.matchScore}`);
        if (trial.goodnessScore != null) pieces.push(`Goodness: ${trial.goodnessScore}`);
        if (trial.distance != null) {
          pieces.push(`${trial.distance} ${trial.distanceUnits ?? ""}`.trim());
        }
        return pieces.length ? <div style={meta}>{pieces.join(" · ")}</div> : null;
      })()}
      {trial.sponsor ? (
        <div style={meta}>Sponsor: {trial.sponsor}</div>
      ) : null}
    </>
  );

  if (onSelect) {
    return (
      <button type="button" style={card} onClick={() => onSelect(trial)}>
        {body}
      </button>
    );
  }
  return <div style={card}>{body}</div>;
}
