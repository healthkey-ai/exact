/** Export CSV — the button, and what it asks the server for.
 *
 *  The file has to be the answer to the question on screen. Everything that
 *  can go wrong here is the export and the list disagreeing: a different tab,
 *  a dropped filter, the bookmarks ignored.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { TrialMatches } from "./TrialMatches";

import { EXPORT_URL_LIFETIME_MS, exportFilename } from "./api";
import { fakeApi, fakeState, renderTrialMatches } from "../test/renderTrialMatches";

const exportRequests = (api: ReturnType<typeof fakeApi>) =>
  api.requests.filter((r) => r.url.startsWith("/trials/export/"));

describe("the Export CSV button", () => {
  let click: ReturnType<typeof vi.spyOn>;
  let created: string[];
  let revoked: string[];

  beforeEach(() => {
    // `shouldAdvanceTime` so `userEvent` still works against a fake clock.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    created = [];
    revoked = [];
    // jsdom implements neither, and the component is not interesting without
    // them: what it does with the blob IS the feature.
    (URL as unknown as Record<string, unknown>).createObjectURL = vi.fn(() => {
      const url = `blob:${created.length}`;
      created.push(url);
      return url;
    });
    (URL as unknown as Record<string, unknown>).revokeObjectURL = vi.fn((u: string) => {
      revoked.push(u);
    });
    click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  afterEach(() => {
    click.mockRestore();
    vi.useRealTimers();
  });

  const pressExport = async (api: ReturnType<typeof fakeApi>) => {
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
  };

  it("saves the file under the name the server gave it", async () => {
    // Parsed from the response, never reconstructed: the server dates the
    // file, and a second guess at the date here disagrees across midnight.
    const api = fakeApi();
    renderTrialMatches(api);
    await pressExport(api);

    await waitFor(() => expect(click).toHaveBeenCalled());
    const anchor = click.mock.instances[0] as HTMLAnchorElement;
    expect(anchor.download).toBe("trials-2026-09-11.csv");
    // Not revoked while the download may still be starting — Safari begins it
    // after the handler returns, and a URL revoked by then yields an empty
    // file.
    expect(revoked).toEqual([]);
    await vi.advanceTimersByTimeAsync(EXPORT_URL_LIFETIME_MS);
    // …but released in the end, or the blob is held for the life of the
    // document, and an export of a few thousand trials is not small.
    expect(revoked).toEqual(created);
  });

  it("asks for the filters and the tab the list is showing", async () => {
    const api = fakeApi();
    renderTrialMatches(api, { patientInfo: { disease: "multiple myeloma" } });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    await userEvent.click(screen.getByRole("button", { name: /Filter Results|Filters \(/ }));
    await userEvent.type(await screen.findByLabelText("Title"), "dara");
    await waitFor(() =>
      expect(api.listRequests().at(-1)?.params.searchTitle).toBe("dara"),
    );

    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => expect(exportRequests(api).length).toBe(1));
    const request = exportRequests(api)[0];
    expect(request.method).toBe("post");
    expect(request.params.searchTitle).toBe("dara");
    expect((request.body as { patient_info?: unknown }).patient_info).toEqual({
      disease: "multiple myeloma",
    });
  });

  it("exports the bookmarks, not the corpus, from the Favorites tab", async () => {
    const api = fakeApi();
    renderTrialMatches(api, { state: fakeState({ favorites: ["1"] }).adapter });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    await userEvent.click(await screen.findByRole("button", { name: /^Favorites/ }));
    await waitFor(() =>
      expect(api.listRequests().at(-1)?.body).toMatchObject({ trial_ids: ["1"] }),
    );

    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => expect(exportRequests(api).length).toBe(1));
    expect(exportRequests(api)[0].body).toMatchObject({ trial_ids: ["1"] });
  });

  it("says so when the file cannot be prepared", async () => {
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));
    api.failNextWith(500);

    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await screen.findByRole("alert");
    expect(click).not.toHaveBeenCalled();
  });
});

describe("exportFilename", () => {
  it("prefers the encoded form, and falls back rather than failing", () => {
    expect(
      exportFilename("attachment; filename=\"trials.csv\"; filename*=UTF-8''trials-2026-09-11.csv"),
    ).toBe("trials-2026-09-11.csv");
    expect(exportFilename('attachment; filename="only-plain.csv"')).toBe("only-plain.csv");
    // Cross-origin the header is readable only because EXACT exposes it; a
    // host that has not caught up still gets a file.
    expect(exportFilename(undefined)).toBe("trials.csv");
    expect(exportFilename("attachment; filename*=UTF-8''%E2%98%A0%%.csv")).toBe("trials.csv");
  });
});

describe("the Export CSV button on a state tab", () => {
  it("refuses while the saved ids are unavailable, rather than exporting the corpus", async () => {
    // `trialIds` is undefined in three ways here — loading, failed, over the
    // 500 cap — and the list refuses to search in all three. Exporting anyway
    // sends no ids, so the server answers with the whole matched corpus and
    // hands it over as a file the tab has labelled "Favorites". Same wrong-set
    // answer the server's `type=favorites` refusal exists to prevent, reached
    // from the other side.
    const api = fakeApi();
    const state = fakeState({
      overrides: {
        listFavoriteIds: vi.fn(async () => {
          throw new Error("promop unreachable");
        }),
      },
    });
    renderTrialMatches(api, { state: state.adapter });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    await userEvent.click(await screen.findByRole("button", { name: /^Favorites/ }));

    const button = await screen.findByRole("button", { name: "Export CSV" });
    await waitFor(() => expect(button).toBeDisabled());
    await userEvent.click(button);
    expect(api.requests.filter((r) => r.url.startsWith("/trials/export/"))).toHaveLength(0);
  });
});

describe("what the export counts as the same view", () => {
  // The export builds its own filters and its own key now, so nothing but a
  // test keeps them in step with the list's. Each of these fails against a
  // plausible slip: taking the filters from the reader's own state instead of
  // the list's drops the tab, and leaving either the tab or the saved ids out
  // of the key hands over a file for a view the reader has left.
  const exportRequests = (api: ReturnType<typeof fakeApi>) =>
    api.requests.filter((r) => r.url.startsWith("/trials/export/"));

  it("asks for the tab the LIST is on, not the one the host deep-linked to", async () => {
    // The tab and the reader's own filters are separate state, and after a
    // switch they disagree: `filters.type` still says what the host asked for
    // while the list has moved on. Built from the reader's filters instead of
    // the list's, the export keeps sending the deep-linked subset — a file
    // holding the potential trials, labelled as the whole tab.
    const api = fakeApi();
    renderTrialMatches(api, { initialFilters: { type: "potential" } });
    await waitFor(() => expect(api.listRequests().length).toBe(1));
    expect(api.listRequests()[0].params.type).toBe("potential");

    await userEvent.click(screen.getByRole("button", { name: /^Eligible/ }));
    await waitFor(() => expect(api.listRequests().length).toBe(2));
    expect(api.listRequests()[1].params.type).toBeUndefined();
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));

    await waitFor(() => expect(exportRequests(api).length).toBe(1));
    expect(exportRequests(api)[0].params.type).toBeUndefined();
  });

  it("drops a file whose tab the reader has left, on the tab alone", async () => {
    // Favorites and Registered hold the same trial here, so the ids in the
    // key are identical and so are the filters — both state tabs send no
    // `type`. The tab is the only thing that moves, which is what makes this
    // a test of the tab rather than of the ids.
    const api = fakeApi();
    const state = fakeState({ favorites: ["1"], registered: ["1"] });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderTrialMatches(api, { state: state.adapter });
    await userEvent.click(await screen.findByRole("button", { name: /^Favorites/ }));
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));
    const release = api.holdNextExport();
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => expect(exportRequests(api).length).toBe(1));

    await userEvent.click(await screen.findByRole("button", { name: /^Registered/ }));
    release();

    await waitFor(() => expect(screen.queryByText("Preparing…")).toBeNull());
    expect(click).not.toHaveBeenCalled();
    click.mockRestore();
  });

  it("drops a file whose saved-id set has moved under it", async () => {
    // Bookmarking while a Favorites export is in flight changes which trials
    // the file was supposed to be about.
    const api = fakeApi();
    const state = fakeState({ favorites: ["1"] });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    renderTrialMatches(api, { state: state.adapter });
    await userEvent.click(await screen.findByRole("button", { name: /^Favorites/ }));
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));
    const release = api.holdNextExport();
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => expect(exportRequests(api).length).toBe(1));

    await userEvent.click(
      (await screen.findAllByRole("button", { name: /to favorites$|from favorites$/ }))[0],
    );
    release();

    await waitFor(() => expect(screen.queryByText("Preparing…")).toBeNull());
    expect(click).not.toHaveBeenCalled();
    click.mockRestore();
  });
});

describe("the export failure message", () => {
  it("goes away when the view it described does", async () => {
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));
    api.failNextWith(500);

    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await screen.findByRole("alert");

    // Change the list under it.
    await userEvent.click(screen.getByRole("button", { name: /Filter Results|Filters \(/ }));
    await userEvent.type(await screen.findByLabelText("Title"), "dara");

    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });
});

describe("an export that did not finish", () => {
  it("says so instead of saving a file that looks complete", async () => {
    // The server's 200 went out before the first row, so a stream that died
    // halfway arrives as a success with a short file. Its last line is the
    // only thing that says otherwise — and this one goes to an appointment.
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    const api = fakeApi();
    api.truncateNextExport();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));

    await screen.findByText(/stopped partway/);
    expect(click).not.toHaveBeenCalled();
    click.mockRestore();
  });
});

describe("an export the reader navigated away from", () => {
  it("is dropped rather than handed over under a different view", async () => {
    // Otherwise the Favorites file arrives while the screen shows All Trials,
    // named after neither — or a failure is painted under a view nobody
    // exported.
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    const api = fakeApi();
    const release = api.holdNextExport();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));

    // The view moves while the export is still open.
    await userEvent.click(
      screen.getByRole("button", { name: /Filter Results|Filters \(/ }),
    );
    await userEvent.type(await screen.findByLabelText("Title"), "dara");
    await waitFor(() =>
      expect(api.listRequests().at(-1)?.params.searchTitle).toBe("dara"),
    );

    release();
    await waitFor(() => expect(screen.queryByText("Preparing…")).toBeNull());
    expect(click).not.toHaveBeenCalled();
    expect(screen.queryByRole("alert")).toBeNull();
    click.mockRestore();
  });
});

describe("the Export button with no patient at all", () => {
  it("is disabled, and says why", async () => {
    // A supported mode for the list. The remote never sends `?type=all`, so
    // every export in it is a guaranteed 400 surfaced as "please try again" —
    // advice that cannot work.
    const api = fakeApi();
    renderTrialMatches(api, { patientInfo: null });

    // No patient means no search either, so there is no request to wait for.
    const button = await screen.findByRole("button", { name: "Export CSV" });
    expect(button).toBeDisabled();
    expect(button).toHaveAccessibleDescription(expect.stringContaining("needs a patient"));
  });
});

describe("an export the server never finished saying anything about", () => {
  it("is refused too, because no marker at all is the commoner failure", async () => {
    // A proxy cutting the response, a dropped connection, a worker killed
    // mid-write: none of them leave the server alive to write its apology, so
    // a check for the FAILURE marker passes them through as complete files.
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    const api = fakeApi();
    api.cutNextExport();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));

    await screen.findByText(/stopped partway/);
    expect(click).not.toHaveBeenCalled();
    click.mockRestore();
  });
});

describe("an export cut inside a quoted field that contains the marker", () => {
  it("is refused, because the last LINE is not the last record", async () => {
    // `csv.writer` keeps a newline inside a quoted title, so a title holding
    // one followed by the completion marker makes the file's last physical
    // line look like a footer while its last record is a half-written row.
    // Whether a newline ends a record depends on the quote state it is in,
    // which is only decidable from the start of the file.
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    const api = fakeApi();
    api.cutNextExportInsideAQuotedMarker();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));

    await screen.findByText(/stopped partway/);
    expect(click).not.toHaveBeenCalled();
    click.mockRestore();
  });
});

describe("an export in flight when the patient changes", () => {
  it("is dropped, even though the filters never moved", async () => {
    // The filters, the tab and the ids are all identical across the switch, so
    // a view key built from those alone does not change — and the previous
    // patient's file is handed over under the new patient's name.
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => {});
    const api = fakeApi();
    const release = api.holdNextExport();
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    const ui = (personId: string) => (
      <QueryClientProvider client={queryClient}>
        <TrialMatches apiClient={api.client} queryClient={queryClient} personId={personId} />
      </QueryClientProvider>
    );

    const view = render(ui("p1"));
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));

    view.rerender(ui("p2"));
    release();

    await waitFor(() => expect(screen.queryByText("Preparing…")).toBeNull());
    expect(click).not.toHaveBeenCalled();
    click.mockRestore();
  });
});

describe("an export issued while the sort is still being walked", () => {
  // The list holds an arrow-key sort change back 250ms so that walking the
  // orders is one request, not three. The control does not hold anything
  // back: the segment the reader arrowed to is checked immediately. For that
  // quarter second the screen says one order and the rows are still the
  // other, and an export belongs to what the reader can see.
  let click: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    (URL as unknown as Record<string, unknown>).createObjectURL = vi.fn(() => "blob:x");
    (URL as unknown as Record<string, unknown>).revokeObjectURL = vi.fn();
    click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });

  afterEach(() => {
    click.mockRestore();
    vi.useRealTimers();
  });

  it("asks for the order the reader can see, not the one the list still has", async () => {
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));
    screen.getByRole("radio", { name: "Sort By Suitability Score" }).focus();

    await userEvent.keyboard("{ArrowRight}");
    // Inside the hold: the segment has moved, the list has not.
    expect(screen.getByRole("radio", { name: "Sort by Matching Score" })).toBeChecked();
    expect(api.listRequests().length).toBe(1);
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));

    await waitFor(() => expect(exportRequests(api).length).toBe(1));
    expect(exportRequests(api)[0].params.sort).toBe("matchScore");
    // Still inside the hold when the export went out — otherwise this passes
    // against the code it was written to fail against.
    expect(api.listRequests().length).toBe(1);
  });

  it("survives the reader walking on to another order while it is in flight", async () => {
    // The mirror of the case above, and the one a key built on the order gets
    // wrong from the other end: the file was asked for under the order that
    // was showing, and the next arrow key must not read as "the reader left
    // the view" and throw it away. Worse than losing the file, idle again
    // means enabled again — a second full-corpus export beside the first.
    const api = fakeApi();
    const release = api.holdNextExport();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() => expect(exportRequests(api).length).toBe(1));

    screen.getByRole("radio", { name: "Sort By Suitability Score" }).focus();
    await userEvent.keyboard("{ArrowRight}");

    // The button is still busy, so a second corpus-wide export cannot start.
    expect(screen.getByRole("button", { name: /Preparing/ })).toBeDisabled();
    release();

    await waitFor(() => expect(click).toHaveBeenCalled());
    expect(exportRequests(api).length).toBe(1);
  });

  it("takes a pointer-picked order immediately, because the list does too", async () => {
    // The hold is for the arrows only. A click is one deliberate choice, and
    // the premise of everything above is that it moves the list at once.
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));

    await userEvent.click(screen.getByRole("radio", { name: "Sort by Distance" }));

    await waitFor(() => expect(api.listRequests().length).toBe(2));
    expect(api.listRequests()[1].params.sort).toBe("distance");
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));

    await waitFor(() => expect(exportRequests(api).length).toBe(1));
    expect(exportRequests(api)[0].params.sort).toBe("distance");
  });

  it("is still handed over once the list catches up", async () => {
    // The hold landing moves the LIST's key. Read as a change of view, it
    // discards the file on arrival and puts the button back to idle — the
    // reader clicks Export and gets nothing, with nothing said.
    const api = fakeApi();
    const release = api.holdNextExport();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));
    screen.getByRole("radio", { name: "Sort By Suitability Score" }).focus();

    await userEvent.keyboard("{ArrowRight}");
    await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    // The hold expires while the export is in flight, and the list re-reads.
    await act(async () => {
      vi.advanceTimersByTime(300);
    });
    await waitFor(() => expect(api.listRequests().length).toBe(2));
    release();

    await waitFor(() => expect(click).toHaveBeenCalled());
  });
});
