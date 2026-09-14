// Dev preview — renders the real federated `TrialMatches` / `TrialDetailPage`
// against a fake axios client returning canned data, so the card, list, and
// detail page can be viewed in a browser without a backend.
//
//   npm run dev  ->  http://localhost:5173/mock-preview.html          (list)
//                    http://localhost:5173/mock-preview.html?trial=1  (detail)
//
// Not part of any production build: the remote build (`vite.remote.config.ts`)
// only bundles the `exposes` graph, and the SPA build uses `index.html` /
// `main.tsx` — neither imports this file or `mock-preview.html`.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TrialMatches } from "../federation/TrialMatches";
import { TrialDetailPage } from "../federation/TrialDetailPage";
import type { TrialMatch, TrialsResponse } from "../federation/types";

const results = [
  {
    trialId: 1,
    studyId: "NCT04012345",
    briefTitle: "A Study of Daratumumab in Newly Diagnosed Multiple Myeloma",
    recruitingStatus: "Recruiting",
    phase: ["Phase 2", "Phase 3"],
    trialType: "Interventional",
    location: [
      "Memorial Sloan Kettering Cancer Center, New York",
      "Dana-Farber Cancer Institute, Boston",
      "MD Anderson Cancer Center, Houston",
    ],
    interventionTreatments: ["Daratumumab", "Lenalidomide", "Dexamethasone"],
    matchScore: 92,
    goodnessScore: 88,
    matchingType: "eligible",
    distance: 12,
    distanceUnits: "km",
    // Real coordinates, so the backend-free QA surface can show the map. Two
    // of these three sit at the same hospital on purpose: grouping is the
    // part worth looking at by eye.
    closestLocationGeoPoint: { latitude: 40.7644, longitude: -73.9566 },
    link: "https://clinicaltrials.gov/study/NCT04012345",
  },
  {
    trialId: 2,
    studyId: "NCT04567890",
    briefTitle: "Carfilzomib Maintenance After Transplant",
    recruitingStatus: "Recruiting",
    phase: ["Phase 2"],
    trialType: "Interventional",
    location: ["Mayo Clinic, Rochester"],
    interventionTreatments: ["Carfilzomib"],
    matchScore: 84,
    goodnessScore: 65,
    matchingType: "eligible",
    distance: 210,
    distanceUnits: "km",
    closestLocationGeoPoint: { latitude: 40.7644, longitude: -73.9566 },
    link: "https://clinicaltrials.gov/study/NCT04567890",
  },
  {
    trialId: 3,
    studyId: "NCT05098765",
    briefTitle: "Venetoclax Combination Therapy in Relapsed/Refractory Disease",
    recruitingStatus: "Recruiting",
    phase: ["Phase 1"],
    trialType: "Interventional",
    location: ["UCSF Helen Diller Family Comprehensive Cancer Center, San Francisco"],
    interventionTreatments: ["Venetoclax", "Obinutuzumab"],
    matchScore: 71,
    goodnessScore: 54,
    matchingType: "potential",
    closestLocationGeoPoint: { latitude: 37.7631, longitude: -122.4586 },
    distance: 4100,
    distanceUnits: "km",
    link: "https://clinicaltrials.gov/study/NCT05098765",
  },
  {
    trialId: 4,
    studyId: "NCT05223344",
    briefTitle: "Bispecific Antibody Dose-Escalation Study",
    recruitingStatus: "Not yet recruiting",
    phase: ["Phase 1"],
    trialType: "Interventional",
    location: ["Johns Hopkins, Baltimore"],
    interventionTreatments: ["Teclistamab"],
    matchScore: null,
    goodnessScore: 47,
    matchingType: "potential",
    distance: null,
    distanceUnits: null,
    link: "https://clinicaltrials.gov/study/NCT05223344",
  },
] as unknown as TrialMatch[];

const trials: TrialsResponse = {
  // `count` is the number of PAGES, which the pager reads directly.
  count: 1,
  itemsTotalCount: results.length,
  next: null,
  previous: null,
  results,
  // Without this the tab badges render empty — absent counts mean "the
  // server could not judge", which is the honest reading for a live server
  // but just makes the harness look broken. Split by the fixtures' own
  // `matchingType` so the numbers match the cards on screen.
  tabCounts: {
    eligible: results.filter((t) => t.matchingType === "eligible").length,
    // Not `!== "eligible"`: a row from a patient-less search carries
    // `matchingType: null`, which that spelling counts as potential (#456).
    potential: results.filter((t) => t.matchingType === "potential").length,
  },
};

const formSettings = {
  trialType: {
    options: [
      { value: "interventional", label: "Interventional" },
      { value: "observational", label: "Observational" },
    ],
  },
  // Names for the high-risk MCL panel. Without them the backend-free QA
  // surface cannot show the feature at all — and that page is the only place
  // the marks, the colours and the 640px layout get looked at by eye.
  highRiskMclCriteria: {
    options: [
      { value: "tp53_mutation", label: "TP53 mutation" },
      { value: "del17p", label: "del(17p)" },
      { value: "ki67_30", label: "Ki-67 >= 30%" },
      { value: "blastoid", label: "Blastoid morphology" },
    ],
  },
};

// Canned trial-detail payload (mirrors `TrialDetailsSerializer`): header meta,
// summary, and the Required/Your-Value eligibility table with mixed verdicts.
function detailFor(id: string) {
  const t = results.find((r) => String(r.trialId) === String(id)) ?? results[0];
  return {
    trialId: t.trialId,
    studyId: t.studyId,
    briefTitle: t.briefTitle,
    officialTitle: t.briefTitle,
    locationsName: t.location,
    interventionTreatments: t.interventionTreatments,
    sponsorName: "Massive Bio, Inc.",
    link: t.link,
    recruitmentStatus: t.recruitingStatus,
    phases: t.phase,
    trialType: t.trialType,
    laySummary:
      "This study is an international registry for adult and pediatric patients " +
      "with advanced solid or blood cancers. The main goal is to see how well an " +
      "AI tool can help match patients to suitable clinical trials and what impact " +
      "this has on their treatment and outcomes.",
    matchScore: t.matchScore,
    goodnessScore: t.goodnessScore,
    // One of each tone, so the panel can be checked by eye: a criterion met, a
    // confirmed absence, a gap in the data, an alternative that costs nothing,
    // and an exclusion the patient is clear of.
    highRiskMclCriteriaBreakdown: {
      aggregate: "matched",
      minCount: 1,
      matchedCount: 1,
      required: [
        { code: "tp53_mutation", status: "matched" },
        { code: "del17p", status: "unknown" },
      ],
      sufficientAny: [{ code: "ki67_30", status: "not_matched" }],
      excluded: [{ code: "blastoid", status: "matched" }],
    },
    groupNames: [
      { value: "trialEligibilityAttributes", label: "Trial Eligibility Attributes" },
    ],
    details: {
      general: [],
      trialEligibilityAttributes: [
        // The four answers the editing work has to tell apart, in order:
        // writable, computed-by-EXACT (no control, but a subform), refused by
        // PROMOP with a reason, and writable somewhere else.
        {
          name: "hemoglobinMin",
          label: "Hemoglobin",
          type: "number",
          value: 10,
          units: "g/dL",
          ufield: "hemoglobinLevel",
          upatientField: "hemoglobin_g_dl",
          upatientRecomputed: false,
          uvalue: 11.2,
          uunits: "g/dL",
          matchingType: "matched",
        },
        {
          name: "tnbcStatus",
          label: "TNBC Status",
          type: "boolean",
          value: true,
          ureadonly: true,
          ufield: "tnbcStatus",
          upatientField: "tnbc_status",
          // EXACT derives it, so no pencil — the subform is the way in.
          upatientRecomputed: true,
          uvalue: false,
          matchingType: "not_matched",
          subform_details: [
            {
              name: "estrogenReceptorStatus",
              label: "Estrogen receptor status",
              type: "select",
              value: "ER-",
              options: [
                { value: "ER-", label: "ER-" },
                { value: "ER+", label: "ER+" },
              ],
              upatientField: "estrogen_receptor_status",
              upatientRecomputed: false,
            },
            {
              name: "progesteroneReceptorStatus",
              label: "Progesterone receptor status",
              type: "select",
              value: "PR-",
              options: [
                { value: "PR-", label: "PR-" },
                { value: "PR+", label: "PR+" },
              ],
              upatientField: "progesterone_receptor_status",
              upatientRecomputed: false,
            },
            {
              name: "her2Status",
              label: "HER2 status",
              type: "select",
              value: "HER2-",
              options: [
                { value: "HER2-", label: "HER2-" },
                { value: "HER2+", label: "HER2+" },
                { value: "HER2 low", label: "HER2 low" },
              ],
              upatientField: "her2_status",
              upatientRecomputed: false,
            },
          ],
        },
        {
          name: "bmiMin",
          label: "BMI",
          type: "number",
          value: 18,
          ufield: "bmi",
          upatientField: "bmi",
          upatientRecomputed: true,
          uvalue: 24,
          matchingType: "matched",
        },
        {
          name: "mutationGenesRequired",
          label: "Mutation Genes (writable elsewhere)",
          type: "multiselect",
          value: ["BRCA1"],
          ufield: "geneticMutations",
          upatientField: "genetic_mutations",
          upatientRecomputed: false,
          uvalue: ["BRCA1"],
          matchingType: "not_matched",
        },
        {
          name: "mutationGenes",
          label: "Mutation Genes",
          type: "multiselect",
          value: ["BRCA1", "BRCA2", "ESR1", "PIK3CA", "TP53"],
          uvalue: null,
          matchingType: "unknown",
        },
        {
          name: "disease",
          label: "Disease",
          type: "select",
          value: "Breast Cancer",
          uvalue: "Breast Cancer",
          matchingType: "matched",
        },
        {
          name: "ecogMax",
          label: "ECOG Performance Status Maximum",
          type: "int",
          value: 2,
          uvalue: 2,
          matchingType: "matched",
        },
        {
          name: "ageMin",
          label: "Minimum Age",
          type: "int",
          value: 18,
          uvalue: 64,
          matchingType: "matched",
          units: "years",
        },
        {
          name: "priorLines",
          label: "Prior Lines of Therapy (max)",
          type: "int",
          value: 2,
          uvalue: 3,
          matchingType: "not_matched",
        },
      ],
    },
  };
}

// Minimal axios stand-in: route by URL, ignore params/body.
//
// The list routes are matched BEFORE the `:id` ones. `/trials/search/` and
// `/trials/search/match/` are otherwise captured by the detail patterns —
// `[^/]+` happily matches the literal "search" — and the list would be
// served a single trial-detail object, leaving the harness showing "No
// trials found" with no clue why.
const LIST_PATHS = ["/trials/search/", "/trials/search/match/"];

const apiClient = {
  get: async (url: string) => {
    if (url.includes("form-settings")) return { data: formSettings };
    if (LIST_PATHS.includes(url)) return { data: trials };
    const detail = url.match(/^\/trials\/([^/]+)\/$/);
    if (detail) return { data: detailFor(detail[1]) };
    return { data: trials };
  },
  post: async (url: string) => {
    if (LIST_PATHS.includes(url)) return { data: trials };
    const detail = url.match(/^\/trials\/([^/]+)\/match\/$/);
    if (detail) return { data: detailFor(detail[1]) };
    return { data: trials };
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
} as any;

const patientInfo = { disease: "MM", country: "US" };

// An adapter in memory, so the whole write path can be tried without PROMOP.
//
// The descriptor is the part worth faking carefully: it is what decides which
// rows get a control, and half the behaviour of phases 2–4 is refusals. So it
// carries all four answers a real one gives — writable, writable-elsewhere,
// computed, and a field it has never heard of — rather than saying yes to
// everything and hiding exactly what there is to look at.
const RECORD: Record<string, unknown> = {
  hemoglobin_g_dl: 11.2,
  estrogen_receptor_status: "ER-",
  progesterone_receptor_status: "PR-",
  her2_status: "HER2-",
};

const WRITABLE_FIELDS = {
  hemoglobin_g_dl: {
    kind: "direct", writable: true, value_kind: "number", unit: "g/dL",
  },
  estrogen_receptor_status: {
    kind: "direct", writable: true, value_kind: "string",
    options: [{ value: "ER-" }, { value: "ER+" }],
  },
  progesterone_receptor_status: {
    kind: "direct", writable: true, value_kind: "string",
    options: [{ value: "PR-" }, { value: "PR+" }],
  },
  her2_status: {
    kind: "direct", writable: true, value_kind: "string",
    options: [{ value: "HER2-" }, { value: "HER2+" }, { value: "HER2 low" }],
  },
  // Writable, but not here — the row should refuse and say where.
  genetic_mutations: {
    kind: "editable", writable: true, target: "genomics",
    reason: "Edit individual variants in the Genomics tab.",
  },
  // PROMOP's own refusal, with its own reason.
  bmi: {
    kind: "computed", writable: false,
    reason: "Derived from height and weight; edit those instead.",
  },
};

/** Set `?slow=1` to watch the optimistic value and "Saving…" for a second,
 *  and `?fail=1` to see what a refused write leaves behind. */
const params = new URLSearchParams(window.location.search);
const SLOW = params.has("slow");
const FAIL = params.has("fail");

const mockState = {
  listFavoriteIds: async () => [],
  setFavorite: async () => undefined,
  listRegisteredIds: async () => [],
  setRegistered: async () => undefined,
  listAdvancedEnrollments: async () => ({}),
  getPreferences: async () => ({}),
  savePreferences: async () => undefined,
  resetPreferences: async () => undefined,
  getWritableFields: async () => WRITABLE_FIELDS,
  setPatientFields: async (fields: Record<string, unknown>) => {
    if (SLOW) await new Promise((r) => setTimeout(r, 1200));
    if (FAIL) throw new Error("refused, for the look of it");
    const out: Record<string, { status: "saved"; value: unknown }> = {};
    for (const [field, value] of Object.entries(fields)) {
      RECORD[field] = value;
      out[field] = { status: "saved", value };
    }
    // eslint-disable-next-line no-console
    console.info("[mock] wrote", fields, "→ record is now", { ...RECORD });
    return out;
  },
} as unknown as Parameters<typeof TrialMatches>[0]["state"];



// `?trial=<id>` opens the detail page directly; otherwise show the list
// (click a card / "View Trial" to reach the detail).
const directTrialId = new URLSearchParams(window.location.search).get("trial");
const queryClient = new QueryClient();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      {directTrialId ? (
        <div className="exact-root">
          <TrialDetailPage
            apiClient={apiClient}
            trialId={directTrialId}
            patientInfo={patientInfo}
            personId="mock-1"
            onBack={() => {
              window.location.href = "/mock-preview.html";
            }}
          />
        </div>
      ) : (
        <TrialMatches
          apiClient={apiClient}
          patientInfo={patientInfo}
          personId="mock-1"
          state={mockState}
        />
      )}
    </QueryClientProvider>
  </StrictMode>,
);
