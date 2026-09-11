/** Per-criterion explainability for the high-risk MCL attribute (#4408).
 *
 *  EXACT has returned `highRiskMclCriteriaBreakdown` from `retrieve` since
 *  #4408 and the remote dropped it on the floor. The eligibility table above
 *  can only say "High Risk Mcl Criteria: matched" — one verdict over a rule
 *  that is really "at least N of these, none of those, or any one of these".
 *  For a reader deciding whether a trial is worth a phone call, WHICH
 *  criterion they meet is the whole question.
 */
import type { HighRiskMclCriteriaBreakdown, MclCriterion } from "./types";
import { CheckIcon } from "./bits";

/** Which of the trial's three lists a criterion sits in. The same status means
 *  a different thing in each, so nothing here is shared between them. */
type ListKind = "required" | "sufficient" | "excluded";

/** What a criterion's status means to the reader.
 *
 *  The server reports every criterion in ELIGIBILITY terms, so on an EXCLUDED
 *  criterion `matched` means "confirmed absent, good" and `not_matched` means
 *  "present, which rules you out". Printing the raw status word next to an
 *  excluded criterion would tell the reader the exact opposite of the truth.
 *
 *  In a SUFFICIENT list an unmet criterion is not a failure — any one of them
 *  qualifies on its own, so the others being absent costs nothing. Marking
 *  them in red put two failure marks under a heading saying exactly that,
 *  beside a verdict saying the patient qualifies.
 *
 *  And "you do not have this" is not "not known from your data": #4399 went to
 *  some trouble to keep confirmed-absent apart from never-entered, and a
 *  patient who was sequenced and is TP53-negative should not read the same
 *  sentence as one who was never tested.
 */
function phrasing(
  status: MclCriterion["status"],
  kind: ListKind,
): { text: string; tone: "good" | "bad" | "neutral" | "unknown" } {
  if (status === "unknown") {
    return { text: "not known from your data", tone: "unknown" };
  }
  if (kind === "excluded") {
    return status === "matched"
      ? { text: "you are clear of this", tone: "good" }
      : { text: "you have this — it rules this trial out", tone: "bad" };
  }
  if (status === "matched") return { text: "you have this", tone: "good" };
  return { text: "you do not have this", tone: kind === "sufficient" ? "neutral" : "bad" };
}

const MARK: Record<"good" | "bad" | "neutral" | "unknown", string> = {
  good: "",
  bad: "\u2715",
  neutral: "\u2013",
  unknown: "?",
};

function CriterionList({
  heading,
  codes,
  kind,
  titleOf,
}: {
  heading: string;
  codes: MclCriterion[];
  kind: ListKind;
  titleOf: (code: string) => string;
}) {
  if (!codes.length) return null;
  return (
    <div className="exact-mcl__group">
      <h3 className="exact-mcl__group-title">{heading}</h3>
      <ul className="exact-mcl__list">
        {codes.map((c) => {
          const { text, tone } = phrasing(c.status, kind);
          return (
            <li key={c.code} className={`exact-mcl__item is-${tone}`}>
              <span className="exact-mcl__mark" aria-hidden="true">
                {tone === "good" ? <CheckIcon /> : MARK[tone]}
              </span>
              <span className="exact-mcl__name">{titleOf(c.code)}</span>
              <span className="exact-mcl__status">{text}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

const VERDICT: Record<string, string> = {
  matched: "You meet this trial's high-risk criteria.",
  not_matched: "You do not meet this trial's high-risk criteria.",
  unknown: "Some of the data this rule needs is missing from your profile.",
};

export function HighRiskMclPanel({
  breakdown,
  titleOf,
}: {
  breakdown: HighRiskMclCriteriaBreakdown;
  titleOf: (code: string) => string;
}) {
  // `?? []` on all three: a breakdown that arrives without one of them — a
  // remote deployed ahead of the API, a gateway that prunes nulls, a
  // hand-rolled host fixture — would otherwise throw inside the render, and
  // with no ErrorBoundary above it React unmounts the whole federated tree.
  // The host is then left with a blank trials area, not a missing panel.
  const required = breakdown.required ?? [];
  const excluded = breakdown.excluded ?? [];
  const sufficientAny = breakdown.sufficientAny ?? [];
  const minCount = breakdown.minCount ?? 1;
  const matchedCount = breakdown.matchedCount ?? 0;
  const verdict = VERDICT[breakdown.aggregate];
  // The server ORs the two inclusion rules (`inclusion_rules` in
  // `_match_criteria_count`), so with both lists populated the required block
  // is one route of two, not a requirement. Heading it "Requires" told a
  // patient who qualified via the alternatives that they had failed something.
  const eitherOr = required.length > 0 && sufficientAny.length > 0;
  const count =
    required.length > 1 ? `at least ${minCount} of these — you have ${matchedCount}` : "";

  return (
    <section className="exact-panel exact-mcl">
      <h2 className="exact-panel__title">High-Risk MCL Criteria</h2>
      {verdict ? (
        <p className={`exact-mcl__verdict is-${breakdown.aggregate}`}>{verdict}</p>
      ) : null}
      <CriterionList
        // The count is part of the rule, not decoration: "1 of 3" and "3 of 3"
        // are different trials to a reader, and the aggregate verdict alone
        // cannot tell them apart.
        heading={
          eitherOr
            ? count
              ? `Either — ${count}`
              : "Either"
            : count
              ? `Requires ${count}`
              : "Requires"
        }
        codes={required}
        kind="required"
        titleOf={titleOf}
      />
      <CriterionList
        heading={eitherOr ? "Or — any one of these on its own" : "Any one of these qualifies on its own"}
        codes={sufficientAny}
        kind="sufficient"
        titleOf={titleOf}
      />
      <CriterionList
        heading="Rules the trial out"
        codes={excluded}
        kind="excluded"
        titleOf={titleOf}
      />
    </section>
  );
}
