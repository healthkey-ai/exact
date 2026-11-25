// The `styles` prop exists for hosts whose own CSS is unlayered.
//
// Tailwind v3's preflight is unlayered, and unlayered author rules outrank
// every layered rule regardless of specificity. CB's `ui.v2` is such a host,
// so a sheet wrapped in `@layer mf-remote` loses its typography and control
// styling there. `styles="unlayered"` injects the same scoped sheet without
// the wrapper.
//
// Kept in its own file because the mode is a module-instance decision: the
// first mount latches it, and a test file that rendered the default first
// could never observe the other mode. The deep-link test below is FIRST on
// purpose — it is the one that catches the detail page's passive effect
// running before this component's injection.
import { describe, expect, it } from "vitest";
import { waitFor } from "@testing-library/react";

import { injectStyles } from "./injectStyles";
import { fakeApi, renderTrialMatches } from "../test/renderTrialMatches";

const LAYERED = 'style[data-mf="exact-remote"]';
const UNLAYERED = 'style[data-mf="exact-remote-unlayered"]';

describe('styles="unlayered"', () => {
  it("decides the mode before a deep-linked detail page injects its fallback", async () => {
    // Child passive effects run before the parent's, and TrialDetailPage
    // injects as a fallback when it was not mounted under the list. A parent
    // `useEffect` would therefore arrive second and append the layered sheet
    // beside the unlayered one. The parent injects in an insertion effect,
    // which runs before every passive effect.
    renderTrialMatches(fakeApi(), {
      styles: "unlayered",
      trialId: 1,
      // Half a navigation contract is read as none: without the setter the
      // remote keeps its own selection and never deep-links to the detail
      // page this test is about.
      onTrialIdChange: () => {},
    });

    await waitFor(() => expect(document.querySelector(".exact-detail__actions")).not.toBeNull());

    expect(document.querySelector(UNLAYERED)).not.toBeNull();
    expect(document.querySelector(LAYERED)).toBeNull();
  });

  it("keeps the first mount's mode when a later mount asks for the other one", async () => {
    // The mode belongs to the bundle instance. A second mount that passes
    // `styles="layered"` inherits the first choice instead of adding a sheet
    // in a mode that would fight it in the cascade.
    renderTrialMatches(fakeApi(), { styles: "layered" });

    await waitFor(() => expect(document.querySelector(UNLAYERED)).not.toBeNull());
    expect(document.querySelector(LAYERED)).toBeNull();

    // And a direct call with no options — what the detail page makes when it
    // mounts on its own — inherits the same mode.
    injectStyles();
    expect(document.querySelector(LAYERED)).toBeNull();
    expect(document.querySelector(UNLAYERED)).not.toBeNull();
  });
});
