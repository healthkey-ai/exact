// TypeScript mirrors of the response shapes EXACT's `/trials/` endpoint
// returns. Source of truth: `trials/api/trials_serializers.py`
// (`TrialSerializer`, `TrialDetailsSerializer`) — JSON is camelCased via
// `djangorestframework-camel-case`. These types are kept permissive
// on purpose so a new optional field on the backend doesn't break the
// remote at runtime.

import type { AxiosInstance } from "axios";
import type { QueryClient } from "@tanstack/react-query";

/** Sparse, schema-tolerant patient payload — mirrors EXACT's stateless
 *  `PatientInfo` Python class. Keys are camelCase as sent over the wire.
 *  See `trials/services/patient_info/patient_info.py`. */
export type PatientInfo = Record<string, unknown>;

// Per-trial verdict returned by the matcher. `matchingType` on each trial
// in the `/trials/` response. The matcher emits one of these three; the UI
// groups results by this value.
export type MatchingType = "eligible" | "potential" | "not_eligible";

export interface TrialMatch {
  id: number;
  code: string;
  studyId?: string;
  briefTitle?: string;
  officialTitle?: string;
  register?: string;
  recruitmentStatus?: string;
  phases?: string[];
  matchScore?: number | null;
  goodnessScore?: number | null;
  matchingType?: MatchingType;
  disease?: string | null;
  // Distance to the nearest matching location (km/mi), populated when
  // the caller passes geo/distance prefs. Set to `null` for trials with
  // no matching location.
  distance?: number | null;
  // The full serializer carries many more fields (locations, contacts,
  // etc.). Permissive index lets the UI surface them without a type
  // change when needed.
  [key: string]: unknown;
}

export interface TrialsResponse {
  count: number;
  next?: string | null;
  previous?: string | null;
  results: TrialMatch[];
}

/** Filter prefs the UI surfaces. Keys mirror what
 *  `study_preferences_from_query_params` consumes (see #44 / #63 / #102). */
export interface FilterState {
  recruitmentStatus?: string;
  country?: string;
  region?: string;
  trialType?: string;
  trialPurpose?: string;
  studyType?: string;
  distance?: number;
  distanceUnits?: "km" | "mi";
  validatedOnly?: boolean;
  sponsor?: string;
  register?: string;
}

/** Public props for the federated `./TrialMatches` export. Host-agnostic:
 *  the host wires its own axios instance (with `Authorization: Token …`)
 *  and either a person_id (CTOMOP federation path) or an inline payload
 *  (legacy CB path). */
export interface TrialMatchesProps {
  /** Axios instance pre-configured with `baseURL` (e.g. `/api`) and
   *  `Authorization: Token <…>` header. Required. */
  apiClient: AxiosInstance;
  /** Optional shared TanStack QueryClient — when omitted, the component
   *  spins up its own. Set when the host wants to share the cache. */
  queryClient?: QueryClient;
  /** CTOMOP person_id. Mutually exclusive with `patientInfo` — when
   *  both are provided, `patientInfo` wins (matches the server-side
   *  precedence in `resolve_patient_info`). */
  personId?: string | number;
  /** Inline patient payload. The other half of the resolver contract. */
  patientInfo?: PatientInfo | null;
  /** Initial filter state. The UI lets the user mutate from here. */
  initialFilters?: FilterState;
  /** Called when the user opens a trial card / detail view. */
  onTrialSelect?: (trial: TrialMatch) => void;
}
