// Shared presentational bits used by both the trial card and the trial detail
// page, mirroring CancerBot UI v2's `ScorePill` / `Field`. Structure lives in
// `exact.css` (`.exact-pill*`, `.exact-field`); tier colors are applied inline
// from the `--exact-color-*` token set so a host can re-theme.
import { useId, useState } from "react";

/** CB's Suitability-score explainer article (linked from the pill). */
export const SUITABILITY_HREF =
  "https://medium.com/cancerbot/what-makes-a-trial-good-for-a-patient-a43e5b651754";

const COLLAPSE_THRESHOLD = 80;

export type Tier = "green" | "yellow" | "red" | "neutral";

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

export function ScorePill({
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

export function Field({
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

/** Render inline markdown — `**bold**` only. Returns a string when there are
 *  no markers so callers that pass the result to plain DOM attrs stay safe. */
export function renderMd(text: string): React.ReactNode {
  const parts = text.split(/\*\*(.*?)\*\*/);
  if (parts.length === 1) return text;
  return parts.map((p, i) => (i % 2 === 1 ? <strong key={i}>{p}</strong> : p));
}

/** "?" help icon that shows a tooltip on hover/focus. CSS-only positioning,
 *  no external library. Mirrors CB's Label + Tooltip pattern. */
export function FieldTooltip({ text }: { text: string }) {
  const id = useId();
  return (
    <span className="exact-tooltip__wrap" role="tooltip" aria-describedby={id}>
      <button
        type="button"
        className="exact-tooltip__trigger"
        tabIndex={0}
        aria-label="More information"
      >
        ?
      </button>
      <span id={id} className="exact-tooltip__box" role="tooltip">
        {text}
      </span>
    </span>
  );
}

export const EyeIcon = () => (
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
