/** The knowledge graph's layout, as plain data.
 *
 *  Computed rather than simulated. CancerBot runs a d3 force simulation, which
 *  is the right call there and the wrong one here: this remote ships six
 *  runtime dependencies and the host inherits every one of them, so a graph
 *  library would be the largest thing in the bundle for a single screen. A
 *  deterministic layout also lands where the reader left it — a force
 *  simulation re-settles on every open, so the picture a patient describes to
 *  someone is not the picture they get back.
 *
 *  Kept out of the component so the geometry can be tested without a DOM.
 */
import type { GraphMatchItem, GraphTrialNode } from "./types";

/** Which of the three answers this attribute got for a given trial. */
export type ConceptStatus = "matched" | "missing" | "notMatched";

export interface ConceptNode {
  /** The TRIAL's field, not the patient's. Both are stable across trials, so
   *  either would share a node between them — but several trial fields map to
   *  one patient field (`age_low_limit` and `age_high_limit` are both
   *  `patient_age`, and four `mutation_*_required` are all
   *  `genetic_mutations`), so keying on the patient's collapses two different
   *  requirements into one node that keeps the first label. "Age Low Limit"
   *  would then appear in the "Not met" band when it is the upper bound that
   *  excludes the patient. */
  id: string;
  label: string;
  /** The worst answer across the trials on screen: a contradicted requirement
   *  outranks a missing one, which outranks a met one. Banding on the best
   *  answer would put an attribute that rules the patient out of one trial in
   *  with the ones they meet. */
  status: ConceptStatus;
  /** Trial ids that ask about this attribute, in the order they are drawn. */
  trialIds: number[];
  x: number;
  y: number;
}

export interface TrialPoint {
  trial: GraphTrialNode;
  x: number;
  y: number;
  /** 22–32px by score, so a better match is a bigger target as well as a
   *  higher one. */
  r: number;
}

export interface GraphLayout {
  width: number;
  height: number;
  patient: { x: number; y: number; r: number };
  trials: TrialPoint[];
  concepts: ConceptNode[];
  /** Concept id → trial ids, the edges to draw. */
  edges: { conceptId: string; trialId: number }[];
  /** How many concepts the trials actually name, before any limit. */
  totalConcepts: number;
  /** True when `totalConcepts` exceeded the limit and the least-shared were
   *  dropped. The count is shown either way; this is what makes the drop
   *  visible rather than a quietly smaller picture. */
  truncated: boolean;
  /** Whether any surviving attribute is asked about by more than one trial.
   *  When nothing is shared the cut is decided by the alphabetical tie-break,
   *  so calling it "the ones the most trials ask about" would be a
   *  description of a sort that did not happen. */
  shared: boolean;
}

/** Worst-first, which is also the order the bands run down the page. */
const SEVERITY: ConceptStatus[] = ["notMatched", "missing", "matched"];

const BAND_LABEL: Record<ConceptStatus, string> = {
  matched: "Met",
  missing: "Not known",
  notMatched: "Not met",
};

export const BAND_ORDER: ConceptStatus[] = ["matched", "missing", "notMatched"];
export const bandLabel = (status: ConceptStatus): string => BAND_LABEL[status];

/** How many attributes are drawn before the least-shared are dropped. Beyond
 *  this the picture stops being one a person can read, which is the only thing
 *  it is for. */
export const MAX_CONCEPTS = 50;

const scoreOf = (trial: GraphTrialNode): number =>
  trial.matchScore ?? trial.goodnessScore ?? 0;

function itemsFor(trial: GraphTrialNode): [ConceptStatus, GraphMatchItem][] {
  const out: [ConceptStatus, GraphMatchItem][] = [];
  for (const item of trial.match?.matched ?? []) out.push(["matched", item]);
  for (const item of trial.match?.missing ?? []) out.push(["missing", item]);
  for (const item of trial.match?.notMatched ?? []) out.push(["notMatched", item]);
  return out;
}

export function buildGraphLayout(
  trials: GraphTrialNode[],
  options: { width?: number; maxConcepts?: number } = {},
): GraphLayout {
  // Wide enough for a trial's title to sit to the right of its node and still
  // be inside the viewBox: `overflow-x: auto` scrolls the SVG, it cannot
  // scroll to content the viewBox excludes.
  const width = options.width ?? 1180;
  const maxConcepts = options.maxConcepts ?? MAX_CONCEPTS;

  const ordered = [...trials].sort((a, b) => scoreOf(b) - scoreOf(a));

  // Collect the attributes first: how many survive decides the height, and the
  // height decides where everything sits.
  const byId = new Map<string, ConceptNode>();
  for (const trial of ordered) {
    for (const [status, item] of itemsFor(trial)) {
      const id = String(item.trialField || item.patientField || item.label || "");
      if (!id) continue;
      const existing = byId.get(id);
      if (!existing) {
        byId.set(id, {
          id,
          label: String(item.label || id),
          status,
          trialIds: [trial.trialId],
          x: 0,
          y: 0,
        });
        continue;
      }
      if (!existing.trialIds.includes(trial.trialId)) existing.trialIds.push(trial.trialId);
      if (SEVERITY.indexOf(status) < SEVERITY.indexOf(existing.status)) {
        existing.status = status;
      }
    }
  }

  const totalConcepts = byId.size;
  let concepts = [...byId.values()];
  const truncated = concepts.length > maxConcepts;
  if (truncated) {
    // By how many trials ask about it: an attribute three trials want tells
    // the reader more than one that only a single trial mentions.
    concepts = [...concepts]
      .sort((a, b) => b.trialIds.length - a.trialIds.length || a.label.localeCompare(b.label))
      .slice(0, maxConcepts);
  }

  const bands = BAND_ORDER.map((status) => ({
    status,
    items: concepts
      .filter((c) => c.status === status)
      .sort((a, b) => a.label.localeCompare(b.label)),
  }));

  const ROW = 26;
  const BAND_GAP = 34;
  const TOP = 48;
  const conceptsHeight = bands.reduce(
    (sum, band) => sum + (band.items.length ? band.items.length * ROW + BAND_GAP : 0),
    0,
  );
  const trialsHeight = ordered.length * 78;
  const height = Math.max(320, TOP + Math.max(conceptsHeight, trialsHeight) + 32);

  const conceptX = width * 0.34;
  let y = TOP;
  for (const band of bands) {
    if (!band.items.length) continue;
    y += BAND_GAP;
    for (const concept of band.items) {
      concept.x = conceptX;
      concept.y = y;
      y += ROW;
    }
  }

  const trialX = width * 0.62;
  const trialStep = ordered.length > 1 ? (height - 2 * TOP) / (ordered.length - 1) : 0;
  const trialPoints: TrialPoint[] = ordered.map((trial, i) => ({
    trial,
    x: trialX,
    y: ordered.length > 1 ? TOP + i * trialStep : height / 2,
    r: Math.max(22, Math.min(32, 22 + (scoreOf(trial) / 100) * 10)),
  }));

  // Built from the surviving concepts, so a dropped one cannot be pointed at
  // — by construction rather than by a filter afterwards.
  const edges: { conceptId: string; trialId: number }[] = [];
  for (const concept of concepts) {
    for (const trialId of concept.trialIds) {
      if (ordered.some((t) => t.trialId === trialId)) {
        edges.push({ conceptId: concept.id, trialId });
      }
    }
  }

  return {
    shared: concepts.some((c) => c.trialIds.length > 1),
    width,
    height,
    patient: { x: width * 0.08, y: height / 2, r: 32 },
    trials: trialPoints,
    concepts,
    edges,
    totalConcepts,
    truncated,
  };
}
