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
  count: 1,
  itemsTotalCount: results.length,
  next: null,
  previous: null,
  results,
};

const formSettings = {
  trialType: {
    options: [
      { value: "interventional", label: "Interventional" },
      { value: "observational", label: "Observational" },
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
    groupNames: [
      { value: "trialEligibilityAttributes", label: "Trial Eligibility Attributes" },
    ],
    details: {
      general: [],
      trialEligibilityAttributes: [
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
const apiClient = {
  get: async (url: string) => {
    if (url.includes("form-settings")) return { data: formSettings };
    const detail = url.match(/^\/trials\/([^/]+)\/$/);
    if (detail) return { data: detailFor(detail[1]) };
    return { data: trials };
  },
  post: async (url: string) => {
    const detail = url.match(/^\/trials\/([^/]+)\/match\/$/);
    if (detail) return { data: detailFor(detail[1]) };
    return { data: trials };
  },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
} as any;

const patientInfo = { disease: "MM", country: "US" };

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
            onBack={() => {
              window.location.href = "/mock-preview.html";
            }}
          />
        </div>
      ) : (
        <TrialMatches apiClient={apiClient} patientInfo={patientInfo} />
      )}
    </QueryClientProvider>
  </StrictMode>,
);
