/** Export CSV — the button, and what it asks the server for.
 *
 *  The file has to be the answer to the question on screen. Everything that
 *  can go wrong here is the export and the list disagreeing: a different tab,
 *  a dropped filter, the bookmarks ignored.
 */
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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
    expect(button).toHaveAttribute("title", expect.stringContaining("needs a patient"));
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
