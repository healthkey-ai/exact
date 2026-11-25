// The genomic requirement is part of the search result, not a detail-only
// fact: without it a genomic-filtered list says nothing about why those rows
// are there. The card keeps the row off when the server has no values.
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TrialCard } from "./TrialCard";
import type { TrialMatch } from "./types";

const trial = (over: Partial<TrialMatch> = {}): TrialMatch =>
  ({
    trialId: 1,
    studyId: "NCT00001",
    briefTitle: "A trial",
    officialTitle: "",
    phase: [],
    disease: null,
    recruitingStatus: "RECRUITING",
    sponsor: "",
    link: "",
    trialType: null,
    location: [],
    interventionTreatments: null,
    postedDate: null,
    lastUpdateDate: null,
    firstEnrolment: null,
    enrollmentCount: null,
    patientBurdenScore: null,
    goodnessScore: null,
    matchScore: null,
    matchingType: null,
    stage: "",
    attributesToFillIn: [],
    closestLocationGeoPoint: null,
    distance: null,
    distanceUnits: null,
    ...over,
  }) as TrialMatch;

describe("TrialCard genomic requirements", () => {
  it("shows genes and variants when the row carries them", () => {
    render(
      <TrialCard
        trial={trial({
          mutationGenesRequired: ["brca1", "brca2"],
          mutationVariantsRequired: ["c.524g_a_(r175h)"],
        })}
      />,
    );

    expect(screen.getByText(/Mutation Genes/)).toBeInTheDocument();
    expect(screen.getByText("brca1, brca2")).toBeInTheDocument();
    expect(screen.getByText(/Mutation Variants/)).toBeInTheDocument();
    expect(screen.getByText("c.524g_a_(r175h)")).toBeInTheDocument();
  });

  it("shows only the axis that is present", () => {
    render(<TrialCard trial={trial({ mutationGenesRequired: ["tp53"] })} />);

    expect(screen.getByText(/Mutation Genes/)).toBeInTheDocument();
    expect(screen.queryByText(/Mutation Variants/)).not.toBeInTheDocument();
  });

  it("omits the row when neither axis is present", () => {
    render(<TrialCard trial={trial()} />);

    expect(screen.queryByText(/Mutation Genes/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Mutation Variants/)).not.toBeInTheDocument();
  });
});
