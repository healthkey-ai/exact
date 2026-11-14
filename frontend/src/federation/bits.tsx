// Shared presentational bits used by both the trial card and the trial detail
// page, mirroring CancerBot UI v2's `ScorePill` / `Field`. Structure lives in
// `exact.css` (`.exact-pill*`, `.exact-field`); tier colors are applied inline
// from the `--exact-color-*` token set so a host can re-theme.
import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
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
  // The same box as the actions' (placed, clamped, hoverable), because a "?"
  // near a panel's left edge centred a box that ran off a phone's screen.
  return (
    <ActionTooltip text={text} align="start" className="exact-tooltip__wrap">
      {(tipId) => (
        <button
          type="button"
          className="exact-tooltip__trigger"
          aria-label="More information"
          aria-describedby={tipId}
          // Inside a card, which opens the trial on click.
          onClick={(e) => e.stopPropagation()}
        >
          ?
        </button>
      )}
    </ActionTooltip>
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

/** Lucide's `list`, CB's icon for the list view. */
export const ListIcon = ({ size = 20 }: { size?: number }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.67"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" />
  </svg>
);

/** Lucide's `map-pin`, CB's icon for the map view. */
export const MapPinIcon = ({ size = 20 }: { size?: number }) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="1.67"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <path d="M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10a8 8 0 0 1 16 0" />
    <circle cx="12" cy="10" r="3" />
  </svg>
);

/** The body-level host for action tooltips. Carries `exact-root` so the
 *  scoped stylesheet applies, and sits outside the remote's tree so that a
 *  host ancestor with a `transform` (which re-anchors `position: fixed`) or an
 *  `overflow` (the tab strip) cannot misplace or clip a box. */
let tooltipLayer: HTMLElement | null = null;
function getTooltipLayer(): HTMLElement | null {
  if (typeof document === "undefined") return null;
  if (!tooltipLayer || !tooltipLayer.isConnected) {
    tooltipLayer = document.createElement("div");
    tooltipLayer.className = "exact-root exact-tooltip-layer";
    document.body.appendChild(tooltipLayer);
  }
  return tooltipLayer;
}

const GAP = 6;
const MARGIN = 8;

/** How the last focus most likely arrived, tracked page-wide the way the
 *  `:focus-visible` heuristic does: a key press means keyboard, a pointer
 *  press means pointer. Kept on the document rather than per control, so a
 *  press that brings no focus (a second click on a focused toggle, Safari's
 *  unfocusable buttons, a cancelled touch) cannot leave a stale mark that
 *  swallows the next keyboard focus. */
let lastInput: "keyboard" | "pointer" = "keyboard";
let inputTracking = false;
function trackInputModality() {
  if (inputTracking || typeof document === "undefined") return;
  inputTracking = true;
  document.addEventListener("keydown", () => (lastInput = "keyboard"), true);
  document.addEventListener("pointerdown", () => (lastInput = "pointer"), true);
}
// From module load, so the click that brought the remote in is seen too.
trackInputModality();

/** Keys that scroll a box too tall for the screen while its control keeps
 *  focus — the box cannot take focus itself without closing on blur. */
const BOX_SCROLL_KEYS: Record<string, (box: HTMLElement) => number> = {
  ArrowDown: () => 40,
  ArrowUp: () => -40,
  PageDown: (box) => box.clientHeight - 40,
  PageUp: (box) => -(box.clientHeight - 40),
};

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
  wrapRole,
  children,
}: {
  text?: string;
  align?: "start" | "end";
  /** Extra class on the wrap, for a control whose layout the wrap must keep. */
  className?: string;
  /** `"none"` takes the wrap out of the accessibility tree, for a control
   *  whose parent must OWN it — a `radiogroup` owns its radios, and a plain
   *  `<span>` between the two is not conforming. */
  wrapRole?: "none";
  children: (tipId: string | undefined) => React.ReactNode;
}) {
  const id = useId();
  const wrapRef = useRef<HTMLSpanElement>(null);
  const boxRef = useRef<HTMLSpanElement>(null);
  // Hover and focus are tracked apart: the box shows while either holds, so
  // a focused control keeps its help when the pointer wanders off, and Esc
  // dismisses it until the next hover or focus.
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const open = !!text && !dismissed && (hovered || focused);
  // Leaving waits a moment, so the pointer can cross the gap onto the box and
  // stay there to read it (WCAG 1.4.13: hoverable).
  const leaveTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const enter = () => {
    clearTimeout(leaveTimer.current);
    setHovered(true);
    setDismissed(false);
  };
  const leaveSoon = () => {
    clearTimeout(leaveTimer.current);
    leaveTimer.current = setTimeout(() => setHovered(false), 120);
  };
  useEffect(() => () => clearTimeout(leaveTimer.current), []);
  // Focus that a click gave the control does not hold the box open: after a
  // click the button keeps focus, and the box would hang over the page once
  // the pointer left. Only keyboard focus does.
  // A box whose text goes away takes its hover with it, rather than coming
  // back already open when the text returns.
  useEffect(() => {
    if (!text) setHovered(false);
  }, [text]);

  useLayoutEffect(() => {
    if (!open) return;
    const wrap = wrapRef.current;
    const box = boxRef.current;
    if (!wrap || !box) return;
    // The layer inherits from <body>, not from the host container.
    box.style.fontFamily = getComputedStyle(wrap).fontFamily;
    const place = () => {
      // clientWidth/Height, not innerWidth/100vh: those include a classic
      // scrollbar, and on a phone 100vh is the height with the address bar
      // hidden, taller than what is on screen.
      const vw = document.documentElement.clientWidth;
      const vh = document.documentElement.clientHeight;
      box.style.maxHeight = `${Math.max(0, vh - 2 * MARGIN)}px`;
      const r = wrap.getBoundingClientRect();
      const b = box.getBoundingClientRect();
      // A control scrolled out of the window takes its box with it.
      box.style.visibility = r.bottom < 0 || r.top > vh ? "hidden" : "";
      let left = align === "start" ? r.left : r.right - b.width;
      left = Math.min(Math.max(MARGIN, left), Math.max(MARGIN, vw - b.width - MARGIN));
      let top = r.bottom + GAP;
      if (top + b.height > vh - MARGIN && r.top - GAP - b.height >= MARGIN) {
        top = r.top - GAP - b.height;
      }
      // Fits neither way (a long eligibility note on a phone): keep it on
      // screen; its own max-height and scrolling show the rest.
      top = Math.min(Math.max(MARGIN, top), Math.max(MARGIN, vh - b.height - MARGIN));
      box.style.left = `${Math.round(left)}px`;
      box.style.top = `${Math.round(top)}px`;
    };
    place();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setDismissed(true);
        return;
      }
      // Scroll an overflowing box from its focused button. Not from a
      // select, whose arrow keys change its value.
      const step = BOX_SCROLL_KEYS[e.key];
      const active = document.activeElement;
      if (
        step &&
        box.scrollHeight > box.clientHeight &&
        active instanceof HTMLButtonElement &&
        wrap.contains(active)
      ) {
        e.preventDefault();
        box.scrollTop += step(box);
      }
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
  const layer = getTooltipLayer();
  return (
    <span
      ref={wrapRef}
      className={`exact-action-tip${className ? ` ${className}` : ""}`}
      role={wrapRole}
      onMouseEnter={enter}
      onMouseLeave={leaveSoon}
      // Clicking a control the keyboard had focused leaves focus where it is
      // and fires no focus event, so the keyboard latch has to be dropped
      // here or the box hangs over the page once the pointer leaves.
      onPointerDown={() => setFocused(false)}
      onFocus={() => {
        if (lastInput === "pointer") return;
        setFocused(true);
        setDismissed(false);
      }}
      onBlur={() => setFocused(false)}
    >
      {children(tipId)}
      {text && layer
        ? createPortal(
            <span
              ref={boxRef}
              id={id}
              className={`exact-tooltip__box exact-action-tip__box${open ? " is-open" : ""}`}
              role="tooltip"
              // Inline, not from the stylesheet: the box sits in the host's
              // <body>, and a host that sweeps our <style> out of its <head>
              // would otherwise leave every closed tooltip on its page as
              // stray prose. It stays mounted so `aria-describedby` always
              // resolves.
              style={{ display: open ? "block" : "none" }}
              onMouseEnter={enter}
              onMouseLeave={(e) => {
                // React counts the box as inside the wrap, so moving straight
                // back onto the control fires no enter on the wrap; stay open.
                const to = e.relatedTarget;
                if (to instanceof Node && wrapRef.current?.contains(to)) return;
                leaveSoon();
              }}
              // A click here must not reach a card (portal events bubble
              // through the React tree), nor take focus from the control.
              onClick={(e) => e.stopPropagation()}
              // Costs selecting the text in the box; keeps the control focused.
              onMouseDown={(e) => e.preventDefault()}
            >
              {text}
            </span>,
            layer,
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
