// Shared presentational bits used by both the trial card and the trial detail
// page, mirroring CancerBot UI v2's `ScorePill` / `Field`. Structure lives in
// `exact.css` (`.exact-pill*`, `.exact-field`); tier colors are applied inline
// from the `--exact-color-*` token set so a host can re-theme.
import { useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { ACTION_TOOLTIPS } from "./tooltips";

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
  tooltip,
}: {
  score: number | null | undefined;
  label: string;
  href?: string;
  /** CB's "?" beside the label. Outside the link, since a button inside an
   *  anchor is not a thing a browser will let you click on its own. */
  tooltip?: string;
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

  const body = href ? (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      style={{ textDecoration: "none" }}
      onClick={(e) => e.stopPropagation()}
    >
      {pill}
    </a>
  ) : (
    pill
  );
  if (!tooltip) return body;
  return (
    <span className="exact-pill__wrap">
      {body}
      <FieldTooltip text={tooltip} />
    </span>
  );
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
    <span className="exact-tooltip__wrap">
      <button
        type="button"
        className="exact-tooltip__trigger"
        tabIndex={0}
        aria-label="More information"
        aria-describedby={id}
        // Inside a card, which opens the trial on click.
        onClick={(e) => e.stopPropagation()}
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

/** Lucide's `bookmark`, the icon CB draws for favorites; filled when on. */
export const BookmarkIcon = ({ filled, size = 20 }: { filled: boolean; size?: number }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill={filled ? "currentColor" : "none"}
    stroke="currentColor"
    strokeWidth="1.67"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z" />
  </svg>
);

/** The body-level host for action tooltips. Carries `exact-root` so the
 *  scoped stylesheet applies, and sits outside the remote's tree so that a
 *  host ancestor with a `transform` (which re-anchors `position: fixed`) or an
 *  `overflow` (the tab strip) cannot misplace or clip a box. */
let tooltipLayer: HTMLElement | null = null;
function getTooltipLayer(): HTMLElement {
  if (!tooltipLayer || !tooltipLayer.isConnected) {
    tooltipLayer = document.createElement("div");
    tooltipLayer.className = "exact-root exact-tooltip-layer";
    document.body.appendChild(tooltipLayer);
  }
  return tooltipLayer;
}

const GAP = 6;
const MARGIN = 8;

/** Hover/focus help for an action (a button, a tab, the sort control), as CB
 *  wraps its trial actions in a tooltip.
 *
 *  The wrapped control names the box with `aria-describedby` — `tipId` is
 *  handed to it for that — so the text is read out whether or not the box is
 *  showing. The box lives in a body-level layer (see `getTooltipLayer`) and is
 *  placed from the control's rect after it is measured: below the control,
 *  above it when there is no room below, lined up with the control's `align`
 *  edge and then clamped inside the viewport on both sides. It follows the
 *  control while open (scrolling — including the scroll a browser makes to
 *  bring a focused control into view — and resizing), and Esc dismisses it.
 *
 *  `text` may be absent; the wrap is still rendered, so a control whose
 *  tooltip comes and goes (the sort) is not re-mounted and keeps focus. */
export function ActionTooltip({
  text,
  align = "end",
  className,
  children,
}: {
  text?: string;
  align?: "start" | "end";
  /** Extra class on the wrap, for a control whose layout the wrap must keep. */
  className?: string;
  children: (tipId: string | undefined) => React.ReactNode;
}) {
  const id = useId();
  const wrapRef = useRef<HTMLSpanElement>(null);
  const boxRef = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);

  useLayoutEffect(() => {
    if (!open || !text) return;
    const place = () => {
      const wrap = wrapRef.current;
      const box = boxRef.current;
      if (!wrap || !box) return;
      const r = wrap.getBoundingClientRect();
      const b = box.getBoundingClientRect();
      // clientWidth, not innerWidth: the latter includes a classic scrollbar.
      const vw = document.documentElement.clientWidth;
      const vh = document.documentElement.clientHeight;
      let left = align === "start" ? r.left : r.right - b.width;
      left = Math.min(Math.max(MARGIN, left), Math.max(MARGIN, vw - b.width - MARGIN));
      let top = r.bottom + GAP;
      if (top + b.height > vh - MARGIN && r.top - GAP - b.height >= MARGIN) {
        top = r.top - GAP - b.height;
      }
      box.style.left = `${Math.round(left)}px`;
      box.style.top = `${Math.round(top)}px`;
    };
    place();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    window.addEventListener("scroll", place, true);
    window.addEventListener("resize", place);
    return () => {
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
    };
  }, [open, text, align]);

  const tipId = text ? id : undefined;
  return (
    <span
      ref={wrapRef}
      className={`exact-action-tip${className ? ` ${className}` : ""}`}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      {children(tipId)}
      {text
        ? createPortal(
            <span
              ref={boxRef}
              id={id}
              className={`exact-tooltip__box exact-action-tip__box${open ? " is-open" : ""}`}
              role="tooltip"
            >
              {text}
            </span>,
            getTooltipLayer(),
          )
        : null}
    </span>
  );
}

/** The bookmark toggle, drawn identically on a card and on the detail page.
 *
 *  One component rather than the same JSX twice, because the interesting
 *  part is not the star but the rule about when it may be drawn at all:
 *  only with somewhere to write (`onToggle`) AND a known answer
 *  (`isFavorite !== undefined`). An unknown answer renders nothing rather
 *  than an empty star that would fill in under the reader's eye a moment
 *  later — and "unknown" covers the read having failed, not just being slow.
 *
 *  Written twice, the two copies drift: the card had the rule and the detail
 *  page, added later, would have had a star that flickered.
 */
export function FavoriteToggle({
  title,
  isFavorite,
  onToggle,
  busy,
  boxed,
}: {
  /** The trial's title, for the accessible name — "Add <title> to
   *  favorites" reads usefully in a list of several. */
  title: string;
  isFavorite?: boolean;
  onToggle?: (next: boolean) => void;
  /** A write for this trial is on the wire — announced, not enforced.
   *
   *  Dropping the second click is the caller's job and happens in exactly
   *  one place (`TrialMatches`'s `write`), because every path to a write
   *  goes through it and a control that enforced it too would be the same
   *  rule written twice: a mutation test showed each copy keeping the other
   *  one's test green.
   *
   *  `aria-disabled` rather than `disabled` so the control is not blurred
   *  out from under a keyboard user mid-action. */
  busy?: boolean;
  /** Drawn as a bordered button, like CB's bookmark in the detail header. */
  boxed?: boolean;
}) {
  if (!onToggle || isFavorite === undefined) return null;
  return (
    <ActionTooltip text={isFavorite ? ACTION_TOOLTIPS.favoriteOn : ACTION_TOOLTIPS.favorite}>
      {(tipId) => (
        <button
          type="button"
          className={`exact-fav${boxed ? " exact-fav--boxed" : ""}${isFavorite ? " is-on" : ""}`}
          aria-describedby={tipId}
          aria-pressed={isFavorite}
          aria-disabled={busy || undefined}
          aria-busy={busy || undefined}
          aria-label={
            isFavorite ? `Remove ${title} from favorites` : `Add ${title} to favorites`
          }
          onClick={(e) => {
            // The card is itself a click target; without this, bookmarking would
            // also open the trial. Harmless where nothing is listening.
            e.stopPropagation();
            onToggle(!isFavorite);
          }}
        >
          <BookmarkIcon filled={isFavorite} size={boxed ? 20 : 18} />
        </button>
      )}
    </ActionTooltip>
  );
}

/** The tick the eligibility rows and the high-risk MCL panel share — two ticks
 *  that differ read as two meanings. Here rather than in `TrialDetailPage`
 *  because the panel imports it and that page imports the panel: a module
 *  cycle that survives today only because nothing dereferences it at
 *  module-evaluation time. */
export const CheckIcon = () => (
  <svg
    width="16"
    height="16"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M21.801 10A10 10 0 1 1 17 3.335" />
    <path d="m9 11 3 3L22 4" />
  </svg>
);
