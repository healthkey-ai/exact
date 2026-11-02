/** "Explore Trials" — the patient, the trials, and the attributes between them.
 *
 *  The list answers "which trials" one card at a time. This answers the
 *  question a card cannot: which requirement is standing between this patient
 *  and several trials at once, and which one requirement would unlock more
 *  than one of them.
 *
 *  Drawn from a computed layout rather than a force simulation — see
 *  `graphLayout.ts` for why.
 */
import { useMemo, useState } from "react";

import {
  BAND_ORDER,
  bandLabel,
  buildGraphLayout,
  type ConceptStatus,
} from "./graphLayout";
import type { GraphMatchItem, GraphTrialNode } from "./types";

const STATUS_COLOR: Record<ConceptStatus, string> = {
  matched: "var(--exact-color-eligible)",
  missing: "var(--exact-color-potential)",
  notMatched: "var(--exact-color-not-eligible)",
};

/** How many trials are drawn at once, and listed beneath. Drawing fifty
 *  produces a picture nobody can read, which is the only thing this view is
 *  for; "Show all" lifts the limit for a reader who wants the rest. */
const DRAWN_TRIALS = 6;

function scoreOf(trial: GraphTrialNode): number {
  return trial.matchScore ?? trial.goodnessScore ?? 0;
}

const labelOf = (item: GraphMatchItem): string =>
  String(item.label || item.trialField || item.patientField || "");

/** One trial's own answer for a band — `match` buckets are per trial, while
 *  the graph's banding is across all of them. */
function bucketFor(trial: GraphTrialNode, status: ConceptStatus): GraphMatchItem[] {
  const buckets = trial.match ?? { matched: [], missing: [], notMatched: [] };
  if (status === "matched") return buckets.matched ?? [];
  if (status === "missing") return buckets.missing ?? [];
  return buckets.notMatched ?? [];
}

export function TrialsGraph({
  trials,
  onSelectTrial,
  onClose,
}: {
  trials: GraphTrialNode[];
  onSelectTrial: (trialId: number) => void;
  onClose: () => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const ranked = useMemo(
    () => [...trials].sort((a, b) => scoreOf(b) - scoreOf(a)),
    [trials],
  );
  const drawn = showAll ? ranked : ranked.slice(0, DRAWN_TRIALS);
  const layout = useMemo(() => buildGraphLayout(drawn), [drawn]);
  const trialById = useMemo(
    () => new Map(layout.trials.map((t) => [t.trial.trialId, t])),
    [layout],
  );

  if (!trials.length) {
    return (
      <section className="exact-graph" aria-label="Explore trials">
        <header className="exact-graph__head">
          <h2 className="exact-panel__title">Explore Trials</h2>
          <button type="button" className="exact-graph__close" onClick={onClose}>
            Close
          </button>
        </header>
        <p className="exact-graph__empty">
          No trials matched this search, so there is nothing to explore yet. Widen
          the filters and try again.
        </p>
      </section>
    );
  }

  return (
    <section className="exact-graph" aria-label="Explore trials">
      <header className="exact-graph__head">
        <div>
          <h2 className="exact-panel__title">Explore Trials</h2>
          <p className="exact-graph__sub">
            Which requirements you meet, which are missing, and which trials share
            them.
          </p>
        </div>
        <button type="button" className="exact-graph__close" onClick={onClose}>
          Close
        </button>
      </header>

      <p className="exact-graph__stats">
        Showing {drawn.length} of {ranked.length} trials
        {" · "}
        {layout.concepts.length} of {layout.totalConcepts} requirements
        {layout.truncated ? (
          <span className="exact-graph__truncated">
            {" "}
            {layout.shared
              ? `— the ${layout.concepts.length} shared by the most trials`
              : `— the first ${layout.concepts.length}, since no two trials share one`}
          </span>
        ) : null}
        {ranked.length > DRAWN_TRIALS ? (
          <>
            {" · "}
            <button
              type="button"
              className="exact-graph__more"
              onClick={() => setShowAll((v) => !v)}
            >
              {showAll ? `Show top ${DRAWN_TRIALS}` : `Show all ${ranked.length}`}
            </button>
          </>
        ) : null}
      </p>

      <div className="exact-graph__scroll">
        {/* Hidden from assistive technology, and carrying no tab stops. An
            `<svg role="img">` makes its subtree presentational, so the
            clickable trial groups inside it were nameless tab stops in Chrome
            and unreachable in WebKit — two half-working stories instead of
            one. The list below is the accessible copy, with real buttons, and
            it is not a fallback: it is where the labels are readable. */}
        <svg
          className="exact-graph__svg"
          viewBox={`0 0 ${layout.width} ${layout.height}`}
          width={layout.width}
          height={layout.height}
          aria-hidden="true"
          focusable="false"
        >
          {/* Edges first, so nothing is drawn over a node. */}
          <g className="exact-graph__edges">
            {layout.edges.map((edge) => {
              const concept = layout.concepts.find((c) => c.id === edge.conceptId);
              const trial = trialById.get(edge.trialId);
              if (!concept || !trial) return null;
              return (
                <line
                  key={`${edge.conceptId}-${edge.trialId}`}
                  x1={concept.x}
                  y1={concept.y}
                  x2={trial.x - trial.r}
                  y2={trial.y}
                  stroke={STATUS_COLOR[concept.status]}
                  strokeOpacity={0.28}
                  strokeWidth={1}
                />
              );
            })}
            {layout.trials.map((t) => (
              <line
                key={`patient-${t.trial.trialId}`}
                x1={layout.patient.x + layout.patient.r}
                y1={layout.patient.y}
                x2={t.x - t.r}
                y2={t.y}
                stroke="var(--exact-color-primary)"
                strokeOpacity={0.45}
                strokeWidth={Math.max(1, (scoreOf(t.trial) / 100) * 3)}
              />
            ))}
          </g>

          <g>
            <circle
              cx={layout.patient.x}
              cy={layout.patient.y}
              r={layout.patient.r}
              fill="var(--exact-color-primary)"
            />
            <text
              x={layout.patient.x}
              y={layout.patient.y + 4}
              textAnchor="middle"
              fill="var(--exact-color-on-brand)"
              fontSize="13"
              fontWeight="600"
            >
              You
            </text>
          </g>

          {BAND_ORDER.map((status) => {
            const items = layout.concepts.filter((c) => c.status === status);
            if (!items.length) return null;
            const top = Math.min(...items.map((c) => c.y));
            return (
              <text
                key={status}
                x={items[0].x - 8}
                y={top - 14}
                textAnchor="end"
                fontSize="11"
                fontWeight="600"
                fill={STATUS_COLOR[status]}
              >
                {bandLabel(status)}
              </text>
            );
          })}

          <g>
            {layout.concepts.map((concept) => (
              <g key={concept.id}>
                <circle
                  cx={concept.x}
                  cy={concept.y}
                  r={5}
                  fill={STATUS_COLOR[concept.status]}
                />
                <text
                  x={concept.x - 12}
                  y={concept.y + 4}
                  textAnchor="end"
                  fontSize="11"
                  fill="var(--exact-color-text-muted)"
                >
                  {concept.label}
                </text>
              </g>
            ))}
          </g>

          <g>
            {layout.trials.map((t) => (
              <g
                key={t.trial.trialId}
                className="exact-graph__trial"
                onClick={() => onSelectTrial(t.trial.trialId)}
              >
                <circle cx={t.x} cy={t.y} r={t.r} fill="var(--exact-color-surface-2)" />
                <text
                  x={t.x}
                  y={t.y + 4}
                  textAnchor="middle"
                  fontSize="12"
                  fontWeight="600"
                  fill="var(--exact-color-text)"
                >
                  {scoreOf(t.trial)}%
                </text>
                <text
                  x={t.x + t.r + 8}
                  y={t.y + 4}
                  fontSize="11"
                  fill="var(--exact-color-text-muted)"
                >
                  {(t.trial.briefTitle || t.trial.studyId || "").slice(0, 28)}
                </text>
              </g>
            ))}
          </g>
        </svg>
      </div>

      {/* The same content, as text. The picture is the quick read; this is the
          one that works with a screen reader, at any width, and when a
          requirement's label is longer than the space beside its dot. */}
      <ul className="exact-graph__legend">
        {BAND_ORDER.map((status) => (
          <li key={status}>
            <span
              className="exact-graph__swatch"
              style={{ background: STATUS_COLOR[status] }}
              aria-hidden="true"
            />
            {bandLabel(status)}
          </li>
        ))}
      </ul>

      <ol className="exact-graph__list">
        {layout.trials.map((t) => (
          <li key={t.trial.trialId}>
            <button type="button" onClick={() => onSelectTrial(t.trial.trialId)}>
              {t.trial.briefTitle || t.trial.studyId}
            </button>{" "}
            <span className="exact-graph__score">{scoreOf(t.trial)}%</span>
            <ul>
              {BAND_ORDER.map((status) => {
                // THIS trial's buckets, not the banding. A node is banded by
                // its worst answer across every trial on screen, which is
                // right for the picture and wrong for a line of text under one
                // trial: it would print "Not met: ECOG" beneath a trial whose
                // ECOG the patient does meet.
                const items = bucketFor(t.trial, status);
                if (!items.length) return null;
                return (
                  <li key={status}>
                    {bandLabel(status)}: {items.map(labelOf).join(", ")}
                  </li>
                );
              })}
            </ul>
          </li>
        ))}
      </ol>
    </section>
  );
}
