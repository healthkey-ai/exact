// The host owns registration: CB's patient flow opens the CRC chat or the EU
// invite, and only the navigator branch toggles `isForParticipation` directly.
// When the slot is supplied EXACT must draw no registration UI of its own and
// must never call `setRegistered`.
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  fakeApi,
  fakeState,
  renderTrialMatches,
  trialDetail,
} from "../test/renderTrialMatches";

const open = async () => {
  const cards = await screen.findAllByRole("button", { name: "View Trial" });
  await userEvent.click(cards[0]);
  await screen.findByText("Back to all trials");
  await screen.findByRole("heading", { level: 1, name: "Trial 1" });
};

const hostSlot = () =>
  vi.fn(({ variant }: { variant: "header" | "card" }) => (
    <button type="button" data-testid={`host-register-${variant}`}>
      Host register {variant}
    </button>
  ));

describe("the host register slot", () => {
  it("draws both host variants without any EXACT state adapter", async () => {
    const api = fakeApi();
    const slot = hostSlot();
    renderTrialMatches(api, { renderRegisterInterest: slot });

    await open();

    expect(screen.getByTestId("host-register-header")).toBeInTheDocument();
    expect(screen.getByTestId("host-register-card")).toBeInTheDocument();
    expect(slot).toHaveBeenCalledWith(
      expect.objectContaining({ trialId: 1, variant: "header" }),
    );
    expect(slot).toHaveBeenCalledWith(
      expect.objectContaining({ trialId: 1, variant: "card" }),
    );
    // No EXACT control or card copy beside it.
    expect(screen.queryByRole("button", { name: /I'm Interested|Withdraw/ })).toBeNull();
    expect(screen.queryByText(/Registering marks this trial/)).toBeNull();
  });

  it("replaces EXACT's registered control and never writes it", async () => {
    const api = fakeApi();
    const state = fakeState({ registered: ["1"] });
    const slot = hostSlot();
    renderTrialMatches(api, {
      state: state.adapter,
      stateIdentity: "patient-1",
      renderRegisterInterest: slot,
    });

    await open();

    expect(screen.getByTestId("host-register-header")).toBeInTheDocument();
    expect(slot).toHaveBeenCalledWith(
      expect.objectContaining({ variant: "header", isRegistered: true }),
    );
    // The built-in "Withdraw" and the card's registered copy are gone.
    expect(screen.queryByRole("button", { name: "Withdraw" })).toBeNull();
    expect(screen.queryByText("You registered interest in this trial")).toBeNull();

    await userEvent.click(screen.getByTestId("host-register-card"));
    expect(state.adapter.setRegistered).not.toHaveBeenCalled();
  });

  it("does not print EXACT's own not-eligible or unavailable registration copy", async () => {
    const api = fakeApi();
    api.setDetail(
      trialDetail(1, {
        matchingType: "not_eligible",
        details: { trialEligibilityAttributes: [] },
      } as never),
    );
    const state = fakeState({
      overrides: {
        listRegisteredIds: vi.fn(async () => {
          throw new Error("preferences unavailable");
        }),
      },
    });
    renderTrialMatches(api, {
      state: state.adapter,
      stateIdentity: "patient-1",
      renderRegisterInterest: hostSlot(),
    });

    await open();

    // Both statements are EXACT's own; the host owns the whole registration
    // surface now and its copy must not be contradicted by ours.
    expect(
      screen.queryByText(/You may not meet this trial's eligibility criteria/),
    ).toBeNull();
    expect(
      screen.queryByText(/Couldn't load whether you have registered interest/),
    ).toBeNull();
  });

  it("passes an advanced status instead of drawing EXACT's completed panel", async () => {
    const api = fakeApi();
    const state = fakeState({ advanced: { "1": "completed" } });
    const slot = hostSlot();
    renderTrialMatches(api, {
      state: state.adapter,
      stateIdentity: "patient-1",
      renderRegisterInterest: slot,
    });

    await open();

    expect(slot).toHaveBeenCalledWith(
      expect.objectContaining({ variant: "card", advancedStatus: "completed" }),
    );
    expect(
      screen.queryByText(/recorded as completed/),
    ).toBeNull();
  });
});
