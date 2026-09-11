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

describe("without a state adapter", () => {
  it("renders neither the state tabs nor a bookmark control", async () => {
    // They would be a tab that cannot answer and a button that forgets.
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(listed(api).length).toBe(1));
    expect(screen.queryByRole("button", { name: /^Favorites/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Registered/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /favorites$/i })).toBeNull();
  });

  it("sends no trial_ids at all", async () => {
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(listed(api).length).toBe(1));
    expect(listed(api)[0].body).toEqual({
      patient_info: { disease: "multiple myeloma" },
    });
  });
});

describe("with a state adapter", () => {
  const makeState = (favorites: string[] = [], registered: string[] = []) => {
    const calls: { setFavorite: [string, boolean][] } = { setFavorite: [] };
    const state = {
      listFavoriteIds: vi.fn(async () => favorites),
      listRegisteredIds: vi.fn(async () => registered),
      setFavorite: vi.fn(async (id: string, on: boolean) => {
        calls.setFavorite.push([id, on]);
        if (on) favorites.push(id);
        else favorites = favorites.filter((f) => f !== id);
      }),
      setRegistered: vi.fn(async () => undefined),
      getPreferences: vi.fn(async () => ({})),
      savePreferences: vi.fn(async () => undefined),
      resetPreferences: vi.fn(async () => undefined),
    };
    return { state, calls };
  };

  const renderWithState = (api: ReturnType<typeof fakeApi>, state: unknown) => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    return render(
      <QueryClientProvider client={queryClient}>
        <TrialMatches
          apiClient={api.client}
          queryClient={queryClient}
          patientInfo={{ disease: "multiple myeloma" }}
          state={state as never}
        />
      </QueryClientProvider>,
    );
  };

  it("offers the Favorites and Registered tabs, counted from the adapter", async () => {
    const api = fakeApi();
    const { state } = makeState(["1", "2"], ["3"]);
    renderWithState(api, state);
    await screen.findByRole("button", { name: "Favorites, 2 trials" });
    await screen.findByRole("button", { name: "Registered, 1 trials" });
  });

  it("narrows the list by the saved ids when the tab is opened", async () => {
    const api = fakeApi();
    const { state } = makeState(["11", "12"]);
    renderWithState(api, state);
    await waitFor(() => expect(listed(api).length).toBe(1));

    await userEvent.click(await screen.findByRole("button", { name: /^Favorites/ }));
    await waitFor(() => expect(listed(api).length).toBe(2));
    expect((listed(api)[1].body as { trial_ids: string[] }).trial_ids).toEqual([
      "11",
      "12",
    ]);
  });

  it("asks for nothing — not for everything — when there are no bookmarks", async () => {
    // The failure this guards: `[]` collapsing into "no filter" answers an
    // empty Favorites tab with the whole registry.
    const api = fakeApi();
    const { state } = makeState([]);
    renderWithState(api, state);
    await waitFor(() => expect(listed(api).length).toBe(1));

    await userEvent.click(await screen.findByRole("button", { name: /^Favorites/ }));
    await waitFor(() => expect(listed(api).length).toBe(2));
    expect((listed(api)[1].body as { trial_ids: string[] }).trial_ids).toEqual([]);
  });

  it("shows no rows under the tab until its ids have arrived", async () => {
    // Not a request that can be prevented — while the ids are unknown
    // `trialIds` is `undefined`, which is the DEFAULT tab's query key, so
    // React Query answers from cache without asking anyone. What must not
    // happen is the render: the eligible list under a Favorites heading
    // says those trials are bookmarked.
    const api = fakeApi();
    let release: (ids: string[]) => void = () => {};
    const pending = new Promise<string[]>((resolve) => {
      release = resolve;
    });
    const state = {
      listFavoriteIds: vi.fn(() => pending),
      listRegisteredIds: vi.fn(async () => []),
      setFavorite: vi.fn(async () => undefined),
      setRegistered: vi.fn(async () => undefined),
      getPreferences: vi.fn(async () => ({})),
      savePreferences: vi.fn(async () => undefined),
      resetPreferences: vi.fn(async () => undefined),
    };
    renderWithState(api, state);
    await userEvent.click(await screen.findByRole("button", { name: /^Favorites/ }));

    const before = listed(api).length;
    await new Promise((r) => setTimeout(r, 50));
    expect(listed(api).length).toBe(before);
    // The previous tab's rows are gone, not relabelled.
    expect(screen.queryByText("Trial 1")).toBeNull();
    expect(screen.getByText("Loading trials…")).toBeInTheDocument();

    release(["42"]);
    await waitFor(() => expect(listed(api).length).toBe(before + 1));
    const last = listed(api)[listed(api).length - 1];
    expect((last.body as { trial_ids: string[] }).trial_ids).toEqual(["42"]);
  });

  it("bookmarks a trial from its card", async () => {
    const api = fakeApi();
    const { state, calls } = makeState([]);
    renderWithState(api, state);
    await waitFor(() => expect(listed(api).length).toBe(1));

    await userEvent.click(await screen.findByRole("button", { name: /Add .* to favorites/ }));
    await waitFor(() => expect(calls.setFavorite).toEqual([["1", true]]));
  });

  it("does not open the trial when the bookmark is clicked", async () => {
    // The card is itself a click target, so the toggle has to stop the event.
    const api = fakeApi();
    const { state } = makeState([]);
    renderWithState(api, state);
    await waitFor(() => expect(listed(api).length).toBe(1));

    await userEvent.click(await screen.findByRole("button", { name: /Add .* to favorites/ }));
    expect(screen.queryByText("Back to all trials")).toBeNull();
  });
});

describe("the state tabs under failure and transition", () => {
  const stateWith = (overrides: Record<string, unknown>) => ({
    listFavoriteIds: vi.fn(async () => ["11"]),
    listRegisteredIds: vi.fn(async () => []),
    setFavorite: vi.fn(async () => undefined),
    setRegistered: vi.fn(async () => undefined),
    getPreferences: vi.fn(async () => ({})),
    savePreferences: vi.fn(async () => undefined),
    resetPreferences: vi.fn(async () => undefined),
    ...overrides,
  });

  const renderWith = (api: ReturnType<typeof fakeApi>, state: unknown) => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    return render(
      <QueryClientProvider client={queryClient}>
        <TrialMatches
          apiClient={api.client}
          queryClient={queryClient}
          patientInfo={{ disease: "multiple myeloma" }}
          state={state as never}
        />
      </QueryClientProvider>,
    );
  };

  it("returns to the first page when a state tab is opened", async () => {
    // `queryFilters.type` is undefined for the default tab AND for both
    // state tabs, and JSON.stringify drops undefined keys — so all three
    // hashed identically and the page never reset. A reader on page 2 asked
    // for page 2 of their bookmarks.
    const api = fakeApi({ count: 3, itemsTotalCount: 25 });
    renderWith(api, stateWith({}));
    await waitFor(() => expect(listed(api).length).toBe(1));

    await userEvent.click(await screen.findByRole("button", { name: "2" }));
    await waitFor(() => expect(listed(api).length).toBe(2));
    expect(listed(api)[1].params.page).toBe("2");

    await userEvent.click(await screen.findByRole("button", { name: /^Favorites/ }));
    await waitFor(() => expect(listed(api).length).toBe(3));
    expect(listed(api)[2].params.page).toBeUndefined();
  });

  it("says so when the saved ids cannot be loaded", async () => {
    // A rejected fetch also has no data, so treating that as "still
    // loading" left the tab on "Loading trials…" for ever — with the trials
    // query disabled, so even its own error branch could not speak.
    const api = fakeApi();
    const state = stateWith({
      listFavoriteIds: vi.fn(async () => {
        throw new Error("promop unreachable");
      }),
    });
    renderWith(api, state);
    await waitFor(() => expect(listed(api).length).toBe(1));

    await userEvent.click(await screen.findByRole("button", { name: /^Favorites/ }));
    await screen.findByText(/Couldn't load your saved trials/);
    expect(screen.queryByText("Loading trials…")).toBeNull();
  });

  it("never paints another tab's rows under a state tab", async () => {
    // The window `waitingForIds` did not cover: once the ids arrive it goes
    // false in the same render that changes the query key, and
    // `keepPreviousData` hands back the PREVIOUS key's rows. The eligible
    // list was painted under the Favorites heading — and on a second visit,
    // with the ids already cached, from the very first render.
    const api = fakeApi({ results: [trial(1)] });
    renderWith(api, stateWith({ listFavoriteIds: vi.fn(async () => ["99"]) }));
    await screen.findByText("Trial 1");

    let leaked = false;
    const watch = setInterval(() => {
      const onFavorites = screen
        .queryByRole("button", { name: /^Favorites/ })
        ?.getAttribute("aria-current");
      if (onFavorites === "true" && screen.queryByText("Trial 1")) leaked = true;
    }, 2);

    await userEvent.click(screen.getByRole("button", { name: /^Favorites/ }));
    await waitFor(() => expect(listed(api).length).toBe(2));
    await new Promise((r) => setTimeout(r, 40));
    clearInterval(watch);

    expect(leaked).toBe(false);
  });

  it("says so when a bookmark cannot be saved", async () => {
    // The star is painted from the server's list, so a rejected PATCH
    // leaves it exactly where it was — indistinguishable from a click that
    // never registered.
    const api = fakeApi();
    const state = stateWith({
      listFavoriteIds: vi.fn(async () => []),
      setFavorite: vi.fn(async () => {
        throw new Error("nope");
      }),
    });
    renderWith(api, state);
    await waitFor(() => expect(listed(api).length).toBe(1));

    await userEvent.click(await screen.findByRole("button", { name: /Add .* to favorites/ }));
    await screen.findByRole("alert");
  });
});

describe("when the host takes the adapter away", () => {
  it("moves the reader to a tab that still exists", async () => {
    // On logout, or an adapter reconfiguration. Falling back only in the
    // lookup would list the default tab's trials while no tab in the bar is
    // marked current — the reader would be somewhere the UI cannot name.
    const api = fakeApi();
    const state = {
      listFavoriteIds: vi.fn(async () => ["1"]),
      listRegisteredIds: vi.fn(async () => []),
      setFavorite: vi.fn(async () => undefined),
      setRegistered: vi.fn(async () => undefined),
      getPreferences: vi.fn(async () => ({})),
      savePreferences: vi.fn(async () => undefined),
      resetPreferences: vi.fn(async () => undefined),
    };
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    const ui = (withState: boolean) => (
      <QueryClientProvider client={queryClient}>
        <TrialMatches
          apiClient={api.client}
          queryClient={queryClient}
          patientInfo={{ disease: "multiple myeloma" }}
          state={withState ? (state as never) : undefined}
        />
      </QueryClientProvider>
    );
    const view = render(ui(true));
    await userEvent.click(await screen.findByRole("button", { name: /^Favorites/ }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^Favorites/ })).toHaveAttribute(
        "aria-current",
        "true",
      ),
    );

    view.rerender(ui(false));

    expect(screen.queryByRole("button", { name: /^Favorites/ })).toBeNull();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /^Eligible/ })).toHaveAttribute(
        "aria-current",
        "true",
      ),
    );
  });
});

describe("when a patient has saved more than the server will filter by", () => {
  it("says so instead of sending a request that is refused", async () => {
    // EXACT caps `trial_ids` at 500 — every id joins an `IN (...)`. Sending
    // 501 means the tab never loads while its badge reports 501 saved, so
    // the reader sees a count and an empty list with no explanation.
    const api = fakeApi();
    const many = Array.from({ length: 501 }, (_, i) => String(i + 1));
    const state = {
      listFavoriteIds: vi.fn(async () => many),
      listRegisteredIds: vi.fn(async () => []),
      setFavorite: vi.fn(async () => undefined),
      setRegistered: vi.fn(async () => undefined),
      getPreferences: vi.fn(async () => ({})),
      savePreferences: vi.fn(async () => undefined),
      resetPreferences: vi.fn(async () => undefined),
    };
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    render(
      <QueryClientProvider client={queryClient}>
        <TrialMatches
          apiClient={api.client}
          queryClient={queryClient}
          patientInfo={{ disease: "multiple myeloma" }}
          state={state as never}
        />
      </QueryClientProvider>,
    );
    await waitFor(() => expect(listed(api).length).toBe(1));

    await userEvent.click(await screen.findByRole("button", { name: /^Favorites/ }));
    await screen.findByText(/can show at most 500 at a time/);
    // And no request went out carrying the oversized list.
    expect(listed(api).length).toBe(1);
  });
});

describe("counts while a state tab is active", () => {
  const state = () => ({
    listFavoriteIds: vi.fn(async () => ["1"]),
    listRegisteredIds: vi.fn(async () => []),
    setFavorite: vi.fn(async () => undefined),
    setRegistered: vi.fn(async () => undefined),
    getPreferences: vi.fn(async () => ({})),
    savePreferences: vi.fn(async () => undefined),
    resetPreferences: vi.fn(async () => undefined),
  });

  const renderIt = (api: ReturnType<typeof fakeApi>, adapter: unknown) => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    return render(
      <QueryClientProvider client={queryClient}>
        <TrialMatches
          apiClient={api.client}
          queryClient={queryClient}
          patientInfo={{ disease: "multiple myeloma" }}
          state={adapter as never}
        />
      </QueryClientProvider>,
    );
  };

  it("does not label the match tabs with counts from a narrowed response", async () => {
    // Those counts came back from a request filtered to the saved ids, so
    // they describe the bookmarks. On the Eligible / Fully matched /
    // Potential badges they would read as the corpus — "Fully matched, 1"
    // for a reader with one bookmarked eligible trial and many matching.
    const api = fakeApi({ tabCounts: { eligible: 1, potential: 0 } });
    renderIt(api, state());
    await screen.findByRole("button", { name: "Fully matched, 1 trials" });

    await userEvent.click(screen.getByRole("button", { name: /^Favorites/ }));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: /Fully matched, / })).toBeNull(),
    );
    // The state tab's own count still comes from the adapter.
    await screen.findByRole("button", { name: "Favorites, 1 trials" });
  });

  it("runs no matcher query behind a failed saved-ids read", async () => {
    const api = fakeApi();
    const adapter = {
      ...state(),
      listFavoriteIds: vi.fn(async () => {
        throw new Error("promop unreachable");
      }),
    };
    renderIt(api, adapter);
    await waitFor(() => expect(listed(api).length).toBe(1));

    await userEvent.click(await screen.findByRole("button", { name: /^Favorites/ }));
    await screen.findByText(/Couldn't load your saved trials/);
    await new Promise((r) => setTimeout(r, 40));
    // Still just the first tab's request: nothing ran behind the error.
    expect(listed(api).length).toBe(1);
  });
});

describe("two more ways the state tabs could contradict themselves", () => {
  const adapterWith = (overrides: Record<string, unknown>) => ({
    listFavoriteIds: vi.fn(async () => []),
    listRegisteredIds: vi.fn(async () => []),
    setFavorite: vi.fn(async () => undefined),
    setRegistered: vi.fn(async () => undefined),
    getPreferences: vi.fn(async () => ({})),
    savePreferences: vi.fn(async () => undefined),
    resetPreferences: vi.fn(async () => undefined),
    ...overrides,
  });

  const renderIt = (api: ReturnType<typeof fakeApi>, adapter: unknown) => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    return render(
      <QueryClientProvider client={queryClient}>
        <TrialMatches
          apiClient={api.client}
          queryClient={queryClient}
          patientInfo={{ disease: "multiple myeloma" }}
          state={adapter as never}
        />
      </QueryClientProvider>,
    );
  };

  it("does not also say 'No trials found' over the cap message", async () => {
    // Two answers to one question: the tab explains it cannot show that
    // many, and then reports that there are none.
    const api = fakeApi();
    const many = Array.from({ length: 501 }, (_, i) => String(i + 1));
    renderIt(api, adapterWith({ listFavoriteIds: vi.fn(async () => many) }));
    await userEvent.click(await screen.findByRole("button", { name: /^Favorites/ }));
    await screen.findByText(/can show at most 500 at a time/);
    expect(screen.queryByText("No trials found")).toBeNull();
  });

  it("explains why bookmarking is unavailable on the ordinary tabs", async () => {
    // The star is painted from the favorites list, so a failed read hides
    // every one of them — on every tab — and the reader would conclude the
    // feature had been taken away.
    const api = fakeApi();
    renderIt(
      api,
      adapterWith({
        listFavoriteIds: vi.fn(async () => {
          throw new Error("promop unreachable");
        }),
      }),
    );
    await waitFor(() => expect(listed(api).length).toBe(1));
    // Still on the default tab.
    await screen.findByText(/bookmarking is unavailable/);
    expect(screen.queryByRole("button", { name: /to favorites$/ })).toBeNull();
  });
});

describe("two patients who look alike", () => {
  it("does not serve one reader the other's bookmarks", async () => {
    // The inline payload a host sends can be minimal — `{disease}` and
    // nothing else — so two different people can produce the identical
    // string. Keyed on that alone, the second reader is served the first
    // one's saved trials for as long as they stay fresh.
    const api = fakeApi();
    const byPerson: Record<string, string[]> = { "1": ["11"], "2": ["22"] };
    let current = "1";
    const adapter = {
      listFavoriteIds: vi.fn(async () => byPerson[current]),
      listRegisteredIds: vi.fn(async () => []),
      setFavorite: vi.fn(async () => undefined),
      setRegistered: vi.fn(async () => undefined),
      getPreferences: vi.fn(async () => ({})),
      savePreferences: vi.fn(async () => undefined),
      resetPreferences: vi.fn(async () => undefined),
    };
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    const ui = (personId: string) => (
      <QueryClientProvider client={queryClient}>
        <TrialMatches
          apiClient={api.client}
          queryClient={queryClient}
          // Identical for both readers, which is the point.
          patientInfo={{ disease: "multiple myeloma" }}
          personId={personId}
          state={adapter as never}
        />
      </QueryClientProvider>
    );
    const view = render(ui("1"));
    await screen.findByRole("button", { name: "Favorites, 1 trials" });

    current = "2";
    view.rerender(ui("2"));
    await waitFor(() => expect(adapter.listFavoriteIds).toHaveBeenCalledTimes(2));

    await userEvent.click(await screen.findByRole("button", { name: /^Favorites/ }));
    await waitFor(() => expect(listed(api).length).toBeGreaterThan(1));
    const last = listed(api)[listed(api).length - 1];
    expect((last.body as { trial_ids: string[] }).trial_ids).toEqual(["22"]);
  });
});

describe("the failure messages stand alone", () => {
  const renderWith = (
    api: ReturnType<typeof fakeApi>,
    adapter: unknown,
    initialFilters?: Record<string, unknown>,
  ) => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    return render(
      <QueryClientProvider client={queryClient}>
        <TrialMatches
          apiClient={api.client}
          queryClient={queryClient}
          patientInfo={{ disease: "multiple myeloma" }}
          initialFilters={initialFilters as never}
          state={
            {
              listRegisteredIds: async () => [],
              setFavorite: async () => undefined,
              setRegistered: async () => undefined,
              getPreferences: async () => ({}),
              savePreferences: async () => undefined,
              resetPreferences: async () => undefined,
              ...(adapter as object),
            } as never
          }
        />
      </QueryClientProvider>,
    );
  };

  // The trigger: the state tab falls back to the default tab's query key,
  // and when THAT key has no cached data a disabled query reports
  // `isPlaceholderData` for ever. Mounting on another tab leaves it empty.
  it("shows no loading line beside a failed saved-ids read", async () => {
    const api = fakeApi();
    renderWith(
      api,
      {
        listFavoriteIds: async () => {
          throw new Error("promop unreachable");
        },
      },
      { type: "eligible" },
    );
    await waitFor(() => expect(listed(api).length).toBe(1));

    await userEvent.click(await screen.findByRole("button", { name: /^Favorites/ }));
    await screen.findByText(/Couldn't load your saved trials/);
    await new Promise((r) => setTimeout(r, 40));
    expect(screen.queryByText("Loading trials…")).toBeNull();
  });

  it("shows no loading line beside the over-the-cap message", async () => {
    const api = fakeApi();
    const many = Array.from({ length: 501 }, (_, i) => String(i + 1));
    renderWith(api, { listFavoriteIds: async () => many }, { type: "eligible" });
    await waitFor(() => expect(listed(api).length).toBe(1));

    await userEvent.click(await screen.findByRole("button", { name: /^Favorites/ }));
    await screen.findByText(/can show at most 500 at a time/);
    await new Promise((r) => setTimeout(r, 40));
    expect(screen.queryByText("Loading trials…")).toBeNull();
  });
});
