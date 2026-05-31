import type { CSSProperties } from "react";

import type { TrialMatch } from "./types";

interface Props {
  trial: TrialMatch;
  onSelect?: (trial: TrialMatch) => void;
  isSelected?: boolean;
}

function matchingStyles(t: TrialMatch): { badge: string; label: string } {
  if (t.matchingType === "eligible") {
    return {
      badge:
        "background:#dcfce7;color:#166534;border:1px solid #86efac;padding:0.125rem 0.5rem;border-radius:0.25rem;font-size:0.75rem;",
      label: "Eligible",
    };
  }
  if (t.matchingType === "not_eligible") {
    return {
      badge:
        "background:#fee2e2;color:#991b1b;border:1px solid #fca5a5;padding:0.125rem 0.5rem;border-radius:0.25rem;font-size:0.75rem;",
      label: "Not Eligible",
    };
  }
  return {
    badge:
      "background:#fef3c7;color:#92400e;border:1px solid #fcd34d;padding:0.125rem 0.5rem;border-radius:0.25rem;font-size:0.75rem;",
    label: "Potential",
  };
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
        <span style={{ ...parseStyle(m.badge) }}>{m.label}</span>
      </div>
      <div style={meta}>
        {trial.studyId} · {trial.recruitingStatus}
        {trial.phase && trial.phase.length ? ` · ${trial.phase.join(" / ")}` : null}
      </div>
      <div style={meta}>
        {trial.matchScore != null ? `Match score: ${trial.matchScore}` : null}
        {trial.goodnessScore != null ? ` · Goodness: ${trial.goodnessScore}` : null}
        {trial.distance != null ? ` · ${trial.distance} ${trial.distanceUnits ?? ""}` : null}
      </div>
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

// Parse the inline CSS string into a CSSProperties object. Avoids
// a Tailwind dependency for these tiny pill styles while keeping the
// per-status colours in one place at the top of the file.
function parseStyle(css: string): CSSProperties {
  const out: Record<string, string> = {};
  for (const rule of css.split(";")) {
    const [k, v] = rule.split(":").map((s) => s.trim());
    if (!k || v == null) continue;
    out[k.replace(/-([a-z])/g, (_m: string, c: string) => c.toUpperCase())] = v;
  }
  return out as CSSProperties;
}
