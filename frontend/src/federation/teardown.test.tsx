/** Timers and settles that outlive the page must not reach React (#728).
 *
 *  CI failed once with every test green and an uncaught
 *  `ReferenceError: window is not defined`. See `errorsAfterTeardown`.
 */
import { cleanup, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { fakeApi, fakeState, renderTrialMatches } from "../test/renderTrialMatches";
import { errorsAfterTeardown } from "../test/afterTeardown";
import { SAVED_FILTERS_GRACE_MS } from "./hooks";

describe("after the page is gone (#728)", () => {
  it("the saved-filters grace timer does not fire into React", async () => {
    renderTrialMatches(fakeApi(), { state: fakeState().adapter });
    await screen.findByText("Trial 1");
    // Unmounted before the cap's 300 ms are up: the cap is still pending.
    cleanup();
    const escaped = await errorsAfterTeardown(() => {}, SAVED_FILTERS_GRACE_MS + 100);
    expect(escaped).toEqual([]);
  });
});
