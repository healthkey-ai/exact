// Two structures axe failed on the federated page. Neither is visible in a
// screenshot: the card carried `role="button"` around its own controls, which
// axe reports as `nested-interactive` and a screen reader announces as a
// button that contains buttons; and the matched tick was a `<span>` with an
// `aria-label` and no role, which axe reports as `aria-prohibited-attr`.
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import {
  fakeApi,
  renderTrialMatches,
  trialDetail,
} from "../test/renderTrialMatches";
import type { TrialDetailField } from "./types";

const matchedRow: TrialDetailField = {
  name: "patientAge",
  label: "Age Minimum",
  type: "number",
  value: 18,
  uvalue: 40,
  matchingType: "matched",
} as never;

describe("structures the accessibility tree can describe", () => {
  it("does not wrap the card's own controls in a button role", async () => {
    const api = fakeApi();
    renderTrialMatches(api);

    const card = (await screen.findByText("Trial 1")).closest(".exact-card");
    expect(card).not.toBeNull();
    // The bookmark, the "View Trial" button and the score links are inside.
    // A role here makes this element a button containing buttons.
    expect(card).not.toHaveAttribute("role");
    expect(card).not.toHaveAttribute("tabindex");
    expect(card).not.toHaveAttribute("aria-pressed");

    // The keyboard path is the real control, not the card.
    const view = screen.getByRole("button", { name: "View Trial" });
    expect(view.tagName).toBe("BUTTON");
    expect(view).toHaveAttribute("type", "button");
  });

  it("opens a trial from the keyboard through the View Trial control", async () => {
    // The card's own Enter handler went away with the role. This is the path
    // that replaced it, asserted rather than assumed.
    const api = fakeApi();
    renderTrialMatches(api);

    const view = await screen.findByRole("button", { name: "View Trial" });
    view.focus();
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByText("Back to all trials")).toBeInTheDocument();
  });

  it("still opens a trial when the card itself is clicked", async () => {
    const api = fakeApi();
    renderTrialMatches(api);

    const card = (await screen.findByText("Trial 1")).closest(".exact-card");
    await userEvent.click(card as HTMLElement);
    expect(await screen.findByText("Back to all trials")).toBeInTheDocument();
  });

  it("gives the matched tick a role its aria-label is allowed on", async () => {
    const api = fakeApi();
    api.setDetail(
      trialDetail(1, {
        details: { trialEligibilityAttributes: [matchedRow] },
      } as never),
    );
    renderTrialMatches(api);

    await userEvent.click(
      await screen.findByRole("button", { name: "View Trial" }),
    );
    // `getByRole` fails if the label sits on an element with no role, which
    // is exactly the shape axe refused.
    expect(await screen.findByRole("img", { name: "matches" })).toBeInTheDocument();
  });
});
