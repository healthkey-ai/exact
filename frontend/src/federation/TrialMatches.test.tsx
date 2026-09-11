// Component tests for the list's wiring — the layer three review rounds
// found bugs in and no pure-logic test could reach (#426). Each test below
// corresponds to a defect that shipped or nearly shipped:
//
//   - a page-clamping effect that could never fire, over a 404 that stuck
//   - a stale-data indicator keyed on `isFetching`, so every background
//     refetch dimmed the list
//   - a page reset that fired before the debounce, sending one request for
//     page 1 of the *previous* filter
//   - a country seed keyed on the country value, so it leaked between
//     patients
//
// They assert on the request log rather than on pixels: which request went
// out, and when, is the thing that was wrong.
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TrialMatches } from "./TrialMatches";
import { fakeApi, renderTrialMatches, trial } from "../test/renderTrialMatches";

const listed = (api: ReturnType<typeof fakeApi>) => api.listRequests();

beforeEach(() => {
  vi.useRealTimers();
});

describe("the first request", () => {
  it("goes to the search endpoint with the patient in the body", async () => {
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(listed(api).length).toBe(1));
    expect(listed(api)[0].url).toBe("/trials/search/match/");
    expect(listed(api)[0].body).toEqual({
      patient_info: { disease: "multiple myeloma" },
    });
  });

  it("carries the host's initialFilters.type as the active tab", async () => {
    // The tab state used to overwrite this, so a host asking for the
    // potential subset silently got the default tab.
    const api = fakeApi();
    renderTrialMatches(api, { initialFilters: { type: "potential" } });
    await waitFor(() => expect(listed(api).length).toBe(1));
    expect(listed(api)[0].params.type).toBe("potential");
  });
});

describe("paging", () => {
  it("asks for the page the reader clicked", async () => {
    const api = fakeApi({ count: 3, itemsTotalCount: 25, results: [trial(1)] });
    renderTrialMatches(api);
    await waitFor(() => expect(listed(api).length).toBe(1));

    await userEvent.click(await screen.findByRole("button", { name: "2" }));
    await waitFor(() => expect(listed(api).length).toBe(2));
    expect(listed(api)[1].params.page).toBe("2");
  });

  it("recovers to a page that exists when the server 404s", async () => {
    // DRF's paginator raises NotFound for a page past the end. The stale
    // response — and its stale pager — used to stay on screen, so every
    // further click reproduced it.
    const api = fakeApi({ count: 3, itemsTotalCount: 25 });
    renderTrialMatches(api);
    await waitFor(() => expect(listed(api).length).toBe(1));

    api.failNextWith(404);
    await userEvent.click(await screen.findByRole("button", { name: "3" }));

    // Recovery is to page 1, whose response React Query already holds — so
    // this asserts where the reader ends up, not a request count. A count
    // would be wrong: the recovered key is the one already in cache and
    // still fresh, so no network call is needed or wanted.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "1" })).toHaveAttribute(
        "aria-current",
        "page",
      ),
    );
    expect(screen.queryByText(/Failed to load trials/)).toBeNull();
    expect(await screen.findByText("Trial 1")).toBeInTheDocument();
  });

  it("returns to the first page when the tab changes", async () => {
    const api = fakeApi({ count: 3, itemsTotalCount: 25 });
    renderTrialMatches(api);
    await waitFor(() => expect(listed(api).length).toBe(1));
    await userEvent.click(await screen.findByRole("button", { name: "2" }));
    await waitFor(() => expect(listed(api).length).toBe(2));

    await userEvent.click(screen.getByRole("button", { name: /Potential/ }));
    await waitFor(() => expect(listed(api).length).toBe(3));
    const last = listed(api)[2];
    expect(last.params.type).toBe("potential");
    expect(last.params.page).toBeUndefined();
  });
});

describe("filters", () => {
  it("sends one request per typing burst, for the new filter", async () => {
    // The page reset used to fire on the first keystroke while the text was
    // still the old debounced value, so a reader on page 2 got a request for
    // page 1 of the UNfiltered list before the filtered one.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(listed(api).length).toBe(1));

    await user.click(screen.getByRole("button", { name: /Filter Results/ }));
    await user.type(await screen.findByLabelText("Title"), "myeloma");

    await vi.advanceTimersByTimeAsync(500);
    await waitFor(() => expect(listed(api).length).toBe(2));
    expect(listed(api)[1].params.searchTitle).toBe("myeloma");
    vi.useRealTimers();
  });

  it("counts only what the reader changed", async () => {
    const api = fakeApi();
    renderTrialMatches(api, { patientInfo: { disease: "mm", country: "US" } });
    await waitFor(() => expect(listed(api).length).toBe(1));

    // The seeded country is not a filter the reader applied.
    expect(screen.getByRole("button", { name: /Filter Results/ })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Filter Results/ }));
    await userEvent.selectOptions(await screen.findByLabelText("Phase (this or later)"), "PHASE3");
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Filters \(1\)/ })).toBeInTheDocument(),
    );
  });

  it("resets to the baseline, keeping the patient's country", async () => {
    const api = fakeApi();
    renderTrialMatches(api, { patientInfo: { disease: "mm", country: "US" } });
    await waitFor(() => expect(listed(api).length).toBe(1));
    expect(listed(api)[0].params.country).toBe("US");

    await userEvent.click(screen.getByRole("button", { name: /Filter Results/ }));
    await userEvent.selectOptions(await screen.findByLabelText("Phase (this or later)"), "PHASE3");
    await waitFor(() => expect(listed(api).length).toBe(2));

    await userEvent.click(screen.getByRole("button", { name: "Reset filters" }));

    // Back to the baseline, which is the first request's key — served from
    // cache, so again this asserts state rather than a new request. The
    // country must survive: a Reset to `{}` would widen the search to every
    // country in the registry.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Filter Results/ })).toBeInTheDocument(),
    );
    expect(await screen.findByLabelText("Phase (this or later)")).toHaveValue("");
    expect(listed(api).every((r) => r.params.country === "US")).toBe(true);
  });
});

describe("switching patients", () => {
  it("re-scopes the country and drops a disease-scoped trial type", async () => {
    // The seed marker used to key on the country VALUE, so a reader who
    // overrode Patient A's country kept it for Patient B in the same
    // country; and a trial type picked for an MM patient survived into a BC
    // patient, where its option does not exist and `by_trial_type` has no
    // leniency — an empty list from a control rendering blank.
    const api = fakeApi();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    const view = render(
      <QueryClientProvider client={queryClient}>
        <TrialMatches
          apiClient={api.client}
          queryClient={queryClient}
          patientInfo={{ disease: "multiple myeloma", country: "US" }}
        />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(listed(api).length).toBe(1));

    await userEvent.click(screen.getByRole("button", { name: /Filter Results/ }));
    await userEvent.selectOptions(await screen.findByLabelText("Trial type"), "drug");
    await waitFor(() => expect(listed(api).length).toBe(2));
    expect(listed(api)[1].params.trialType).toBe("drug");

    view.rerender(
      <QueryClientProvider client={queryClient}>
        <TrialMatches
          apiClient={api.client}
          queryClient={queryClient}
          patientInfo={{ disease: "breast cancer", country: "DE" }}
        />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(listed(api).length).toBeGreaterThan(2));
    const afterSwap = listed(api)[listed(api).length - 1];
    expect(afterSwap.params.country).toBe("DE");
    expect(afterSwap.params.trialType).toBeUndefined();
  });
});

describe("tab counts", () => {
  it("labels every tab from the server's counts, not from the page", async () => {
    const api = fakeApi({
      itemsTotalCount: 3,
      results: [trial(1), trial(2), trial(3)],
      tabCounts: { eligible: 7, potential: 12 },
    });
    renderTrialMatches(api);
    await screen.findByRole("button", { name: "Eligible, 19 trials" });
    await screen.findByRole("button", { name: "Fully matched, 7 trials" });
    await screen.findByRole("button", { name: "Potential, 12 trials" });
    // The accessible name is an `aria-label`, so it is computed rather than
    // read off the DOM — asserting only on it leaves what is actually
    // painted unobserved.
    expect(screen.getAllByTestId("tab-count").map((el) => el.textContent)).toEqual([
      "19",
      "7",
      "12",
    ]);
  });

  it("shows no badge at all when the server sent no counts", async () => {
    // Absent counts mean the server could not judge. A "0" there would
    // state a clinical result nobody produced.
    const api = fakeApi({ itemsTotalCount: 3, tabCounts: undefined });
    renderTrialMatches(api);
    // Named without a count at all, rather than "…, 0 trials".
    await screen.findByRole("button", { name: "Fully matched" });
    await screen.findByRole("button", { name: "Potential" });
    expect(screen.queryByRole("button", { name: /0 trials/ })).toBeNull();
    // Exactly one badge is painted — the active tab's, labelled from its
    // own `itemsTotalCount`, which is a number the response really carries.
    // The other two show nothing. Asserting on the DOM and not only on the
    // accessible name, which is an `aria-label` and so would have accepted
    // a badge rendering `0`.
    const painted = screen.getAllByTestId("tab-count");
    expect(painted.map((el) => el.textContent)).toEqual(["3"]);
  });
});

describe("the derived filters reach everything the reader sees", () => {
  it("scores the detail page under the same preferences as the card", async () => {
    // `country` stopped living in filter state when it became derived, and
    // the detail kept being handed raw state — so its request went out
    // without the patient's country and could score, rank by distance and
    // judge eligibility differently from the card just clicked.
    const api = fakeApi();
    renderTrialMatches(api, { patientInfo: { disease: "mm", country: "US" } });
    await waitFor(() => expect(listed(api).length).toBe(1));

    // Exact name: the card itself is a `role="button"` whose accessible
    // name contains the inner button's text, so a regex matches both.
    await userEvent.click(await screen.findByRole("button", { name: "View Trial" }));
    await waitFor(() => expect(api.detailRequests().length).toBe(1));
    expect(api.detailRequests()[0].params.country).toBe("US");
  });
});

describe("a trial type belongs to the patient it was chosen for", () => {
  const twoPatients = async (first: Record<string, unknown>, second: Record<string, unknown>) => {
    const api = fakeApi();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    const ui = (patient: Record<string, unknown>) => (
      <QueryClientProvider client={queryClient}>
        <TrialMatches apiClient={api.client} queryClient={queryClient} patientInfo={patient} />
      </QueryClientProvider>
    );
    const view = render(ui(first));
    await waitFor(() => expect(listed(api).length).toBe(1));
    await userEvent.click(screen.getByRole("button", { name: /Filter Results/ }));
    await userEvent.selectOptions(await screen.findByLabelText("Trial type"), "drug");
    await waitFor(() => expect(listed(api).length).toBe(2));
    view.rerender(ui(second));
    await waitFor(() => expect(listed(api).length).toBeGreaterThan(2));
    return { api, last: listed(api)[listed(api).length - 1] };
  };

  it("drops it for a different patient with the same disease", async () => {
    // Keyed on the disease, this leaked: two MM patients share a disease
    // code, so the second was silently narrowed by the first one's choice.
    const { last } = await twoPatients(
      { disease: "multiple myeloma", personRef: "A" },
      { disease: "multiple myeloma", personRef: "B" },
    );
    expect(last.params.trialType).toBeUndefined();
  });

  it("drops it for a patient with a different disease", async () => {
    const { last } = await twoPatients(
      { disease: "multiple myeloma" },
      { disease: "breast cancer" },
    );
    expect(last.params.trialType).toBeUndefined();
  });
});

describe("a host's initialFilters.trialType", () => {
  it("survives the patient arriving a render later", async () => {
    // Hosts fetch the profile and render `patientInfo={null}` meanwhile.
    // Keyed on the first render's disease, the mask latched on as soon as
    // the patient loaded: the first request honoured the host's type and
    // every request after it silently did not.
    const api = fakeApi();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    const ui = (patient: Record<string, unknown> | null) => (
      <QueryClientProvider client={queryClient}>
        <TrialMatches
          apiClient={api.client}
          queryClient={queryClient}
          patientInfo={patient}
          initialFilters={{ trialType: "drug" }}
        />
      </QueryClientProvider>
    );
    const view = render(ui(null));
    view.rerender(ui({ disease: "multiple myeloma" }));
    await waitFor(() => expect(listed(api).length).toBeGreaterThan(0));
    const last = listed(api)[listed(api).length - 1];
    expect(last.params.trialType).toBe("drug");
  });

  it("falls back to the host's scope, and the badge agrees", async () => {
    // The reader's pick is made for one patient; the host's
    // `initialFilters.trialType` is a mount-time scope. So a patient switch
    // drops the pick back to the host's value rather than to nothing —
    // which also makes the badge agree by construction, since the baseline
    // holds exactly that value.
    //
    // Dropping to `undefined` instead cost a second Reset click: Reset
    // stored the masked baseline while clearing the owner, so the next
    // render's unmasked baseline disagreed with what had just been written,
    // the badge counted the disagreement, and the button stayed armed.
    const api = fakeApi();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    const ui = (patient: Record<string, unknown>) => (
      <QueryClientProvider client={queryClient}>
        <TrialMatches
          apiClient={api.client}
          queryClient={queryClient}
          patientInfo={patient}
          initialFilters={{ trialType: "drug" }}
        />
      </QueryClientProvider>
    );
    const view = render(ui({ disease: "multiple myeloma", ref: "A" }));
    await waitFor(() => expect(listed(api).length).toBe(1));
    expect(listed(api)[0].params.trialType).toBe("drug");

    await userEvent.click(screen.getByRole("button", { name: /Filter/ }));
    await userEvent.selectOptions(await screen.findByLabelText("Trial type"), "device");
    await waitFor(() => expect(listed(api).length).toBe(2));
    expect(listed(api)[1].params.trialType).toBe("device");

    view.rerender(ui({ disease: "multiple myeloma", ref: "B" }));
    await waitFor(() => expect(listed(api).length).toBeGreaterThan(2));

    // The new patient gets the host's scope back, not the previous
    // reader's choice...
    const last = listed(api)[listed(api).length - 1];
    expect(last.params.trialType).toBe("drug");
    // ...and nothing is counted, because that is exactly the baseline.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Filter Results/ })).toBeInTheDocument(),
    );
    expect(screen.queryByRole("button", { name: /Filters \(/ })).toBeNull();
  });

  it("clears in one Reset click", async () => {
    const api = fakeApi();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    const ui = (patient: Record<string, unknown>) => (
      <QueryClientProvider client={queryClient}>
        <TrialMatches
          apiClient={api.client}
          queryClient={queryClient}
          patientInfo={patient}
          initialFilters={{ trialType: "drug" }}
        />
      </QueryClientProvider>
    );
    const view = render(ui({ disease: "multiple myeloma", ref: "A" }));
    await waitFor(() => expect(listed(api).length).toBe(1));

    await userEvent.click(screen.getByRole("button", { name: /Filter/ }));
    await userEvent.selectOptions(await screen.findByLabelText("Trial type"), "device");
    await userEvent.selectOptions(
      await screen.findByLabelText("Recruitment status"),
      "RECRUITING",
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Filters \(2\)/ })).toBeInTheDocument(),
    );

    view.rerender(ui({ disease: "multiple myeloma", ref: "B" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Filters \(1\)/ })).toBeInTheDocument(),
    );

    await userEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /Filter Results/ })).toBeInTheDocument(),
    );
    // One click, not two: the button has to be disarmed afterwards.
    expect(
      (screen.getByRole("button", { name: "Reset filters" }) as HTMLButtonElement).disabled,
    ).toBe(true);
  });
});

describe("a trial type picked before the patient is known", () => {
  it("still goes stale when a real patient arrives", async () => {
    // `patientIdentity` is null for a host placeholder like
    // `patientInfo={}` — the panel is live in that window because
    // `useTrials` only requires `patientInfo != null`. Sharing the
    // "unclaimed" sentinel with that null, a type picked there was read
    // back as belonging to nobody, never went stale, and followed the
    // reader into every patient afterwards — across diseases included,
    // which is the empty-list-from-a-blank-control case.
    const api = fakeApi();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    const ui = (patient: Record<string, unknown>) => (
      <QueryClientProvider client={queryClient}>
        <TrialMatches apiClient={api.client} queryClient={queryClient} patientInfo={patient} />
      </QueryClientProvider>
    );
    const view = render(ui({}));
    await waitFor(() => expect(listed(api).length).toBe(1));

    await userEvent.click(screen.getByRole("button", { name: /Filter/ }));
    await userEvent.selectOptions(await screen.findByLabelText("Trial type"), "drug");
    await waitFor(() => expect(listed(api).length).toBe(2));
    expect(listed(api)[1].params.trialType).toBe("drug");

    view.rerender(ui({ disease: "breast cancer" }));
    await waitFor(() => expect(listed(api).length).toBeGreaterThan(2));
    const last = listed(api)[listed(api).length - 1];
    expect(last.params.trialType).toBeUndefined();
  });
});
