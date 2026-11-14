// The admin list surface — the All Trials tab and the validated-only filter —
// is offered only when the HOST says so. It is never inferred from data: the
// same account can be an admin on one deployment and not another, and the
// server enforces `?type=all` independently.
import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { fakeApi, renderTrialMatches } from "../test/renderTrialMatches";

const listed = (api: ReturnType<typeof fakeApi>) => api.listRequests();

describe("the host-declared admin surface", () => {
  it("hides the All tab and the validated-only filter by default", async () => {
    const api = fakeApi();
    renderTrialMatches(api);

    await waitFor(() => expect(listed(api).length).toBe(1));
    expect(screen.queryByRole("button", { name: /^All Trials/ })).toBeNull();

    await userEvent.click(
      screen.getByRole("button", { name: /Filter Results|Filters/ }),
    );
    expect(screen.queryByLabelText("Validated only")).toBeNull();
  });

  it("offers the All tab and the validated-only filter to an admin host", async () => {
    const api = fakeApi();
    renderTrialMatches(api, { features: { admin: true } });

    await waitFor(() => expect(listed(api).length).toBe(1));
    await userEvent.click(await screen.findByRole("button", { name: /^All Trials/ }));
    await waitFor(() => expect(listed(api).length).toBe(2));
    expect(listed(api)[1].params.type).toBe("all");

    await userEvent.click(
      screen.getByRole("button", { name: /Filter Results|Filters/ }),
    );
    await userEvent.click(screen.getByLabelText("Validated only"));
    await waitFor(() => expect(listed(api).length).toBe(3));
    expect(listed(api)[2].params.validatedOnly).toBe("true");
  });

  it("shows no tab badges on the All view, even after a counted Eligible view", async () => {
    // The corpus key excludes `type`, so the Eligible counts stay warm across
    // the switch. Painting them beside All would label a corpus they were not
    // computed over.
    const api = fakeApi({ tabCounts: { eligible: 7, potential: 12 } });
    renderTrialMatches(api, { features: { admin: true } });

    await waitFor(() => expect(listed(api).length).toBe(1));
    await waitFor(() =>
      expect(screen.getAllByTestId("tab-count").length).toBeGreaterThan(0),
    );

    await userEvent.click(screen.getByRole("button", { name: /^All Trials/ }));
    await waitFor(() => expect(listed(api).length).toBe(2));
    expect(screen.queryAllByTestId("tab-count")).toHaveLength(0);
  });

  it("opens on the All tab from initialFilters.type=all only when offered", async () => {
    const admin = fakeApi();
    renderTrialMatches(admin, {
      features: { admin: true },
      initialFilters: { type: "all" },
    });
    await waitFor(() => expect(listed(admin).length).toBe(1));
    expect(listed(admin)[0].params.type).toBe("all");

    const plain = fakeApi();
    renderTrialMatches(plain, { initialFilters: { type: "all" } });
    await waitFor(() => expect(listed(plain).length).toBe(1));
    // A host that did not offer the tab must not be silently switched to the
    // wider corpus by a stray saved filter.
    expect(listed(plain)[0].params.type).toBeUndefined();
  });
});
