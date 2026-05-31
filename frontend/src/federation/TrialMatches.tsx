// Scaffold for the federated `./TrialMatches` export (#103, part of #101).
// This file deliberately renders a placeholder — the real component
// (filters, eligibility grouping, distance sort, trial detail) lands in
// #104. Keeping the export shape stable now means hosts can wire the
// remote before the component body is filled in.
import { useEffect } from "react";

import { injectStyles } from "./injectStyles";
import type { TrialMatchesProps } from "./types";

export function TrialMatches(_props: TrialMatchesProps) {
  useEffect(() => {
    injectStyles();
  }, []);

  return (
    <div className="exact-root" style={{ padding: "1.5rem" }}>
      <div
        style={{
          borderRadius: "var(--exact-border-radius)",
          background: "var(--exact-color-surface)",
          border: "1px solid var(--exact-color-border)",
          padding: "1.5rem",
          color: "var(--exact-color-text)",
        }}
      >
        <h2 style={{ marginTop: 0, color: "var(--exact-color-primary)" }}>
          EXACT Trial Matches
        </h2>
        <p style={{ color: "var(--exact-color-text-muted)" }}>
          Federated component scaffold. Real rendering lands in #104.
        </p>
      </div>
    </div>
  );
}

export default TrialMatches;
