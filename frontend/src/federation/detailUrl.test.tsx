// Who owns the address bar while a trial is open.
//
// The remote used to answer that alone: it pushed a history entry with no
// URL, so the browser's back button returned to the list instead of leaving
// the host page — and a reload had nothing to restore from, because the
// address never said which trial was being read (#552).
//
// A host with a route for a trial can take it over now. The two arrangements
// have to stay apart: if both push, the back button starts needing two
// presses, and if neither does, back leaves the page.

import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fakeApi, renderTrialMatches, trial, trialDetail } from "../test/renderTrialMatches";

const listOf = (...ids: number[]) =>
  fakeApi({ count: ids.length, itemsTotalCount: ids.length, results: ids.map((id) => trial(id)) });

const openFirst = async () => {
  const cards = await screen.findAllByRole("button", { name: "View Trial" });
  await userEvent.click(cards[0]);
};

const pushes = () => vi.spyOn(window.history, "pushState");

afterEach(() => {
  vi.restoreAllMocks();
});

describe("when the host has no route for a trial", () => {
  it("still pushes its own entry, so back returns to the list", async () => {
    const api = listOf(1);
    const pushed = pushes();
    renderTrialMatches(api);
    await openFirst();

    expect(await screen.findByText("Back to all trials")).toBeInTheDocument();
    expect(pushed).toHaveBeenCalledWith({ exactTrialDetail: 1 }, "");
  });
});

describe("when the host owns the URL", () => {
  it("opens the trial it was mounted with, without the list first", async () => {
    // The reload path. `/curehub/trials/2` mounts this with nothing loaded
    // and the detail page has to stand on its own — it asks for everything
    // it draws by id, which is what makes that possible.
    const api = listOf(1, 2);
    api.setDetail(trialDetail(2, { briefTitle: "The one being read" }));
    renderTrialMatches(api, { trialId: 2, onTrialIdChange: vi.fn() });

    expect(await screen.findByText("The one being read")).toBeInTheDocument();
    expect(screen.queryAllByRole("button", { name: "View Trial" })).toHaveLength(0);
  });

  it("takes a string, because a URL segment is one", async () => {
    const api = listOf(7);
    api.setDetail(trialDetail(7, { briefTitle: "Read from the address bar" }));
    renderTrialMatches(api, { trialId: "7", onTrialIdChange: vi.fn() });

    expect(await screen.findByText("Read from the address bar")).toBeInTheDocument();
  });

  it("asks the host to change the URL, and does not move history itself", async () => {
    const onTrialIdChange = vi.fn();
    const api = listOf(1);
    const pushed = pushes();
    renderTrialMatches(api, { trialId: null, onTrialIdChange });
    await openFirst();

    expect(onTrialIdChange).toHaveBeenCalledWith(1);
    expect(pushed).not.toHaveBeenCalled();
    // And nothing opens until the host says so: the remote renders what it
    // is told, which is still the list.
    expect(screen.queryByText("Back to all trials")).toBeNull();
  });

  it("asks for the list on the way back, rather than walking history", async () => {
    // `history.back()` here would consume an entry this remote never added
    // — the reader would leave the host page instead of returning to the
    // list.
    const onTrialIdChange = vi.fn();
    const back = vi.spyOn(window.history, "back");
    const api = listOf(3);
    api.setDetail(trialDetail(3));
    renderTrialMatches(api, { trialId: 3, onTrialIdChange });

    await userEvent.click(await screen.findByText("Back to all trials"));

    expect(onTrialIdChange).toHaveBeenCalledWith(null);
    expect(back).not.toHaveBeenCalled();
  });

  it("closes when the host's own back button clears the id", async () => {
    const api = listOf(4);
    api.setDetail(trialDetail(4));
    const view = renderTrialMatches(api, { trialId: 4, onTrialIdChange: vi.fn() });
    expect(await screen.findByText("Back to all trials")).toBeInTheDocument();

    view.setProps({ trialId: null });

    await waitFor(() => expect(screen.queryByText("Back to all trials")).toBeNull());
    expect(await screen.findAllByRole("button", { name: "View Trial" })).toHaveLength(1);
  });

  it("does not clear the address it was just handed", async () => {
    // The patient-switch reset runs on mount too, and asking the host to
    // clear there would undo the restore a reload just did — the reader
    // would land on `/trials/5` and be bounced to the list.
    const onTrialIdChange = vi.fn();
    const api = listOf(5);
    api.setDetail(trialDetail(5));
    renderTrialMatches(api, { trialId: 5, onTrialIdChange });

    expect(await screen.findByText("Back to all trials")).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 50));
    expect(onTrialIdChange).not.toHaveBeenCalled();
  });

  it("does clear it when the patient actually changes", async () => {
    // The other half: a trial opened for one person must not stay open, or
    // in the URL, for the next.
    const onTrialIdChange = vi.fn();
    const api = listOf(6);
    api.setDetail(trialDetail(6));
    const view = renderTrialMatches(api, {
      patientInfo: { person_id: 9009, disease: "multiple myeloma" },
      trialId: 6,
      onTrialIdChange,
    });
    expect(await screen.findByText("Back to all trials")).toBeInTheDocument();

    view.setProps({ patientInfo: { person_id: 9010, disease: "multiple myeloma" } });

    await waitFor(() => expect(onTrialIdChange).toHaveBeenCalledWith(null));
  });

  it("keeps a trial the host supplies FOR the new patient", async () => {
    // Both in one commit: a link from one patient's trial URL straight to
    // another's. The reset above must not read the new id as the old one's
    // leftover and ask the host to drop the navigation it just made.
    const onTrialIdChange = vi.fn();
    const api = listOf(6);
    api.setDetail(trialDetail(8, { briefTitle: "Opened for the next patient" }));
    const view = renderTrialMatches(api, {
      patientInfo: { person_id: 9009, disease: "multiple myeloma" },
      trialId: 6,
      onTrialIdChange,
    });
    expect(await screen.findByText("Back to all trials")).toBeInTheDocument();

    view.setProps({
      patientInfo: { person_id: 9010, disease: "multiple myeloma" },
      trialId: 8,
    });

    expect(await screen.findByText("Opened for the next patient")).toBeInTheDocument();
    await new Promise((r) => setTimeout(r, 50));
    expect(onTrialIdChange).not.toHaveBeenCalled();
  });

  it("keeps the trial when the host's profile lands after the mount", async () => {
    // The shape that made this worth checking: a host renders the remote
    // with `personId` while its own profile fetch is still in the air, then
    // hands the payload over. Same patient — but the handle those two spell
    // is different, and keyed on it the reader is bounced off the page a
    // reload had just restored.
    const onTrialIdChange = vi.fn();
    const api = listOf(9);
    api.setDetail(trialDetail(9, { briefTitle: "Still being read" }));
    const view = renderTrialMatches(api, {
      patientInfo: null,
      personId: "9009",
      trialId: 9,
      onTrialIdChange,
    });
    expect(await screen.findByText("Still being read")).toBeInTheDocument();

    view.setProps({ patientInfo: { person_id: 9009, disease: "multiple myeloma" } });

    await new Promise((r) => setTimeout(r, 50));
    expect(onTrialIdChange).not.toHaveBeenCalled();
    expect(screen.getByText("Still being read")).toBeInTheDocument();
  });

  it("keeps it when a payload that names nobody is merely re-read", async () => {
    // The host re-reads its profile after every inline edit (#555). With no
    // id in the payload there is nothing to compare but the whole thing, and
    // a changed age would otherwise read as a changed patient.
    const onTrialIdChange = vi.fn();
    const api = listOf(10);
    api.setDetail(trialDetail(10, { briefTitle: "Unnamed but unchanged" }));
    const view = renderTrialMatches(api, {
      patientInfo: { disease: "multiple myeloma", patient_age: 50 },
      trialId: 10,
      onTrialIdChange,
    });
    expect(await screen.findByText("Unnamed but unchanged")).toBeInTheDocument();

    view.setProps({ patientInfo: { disease: "multiple myeloma", patient_age: 51 } });

    await new Promise((r) => setTimeout(r, 50));
    expect(onTrialIdChange).not.toHaveBeenCalled();
  });

  it("keeps it when a payload that named nobody starts naming someone", async () => {
    // A host that mounts with the little it has — a disease, to get the
    // right corpus — and fills the payload in when its own read lands. The
    // patient did not change; there was simply nothing to compare by before,
    // and "unnameable, then named" says nothing about whether it is the same
    // person. Dropping the reader's address on a maybe is the wrong way to
    // be wrong.
    const onTrialIdChange = vi.fn();
    const api = listOf(14);
    api.setDetail(trialDetail(14, { briefTitle: "Held through the refinement" }));
    const view = renderTrialMatches(api, {
      patientInfo: { disease: "multiple myeloma" },
      trialId: 14,
      onTrialIdChange,
    });
    expect(await screen.findByText("Held through the refinement")).toBeInTheDocument();

    view.setProps({
      patientInfo: { person_id: 9009, disease: "multiple myeloma", patient_age: 50 },
    });

    await new Promise((r) => setTimeout(r, 50));
    expect(onTrialIdChange).not.toHaveBeenCalled();
  });

  it("clears it when personId moves under a steady payload", async () => {
    // The two props name different things — which patient the server
    // matches, and which record a write is PATCHed into — so either moving
    // is a move. Watching only the payload's id would hold a trial open
    // across a host that switches accounts beneath it.
    const onTrialIdChange = vi.fn();
    const api = listOf(15);
    api.setDetail(trialDetail(15));
    const view = renderTrialMatches(api, {
      patientInfo: { person_id: 9009, disease: "multiple myeloma" },
      personId: "acct-1",
      trialId: 15,
      onTrialIdChange,
    });
    expect(await screen.findByText("Back to all trials")).toBeInTheDocument();

    view.setProps({ personId: "acct-2" });

    await waitFor(() => expect(onTrialIdChange).toHaveBeenCalledWith(null));
  });

  it("works from the graph, not only from a card", async () => {
    // The graph opens a trial too, and it is its own code path.
    const onTrialIdChange = vi.fn();
    const onTrialSelect = vi.fn();
    const api = listOf(11);
    renderTrialMatches(api, { trialId: null, onTrialIdChange, onTrialSelect });
    await userEvent.click(await screen.findByRole("button", { name: /Explore Trials/i }));
    // Two controls carry the trial's name — the node in the graph and its
    // row in the list beneath it. Either is the graph's own path; the first
    // is the node.
    const nodes = await screen.findAllByRole("button", { name: /Trial 11/ });
    await userEvent.click(nodes[0]);

    expect(onTrialIdChange).toHaveBeenCalledWith(11);
    expect(onTrialSelect).toHaveBeenCalledWith(expect.objectContaining({ trialId: 11 }));
  });

  it("still tells a host that only wants to know", async () => {
    // `onTrialSelect` carries the whole row and predates all of this; it
    // must keep firing for hosts that use it to log or to cross-link.
    const onTrialSelect = vi.fn();
    const api = listOf(12);
    renderTrialMatches(api, { trialId: null, onTrialIdChange: vi.fn(), onTrialSelect });
    await openFirst();

    expect(onTrialSelect).toHaveBeenCalledWith(expect.objectContaining({ trialId: 12 }));
  });

  it("keeps the reader out of a dead end when the host wires only half of it", async () => {
    // `trialId` with no `onTrialIdChange` is nowhere to report a change to.
    // Taken as controlled, the back button would be a no-op and the reader
    // would be stuck on the detail page; so it reads as uncontrolled, which
    // works, and says so once.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const api = listOf(13);
    renderTrialMatches(api, { trialId: null });
    await openFirst();

    expect(await screen.findByText("Back to all trials")).toBeInTheDocument();
    expect(warn).toHaveBeenCalledWith(expect.stringContaining("without `onTrialIdChange`"));
    warn.mockRestore();
  });

  it("lands on a page with a way back when the id names nothing", async () => {
    // `/curehub/trials/nonsense`. The detail request answers 404 and the
    // page says so — with its back link, which is the difference between an
    // honest dead end and an empty frame.
    const api = listOf(1);
    api.failNextWith(404);
    renderTrialMatches(api, { trialId: "nonsense", onTrialIdChange: vi.fn() });

    expect(await screen.findByText("Back to all trials")).toBeInTheDocument();
    expect(await screen.findByText(/could not find that trial/i)).toBeInTheDocument();
  });
});
