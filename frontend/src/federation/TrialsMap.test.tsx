/** The map view, through `TrialMatches`.
 *
 *  What matters here is what the reader is told when the map cannot show
 *  everything, and that the toggle costs no request.
 */
import { describe, expect, it } from "vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { fakeApi, renderTrialMatches, trial } from "../test/renderTrialMatches";
import type { MapRenderProps } from "./TrialsMap";

const withPoint = (id: number, latitude: number, longitude: number, extra = {}) =>
  trial(id, {
    closestLocationGeoPoint: { latitude, longitude },
    location: [`Hospital ${id}`],
    ...extra,
  });

describe("the Map toggle", () => {
  it("costs no request, because the rows already carry their location", async () => {
    const api = fakeApi({ results: [withPoint(1, 51.5, -0.12)] });
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));

    await userEvent.click(screen.getByRole("button", { name: "Map" }));

    await screen.findByText("Where these trials are");
    expect(api.requests).toHaveLength(1);
  });

  it("keeps the list on screen, which is what makes the panel worth sticking", async () => {
    const api = fakeApi({ results: [withPoint(1, 51.5, -0.12)] });
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));

    await userEvent.click(screen.getByRole("button", { name: "Map" }));

    await screen.findByText("Where these trials are");
    expect(screen.getByRole("button", { name: "View Trial" })).toBeInTheDocument();
  });

  it("gathers the trials at one hospital under one place", async () => {
    const api = fakeApi({
      results: [withPoint(1, 51.5074, -0.1278), withPoint(2, 51.5074, -0.1278)],
    });
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));
    await userEvent.click(screen.getByRole("button", { name: "Map" }));

    await screen.findByText("One place on this page");
    await screen.findByText("2 trials");
  });

  it("says how many trials it cannot place", async () => {
    // A map showing fewer trials than the list lies by omission, and "no
    // location on file" is ordinary rather than exceptional.
    const api = fakeApi({
      results: [withPoint(1, 51.5, -0.12), trial(2), trial(3)],
    });
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));
    await userEvent.click(screen.getByRole("button", { name: "Map" }));

    await screen.findByText(/2 trials have no location on file/);
  });

  it("says there is nothing to place rather than drawing an empty map", async () => {
    const api = fakeApi({ results: [trial(1), trial(2)] });
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));
    await userEvent.click(screen.getByRole("button", { name: "Map" }));

    await screen.findByText(/nothing to place/);
  });

  it("opens a trial from a place", async () => {
    const api = fakeApi({ results: [withPoint(1, 51.5, -0.12)] });
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));
    await userEvent.click(screen.getByRole("button", { name: "Map" }));

    // Scoped to the map: the card in the list carries the same words, and a
    // query that matches both proves nothing about which one was clicked.
    const map = within(screen.getByLabelText("Trial locations"));
    await userEvent.click(await map.findByRole("button", { name: /Hospital 1/ }));
    await userEvent.click(await map.findByRole("button", { name: "Trial 1" }));

    await screen.findByText("Back to all trials");
  });

  it("hands the renderer the pins and the box that holds them", async () => {
    // The host draws the tiles; everything the remote knows about placement
    // goes across this seam.
    const seen: MapRenderProps[] = [];
    const renderMap = (props: MapRenderProps) => {
      seen.push(props);
      return <div data-testid="host-map" />;
    };
    const api = fakeApi({
      results: [withPoint(1, 51.5, -0.12), withPoint(2, 48.85, 2.35)],
    });
    renderTrialMatches(api, { renderMap });
    await waitFor(() => expect(api.listRequests().length).toBe(1));
    await userEvent.click(screen.getByRole("button", { name: "Map" }));

    await screen.findByTestId("host-map");
    const props = seen.at(-1)!;
    expect(props.markers).toHaveLength(2);
    expect(props.bounds!.north).toBeGreaterThan(props.bounds!.south);
  });

  it("says the places are a list when no renderer was given", async () => {
    // Not an apology for a broken map: the host declined to load one, and the
    // information a reader needs is the place, the distance and what runs
    // there — which is here either way.
    const api = fakeApi({ results: [withPoint(1, 51.5, -0.12)] });
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));
    await userEvent.click(screen.getByRole("button", { name: "Map" }));

    await screen.findByText(/needs a maps provider/);
  });
});

describe("a place left open while the rows change", () => {
  it("is not handed to the renderer once it is off the page", async () => {
    // The rows change under this panel — paging, a filter, a tab. A marker
    // object from the previous set would be handed to the host renderer while
    // no longer being among its markers, leaving a pin or a popup open over a
    // place that is not on the page.
    const seen: MapRenderProps[] = [];
    const renderMap = (props: MapRenderProps) => {
      seen.push(props);
      return <div data-testid="host-map" />;
    };
    const api = fakeApi({ results: [withPoint(1, 51.5, -0.12)] });
    renderTrialMatches(api, { renderMap });
    await waitFor(() => expect(api.listRequests().length).toBe(1));
    await userEvent.click(screen.getByRole("button", { name: "Map" }));

    const map = within(screen.getByLabelText("Trial locations"));
    await userEvent.click(await map.findByRole("button", { name: /Hospital 1/ }));
    await waitFor(() => expect(seen.at(-1)!.selected).not.toBeNull());

    // A different page of results, with the place gone.
    api.setResponse({ results: [withPoint(2, 48.85, 2.35)] });
    await userEvent.click(screen.getByRole("button", { name: /Filter Results|Filters \(/ }));
    await userEvent.type(await screen.findByLabelText("Title"), "dara");
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(1));

    await waitFor(() => {
      const last = seen.at(-1)!;
      expect(last.markers.map((m) => m.latitude)).toEqual([48.85]);
      expect(last.selected).toBeNull();
    });
  });
});
