// Bucket the trial list by the matcher's per-trial verdict. Extracted from
// `TrialMatches` so it can be unit-tested as a pure function (and so the test
// doesn't have to import the whole component tree / injected CSS).
import type { MatchingType, TrialMatch } from "./types";

export function groupByMatchingType(
  trials: TrialMatch[],
): Record<MatchingType, TrialMatch[]> {
  const buckets: Record<MatchingType, TrialMatch[]> = {
    eligible: [],
    potential: [],
    not_eligible: [],
  };
  for (const t of trials) {
    // `?.` guards against an unexpected matchingType value from a permissive
    // server response — it's dropped rather than throwing.
    buckets[t.matchingType]?.push(t);
  }
  return buckets;
}
