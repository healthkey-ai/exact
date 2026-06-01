import type { CSSProperties } from "react";

import type { TrialMatch } from "./types";

interface Props {
  trial: TrialMatch;
  onClose: () => void;
}

const PANEL: CSSProperties = {
  border: "1px solid var(--exact-color-border)",
  borderRadius: "var(--exact-border-radius)",
  padding: "1rem",
  background: "var(--exact-color-surface)",
};

const META: CSSProperties = {
  fontSize: "0.875rem",
  color: "var(--exact-color-text-muted)",
  marginBottom: "0.5rem",
};

const SECTION_HEADER: CSSProperties = {
  margin: "1rem 0 0.5rem",
  fontSize: "0.875rem",
  fontWeight: 600,
  color: "var(--exact-color-text)",
};

export function TrialDetail({ trial, onClose }: Props) {
  // The list response already carries every field we render here
  // (briefTitle / officialTitle / phase / sponsor / matchScore /
  // goodnessScore / distance / location / attributesToFillIn / link).
  // The previous version re-fetched `/trials/{trialId}/` for "richer
  // detail data", but that detail re-fetch had to drop `patient_info`
  // from the request body (Fetch-spec / axios XHR don't allow
  // GET-with-body), so the matcher annotations on the response came
  // back patient-less and OVERWROTE the list's good values. Dropping
  // the re-fetch entirely keeps the patient-scoped values from the
  // list visible.
  const data = trial;

  return (
    <div style={PANEL}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: "1rem" }}>
        <h3 style={{ margin: 0, color: "var(--exact-color-primary)" }}>
          {data.briefTitle}
        </h3>
        <button
          type="button"
          onClick={onClose}
          style={{
            border: "1px solid var(--exact-color-border)",
            background: "transparent",
            padding: "0.25rem 0.625rem",
            borderRadius: "0.25rem",
            cursor: "pointer",
            font: "inherit",
            color: "var(--exact-color-text-muted)",
          }}
        >
          Close
        </button>
      </div>

      {data.officialTitle && data.officialTitle !== data.briefTitle ? (
        <div style={META}>{data.officialTitle}</div>
      ) : null}

      <div style={META}>
        <strong>{data.studyId}</strong> · {data.recruitingStatus}
        {data.phase?.length ? ` · ${data.phase.join(" / ")}` : null}
        {data.sponsor ? ` · ${data.sponsor}` : null}
      </div>

      <div style={META}>
        {data.matchScore != null ? `Match: ${data.matchScore}%` : null}
        {data.goodnessScore != null ? ` · Goodness: ${data.goodnessScore}` : null}
        {data.distance != null
          ? ` · ${data.distance} ${data.distanceUnits ?? ""}`
          : null}
      </div>

      {data.location?.length ? (
        <>
          <div style={SECTION_HEADER}>Locations</div>
          <ul style={{ margin: 0, paddingLeft: "1.25rem" }}>
            {data.location.slice(0, 10).map((loc) => (
              <li key={loc}>{loc}</li>
            ))}
          </ul>
        </>
      ) : null}

      {data.attributesToFillIn?.length ? (
        <>
          <div style={SECTION_HEADER}>Why "potential"</div>
          <ul style={{ margin: 0, paddingLeft: "1.25rem" }}>
            {data.attributesToFillIn.map((a, i) => (
              <li key={i} style={{ fontSize: "0.875rem" }}>
                {summarizeAttr(a)}
              </li>
            ))}
          </ul>
        </>
      ) : null}

      {data.link ? (
        <div style={{ marginTop: "1rem" }}>
          <a
            href={data.link}
            target="_blank"
            rel="noopener noreferrer"
            style={{ color: "var(--exact-color-primary)" }}
          >
            Open registry entry →
          </a>
        </div>
      ) : null}
    </div>
  );
}

function summarizeAttr(attr: Record<string, unknown>): string {
  // The matcher emits attr payloads already camelCased
  // (`UserToTrialAttrsMapper.potential_attrs_for_trial` returns
  // `{ userAttributeTitle, trialAttributeName, … }` directly — no DRF
  // camelCase middleware is in play because the keys are constructed
  // server-side in JS-shaped form). Render the human-friendly title
  // when present; fall back to a stringified payload so a future
  // contract change doesn't blank out the row.
  const title = attr.userAttributeTitle;
  if (typeof title === "string") return title;
  return JSON.stringify(attr);
}
