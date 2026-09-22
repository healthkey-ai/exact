/** Suitability Preferences — the four weights behind the Suitability Score.
 *
 *  What is worth pinning is the part CB does not have: here a save re-runs
 *  the search, so the list the reader is looking at is ranked by what they
 *  just chose. CB stores the weights and leaves the ranked list alone.
 */
import { describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { fakeApi, renderTrialMatches } from "../test/renderTrialMatches";

/** A preference store and nothing else — the seam a widget host fills. Its
 *  `savePreferences` is typed so the assertions can read what was written. */
const preferenceStore = (saved: Record<string, unknown> = {}) => ({
  getPreferences: vi.fn(async () => saved),
  savePreferences: vi.fn(async (_filters: Record<string, unknown>) => undefined),
  resetPreferences: vi.fn(async () => undefined),
});

/** A store that keeps the row, so a test can ask what the reader would get
 *  back rather than what one call happened to carry. `savePreferences` is
 *  handed the WHOLE row — the transport merges the edit over what it believes
 *  is stored and deletes the keys the reader cleared — so this replaces. */
const rowStore = () => {
  let row: Record<string, unknown> = {};
  return {
    read: () => row,
    getPreferences: vi.fn(async () => ({ ...row })),
    savePreferences: vi.fn(async (filters: Record<string, unknown>) => {
      row = { ...filters };
    }),
    resetPreferences: vi.fn(async () => {
      row = {};
    }),
  };
};

/** A store whose read is still in flight until the test lets it land, so a
 *  keystroke can land first — which is what makes `applied` false. */
const slowStore = (row: Record<string, unknown>) => {
  let land: () => void = () => {};
  const arrived = new Promise<void>((resolve) => {
    land = resolve;
  });
  return {
    land: () => land(),
    getPreferences: vi.fn(async () => {
      await arrived;
      return row;
    }),
    savePreferences: vi.fn(async (_filters: Record<string, unknown>) => undefined),
    resetPreferences: vi.fn(async () => undefined),
  };
};

const openDialog = async () => {
  await userEvent.click(await screen.findByRole("button", { name: /Suitability Preferences/ }));
  return screen.getByRole("dialog", { name: "Suitability Preferences" });
};

const field = (label: string) => screen.getByLabelText(label) as HTMLInputElement;

describe("the suitability preferences control", () => {
  it("sends the weights the reader chose, and re-ranks the list", async () => {
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));
    // Nothing on the wire until there is something to say: the server's own
    // default is 25 apiece, so sending them would be noise in the query key.
    expect(api.listRequests()[0].params.benefitWeight).toBeUndefined();

    await openDialog();
    await userEvent.clear(field("Benefit Weight"));
    await userEvent.type(field("Benefit Weight"), "60");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.listRequests().length).toBe(2));
    const params = api.listRequests()[1].params;
    expect(params.benefitWeight).toBe("60");
    // The three the reader left alone stay off the wire at their default.
    expect(params.riskWeight).toBeUndefined();
    expect(params.patientBurdenWeight).toBeUndefined();
    expect(params.distancePenaltyWeight).toBeUndefined();
  });

  it("saves all four, so the next mount ranks the same way", async () => {
    // Stored with the filters, in the same row. All four go, not just the one
    // that moved: the store merges, so a partial write would leave whatever
    // it held before standing beside the new value.
    const api = fakeApi();
    const store = preferenceStore();
    renderTrialMatches(api, { preferences: store });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    await openDialog();
    await userEvent.clear(field("Risk Weight"));
    await userEvent.type(field("Risk Weight"), "5");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(store.savePreferences).toHaveBeenCalled());
    const last = store.savePreferences.mock.calls.at(-1)![0];
    expect(last.riskWeight).toBe(5);
    expect(last.benefitWeight).toBe(25);
    expect(last.patientBurdenWeight).toBe(25);
    expect(last.distancePenaltyWeight).toBe(25);
  });

  it("applies what was stored, without putting it back on the wire", async () => {
    const api = fakeApi();
    const store = preferenceStore({ benefitWeight: 70, riskWeight: 10 });
    renderTrialMatches(api, { preferences: store });

    await waitFor(() => {
      const last = api.listRequests().at(-1);
      expect(last?.params.benefitWeight).toBe("70");
    });
    expect(api.listRequests().at(-1)!.params.riskWeight).toBe("10");
    // Stored at the default, so still nothing to say about the other two.
    expect(api.listRequests().at(-1)!.params.patientBurdenWeight).toBeUndefined();
  });

  it("is not a filter: the Filters badge does not count it", async () => {
    // They change the ORDER of the list and the number on each card, never
    // which trials are in it. Counted, the badge would claim a filter the
    // panel cannot show and Reset would offer to clear it.
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));

    await openDialog();
    await userEvent.clear(field("Distance Penalty Weight"));
    await userEvent.type(field("Distance Penalty Weight"), "0");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.listRequests().length).toBe(2));
    expect(screen.getByRole("button", { name: /^Filter Results$/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Filters \(/ })).toBeNull();
  });

  it("refuses a number the score cannot use, and says which field", async () => {
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));

    await openDialog();
    await userEvent.clear(field("Risk Weight"));
    await userEvent.type(field("Risk Weight"), "500");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Must be between 0 and 100");
    // Still open, still holding what was typed, and nothing was searched for.
    expect(screen.getByRole("dialog", { name: "Suitability Preferences" })).toBeInTheDocument();
    expect(api.listRequests().length).toBe(1);

    // And the complaint goes when the field it belongs to is touched.
    await userEvent.clear(field("Risk Weight"));
    await userEvent.type(field("Risk Weight"), "50");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("puts the four back with Restore defaults", async () => {
    // CB has no such affordance — its Reset drops local edits back to what
    // the server holds — and a reader who has lost track of what they changed
    // has no other way back to the score everyone else sees.
    const api = fakeApi();
    renderTrialMatches(api, { initialFilters: { benefitWeight: 90 } });
    await waitFor(() => expect(api.listRequests().length).toBe(1));
    expect(api.listRequests()[0].params.benefitWeight).toBe("90");

    await openDialog();
    expect(field("Benefit Weight").value).toBe("90");
    await userEvent.click(screen.getByRole("button", { name: "Restore defaults" }));
    expect(field("Benefit Weight").value).toBe("25");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.listRequests().length).toBe(2));
    expect(api.listRequests()[1].params.benefitWeight).toBeUndefined();
    expect(
      screen.getByRole("button", { name: /Suitability Preferences/ }).textContent,
    ).not.toMatch(/changed/);
  });

  it("does not re-run the search for a save that changes nothing", async () => {
    // All four are written to STORAGE every time, so that a weight returned
    // to 25 is stored as a decision rather than merged away. The query is the
    // other question: 25s and absences ask the server for exactly the same
    // list, and treating them as different states costs a second full matcher
    // run and throws the reader back to page 1.
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));

    await openDialog();
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Suitability Preferences" })).toBeNull(),
    );
    expect(api.listRequests().length).toBe(1);
  });

  it("survives Reset, which is a reset of the FILTERS", async () => {
    // The badge beside that button has never counted the weights, so a reader
    // clearing their filters is not told the score is about to go back to
    // 25/25/25/25 — and `savedFilters.reset()` empties the row, so it would
    // not come back on the next mount either.
    const api = fakeApi();
    const store = preferenceStore();
    renderTrialMatches(api, { preferences: store });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    await openDialog();
    await userEvent.clear(field("Risk Weight"));
    await userEvent.type(field("Risk Weight"), "80");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(api.listRequests().at(-1)!.params.riskWeight).toBe("80"),
    );

    // Reset is only offered when a FILTER is active, which is the shape the
    // clobbering needed: a reader with both a filter and a chosen score.
    await userEvent.click(screen.getByRole("button", { name: /Filter Results|Filters \(/ }));
    await userEvent.type(await screen.findByLabelText("Sponsor"), "Acme");
    await userEvent.click(await screen.findByRole("button", { name: /^Reset/ }));

    await waitFor(() => expect(store.resetPreferences).toHaveBeenCalled());
    expect(api.listRequests().at(-1)!.params.riskWeight).toBe("80");
    expect(
      screen.getByRole("button", { name: /Suitability Preferences/ }).textContent,
    ).toMatch(/changed/);
    // And written back, so the next mount still has them.
    await waitFor(() =>
      expect(store.savePreferences.mock.calls.at(-1)?.[0].riskWeight).toBe(80),
    );
  });

  it("keeps both halves when a filter is edited in the same breath", async () => {
    // The writer's debounce replaces what is queued rather than merging it,
    // and these are two savers writing disjoint halves. Whichever went second
    // used to retire the other: weights then a filter stored no weights, and a
    // filter CLEARED then weights dropped the tombstone, so the filter the
    // reader had just removed came back on the next mount.
    const api = fakeApi();
    const store = preferenceStore();
    renderTrialMatches(api, { preferences: store });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    await openDialog();
    await userEvent.clear(field("Risk Weight"));
    await userEvent.type(field("Risk Weight"), "40");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await userEvent.click(screen.getByRole("button", { name: /Filter Results|Filters \(/ }));
    await userEvent.type(await screen.findByLabelText("Sponsor"), "Acme");

    await waitFor(() => {
      const last = store.savePreferences.mock.calls.at(-1)?.[0];
      expect(last?.sponsor).toBe("Acme");
      expect(last?.riskWeight).toBe(40);
    });
  });

  it("does not bring back a filter the reader has just cleared", async () => {
    // The other direction of the same hazard, and the worse one. Clearing a
    // filter writes a TOMBSTONE — the key present and undefined — because a
    // merging store reads an absent key as "no opinion, keep what you have".
    // A weights save that carried only weights retired that tombstone, and
    // the cleared filter came back on the next mount, narrowing the list.
    const api = fakeApi();
    const store = rowStore();
    renderTrialMatches(api, { preferences: store });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    await userEvent.click(screen.getByRole("button", { name: /Filter Results|Filters \(/ }));
    const sponsor = await screen.findByLabelText("Sponsor");
    await userEvent.type(sponsor, "Acme");
    await waitFor(() => expect(store.read().sponsor).toBe("Acme"));
    await userEvent.clear(sponsor);

    await openDialog();
    await userEvent.clear(field("Risk Weight"));
    await userEvent.type(field("Risk Weight"), "40");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    // What the next mount would read back: the weights the reader chose, and
    // no sponsor — the filter they cleared stays cleared.
    await waitFor(() => expect(store.read().riskWeight).toBe(40));
    expect(store.read().sponsor).toBeUndefined();
  });

  it("closes when the host changes patient, rather than saving a stale draft", async () => {
    // The component stays mounted across the switch, so the numbers in hand
    // are the PREVIOUS reader's. Saved then, they land in the new patient's
    // row — and there is no answer to "which of these did you mean for whom".
    const api = fakeApi();
    const view = renderTrialMatches(api, { personId: "p1" });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));
    await openDialog();
    await userEvent.clear(field("Risk Weight"));
    await userEvent.type(field("Risk Weight"), "80");

    view.setProps({ personId: "p2" });

    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Suitability Preferences" })).toBeNull(),
    );
    // And nothing of the first patient's went out for the second.
    expect(api.listRequests().every((r) => r.params.riskWeight === undefined)).toBe(true);
  });

  it("asks for a number when a field is left empty", async () => {
    // A number input hands back "" for an empty box AND for "abc", and
    // neither is out of range — which is all CB's single message says.
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));

    await openDialog();
    await userEvent.clear(field("Benefit Weight"));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Enter a number between 0 and 100",
    );
    // The message is reachable from the field, not only as a one-off alert.
    expect(field("Benefit Weight")).toHaveAccessibleDescription(
      /Enter a number between 0 and 100/,
    );
  });

  it("closes on a save that goes through", async () => {
    const api = fakeApi();
    renderTrialMatches(api);
    await waitFor(() => expect(api.listRequests().length).toBe(1));

    await openDialog();
    await userEvent.clear(field("Risk Weight"));
    await userEvent.type(field("Risk Weight"), "30");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Suitability Preferences" })).toBeNull(),
    );
  });

  it("does not store weights the HOST mounted with", async () => {
    // `initialFilters` is the host's scope for this mount, not the reader's
    // standing preference. Stored, it would outlive the mount that asked for
    // it and follow the reader to hosts that never wanted it.
    const api = fakeApi();
    const store = rowStore();
    renderTrialMatches(api, { preferences: store, initialFilters: { riskWeight: 40 } });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    // Something the reader DOES own, to make a write happen at all.
    await userEvent.click(screen.getByRole("button", { name: /Filter Results|Filters \(/ }));
    await userEvent.type(await screen.findByLabelText("Sponsor"), "Acme");

    await waitFor(() => expect(store.read().sponsor).toBe("Acme"));
    expect(store.read().riskWeight).toBeUndefined();
  });

  it("keeps a weight chosen while a slow read was still in flight", async () => {
    // The saved set arrives late and is merged over the panel. A weight the
    // reader chose in that window would be reverted a second after they set
    // it — with their own write already on its way to the same row.
    const api = fakeApi();
    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    const store = {
      // The sponsor rides along as the PROOF that this row was applied: it is
      // a field the reader did not touch, so its arrival is the moment the
      // merge happened, and asserting before that would pass on any code.
      getPreferences: vi.fn(async () => {
        await held;
        return { riskWeight: 10, sponsor: "Stored" };
      }),
      savePreferences: vi.fn(async (_f: Record<string, unknown>) => undefined),
      resetPreferences: vi.fn(async () => undefined),
    };
    renderTrialMatches(api, { preferences: store });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));

    await openDialog();
    await userEvent.clear(field("Risk Weight"));
    await userEvent.type(field("Risk Weight"), "90");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.listRequests().at(-1)!.params.riskWeight).toBe("90"));

    release();

    // Wait for the row to actually land — the sponsor it carries is the sign —
    // and only then ask what became of the weight.
    await waitFor(() => expect(api.listRequests().at(-1)!.params.sponsor).toBe("Stored"));
    // The stored 10 does not win: it is what the row held BEFORE the reader
    // chose, and the choice is already on its way there.
    expect(api.listRequests().at(-1)!.params.riskWeight).toBe("90");
  });

  it("shows a stored fraction as it is, rather than rounding it into a change", async () => {
    // Storage, the sanitizer and the wire all take a fraction. Rounded here,
    // a reader who opened the dialog and pressed Save changed their score
    // without touching it.
    const api = fakeApi();
    renderTrialMatches(api, { preferences: preferenceStore({ riskWeight: 40.4 }) });
    await waitFor(() =>
      expect(api.listRequests().at(-1)!.params.riskWeight).toBe("40.4"),
    );

    await openDialog();

    expect(field("Risk Weight").value).toBe("40.4");
  });

  it("stays open while the same patient's payload is merely refreshed", async () => {
    // A host re-reading the profile hands over a new object for the same
    // person. Keyed on the payload, the draft in hand would be thrown away
    // for nothing.
    const api = fakeApi();
    // `externalId` is what EXACT's own payload names the patient; the host's
    // `person_id` is the other spelling seen in practice. Both must hold the
    // dialog open across a refresh, so this uses the camelCase one.
    const view = renderTrialMatches(api, {
      patientInfo: { externalId: "abc-123", disease: "Multiple Myeloma" },
    });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));
    await openDialog();
    await userEvent.clear(field("Risk Weight"));
    await userEvent.type(field("Risk Weight"), "60");

    view.setProps({
      patientInfo: { externalId: "abc-123", disease: "Multiple Myeloma", patientAge: 68 },
    });

    expect(
      await screen.findByRole("dialog", { name: "Suitability Preferences" }),
    ).toBeInTheDocument();
    expect(field("Risk Weight").value).toBe("60");
  });

  it("closes for an inline patient swap, even with no id to key on", async () => {
    // A payload with nothing stable in it: the whole thing stands in for the
    // identity, so a swap closes the dialog. Wrong about a refresh, right
    // about a swap — which is the safe direction.
    const api = fakeApi();
    const view = renderTrialMatches(api, { patientInfo: { disease: "Multiple Myeloma" } });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));
    await openDialog();

    view.setProps({ patientInfo: { disease: "Follicular Lymphoma" } });

    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "Suitability Preferences" })).toBeNull(),
    );
  });

  it("keeps the weights through a Reset that follows a patient switch", async () => {
    // Ownership of the PANEL's fields is per patient and cleared on the
    // switch. The weights are the reader's, and the session keeps them live —
    // so a Reset after a switch dropped them from the page and wrote nothing
    // back, which is the silent change this control must not make.
    const api = fakeApi();
    const store = rowStore();
    const view = renderTrialMatches(api, { preferences: store, personId: "p1" });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));
    await openDialog();
    await userEvent.clear(field("Risk Weight"));
    await userEvent.type(field("Risk Weight"), "80");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(api.listRequests().at(-1)!.params.riskWeight).toBe("80"));

    // The switch: the panel's ownership is cleared here, and the weights ride
    // on — live on the wire and on the trigger.
    view.setProps({ preferences: store, personId: "p2" });
    await waitFor(() => expect(api.listRequests().at(-1)!.params.riskWeight).toBe("80"));

    await userEvent.click(screen.getByRole("button", { name: /Filter Results|Filters \(/ }));
    await userEvent.type(await screen.findByLabelText("Sponsor"), "Acme");
    await userEvent.click(await screen.findByRole("button", { name: /^Reset/ }));

    await waitFor(() => expect(store.read().riskWeight).toBe(80));
    expect(api.listRequests().at(-1)!.params.riskWeight).toBe("80");
  });

  it("gives each mount its own field ids", async () => {
    // Two remotes on one page is a supported shape, and ids are global: with
    // one spelling between them, a label in the second dialog points at a
    // field in the first, and `aria-describedby` reads out its hint.
    const api = fakeApi();
    renderTrialMatches(api);
    await openDialog();
    const first = [...document.querySelectorAll(".exact-prefs__input")].map((i) => i.id);

    renderTrialMatches(fakeApi());
    const triggers = await screen.findAllByRole("button", { name: /Suitability Preferences/ });
    // `fireEvent`, not `userEvent`: the first dialog's scrim covers the page,
    // and pointer-events are exactly what this test is not about.
    fireEvent.click(triggers[triggers.length - 1]);

    const all = [...document.querySelectorAll(".exact-prefs__input")].map((i) => i.id);
    expect(all.length).toBe(8);
    expect(new Set(all).size).toBe(8);
    expect(first.every((id) => all.includes(id))).toBe(true);
  });

  it("is reached from the keyboard where it is seen: after the title, before the tabs", async () => {
    // It shares the title's line and the tab strip has the next one, so the
    // keyboard must meet it in that order too. CB gets this by rendering the
    // button twice and hiding one per breakpoint; one copy has to be written
    // where it is painted instead (WCAG 2.4.3).
    const api = fakeApi();
    renderTrialMatches(api);
    const trigger = await screen.findByRole("button", { name: /Suitability Preferences/ });
    const firstTab = screen.getByRole("button", { name: /^Eligible/ });

    // DOCUMENT_POSITION_FOLLOWING, twice: the title, then the trigger it
    // shares a line with, then the strip on the line below.
    const title = screen.getByRole("heading", { name: "Your Trials" });
    expect(title.compareDocumentPosition(trigger) & 4).toBe(4);
    expect(trigger.compareDocumentPosition(firstTab) & 4).toBe(4);

    trigger.focus();
    await userEvent.tab();

    expect(firstTab).toHaveFocus();
  });

  it("leaves the tab strip a row of its own", async () => {
    // The reason the trigger moved up here. Inside the head it was a flex
    // item that could shrink — `overflow-x` resolves its minimum size to
    // zero — and its bottom rule, the one the active tab's underline sits
    // on, stopped at the last tab instead of ruling the row. Two ways back
    // into that: the markup, and the strip's own `display`.
    const api = fakeApi();
    renderTrialMatches(api);
    await screen.findByRole("button", { name: /^Eligible/ });
    const head = document.querySelector(".exact-list__head")!;
    const tabs = document.querySelector(".exact-tabs")!;

    expect(head).toBeTruthy();
    expect(head.contains(tabs)).toBe(false);

    // The other way back in is the strip's own `display`, which jsdom cannot
    // see — that half is read off the sheet in `controlsRowCss.test.ts`.
  });

  it("says on the button that the score is not on its defaults", async () => {
    const api = fakeApi();
    renderTrialMatches(api, { initialFilters: { riskWeight: 40 } });

    expect(
      await screen.findByRole("button", { name: /Suitability Preferences changed/ }),
    ).toBeInTheDocument();
  });
});

describe("a weight the host seeded, against one the reader saved", () => {
  it("does not let the host's mount-time weight win over the stored one", async () => {
    // The overlay below the saved-filter read exists to protect a weight the
    // reader chose while that read was in flight. It used to read every
    // weight the ROW had just claimed — and the panel still holds the host's
    // `initialFilters` seed for those, so the host's number went over the
    // reader's saved one (#546).
    const api = fakeApi();
    const store = slowStore({ riskWeight: 70, sponsor: "Stored" });
    renderTrialMatches(api, {
      preferences: store,
      initialFilters: { riskWeight: 5 },
    });
    await waitFor(() => expect(store.getPreferences).toHaveBeenCalled());
    // Typed while the read is out, which is what makes it `applied: false`.
    await userEvent.click(
      await screen.findByRole("button", { name: /Filter Results|Filters \(/ }),
    );
    await userEvent.type(await screen.findByLabelText("Title"), "daratum");

    store.land();

    await waitFor(() =>
      expect(api.listRequests().at(-1)!.params.riskWeight).toBe("70"),
    );
  });

  it("does not store the host's weight as the reader's own", async () => {
    // Worse than the wrong ranking: one more keystroke and the row itself
    // holds the host's number, as though the reader had chosen it. It would
    // then follow them to a host that never asked for it.
    const api = fakeApi();
    const store = slowStore({ riskWeight: 70, sponsor: "Stored" });
    renderTrialMatches(api, {
      preferences: store,
      initialFilters: { riskWeight: 5 },
    });
    await waitFor(() => expect(store.getPreferences).toHaveBeenCalled());
    await userEvent.click(
      await screen.findByRole("button", { name: /Filter Results|Filters \(/ }),
    );
    await userEvent.type(await screen.findByLabelText("Title"), "daratum");
    store.land();
    await waitFor(() =>
      expect(api.listRequests().at(-1)!.params.riskWeight).toBe("70"),
    );

    // Counted first: the edit BEFORE the row landed has a save of its own,
    // and `toHaveBeenCalled()` would be satisfied by that one — leaving the
    // assertions to run over a write made before the bug could reach it.
    const before = store.savePreferences.mock.calls.length;

    await userEvent.type(screen.getByLabelText("Title"), "umab");

    await waitFor(() =>
      expect(store.savePreferences.mock.calls.length).toBeGreaterThan(before),
    );
    for (const [written] of store.savePreferences.mock.calls) {
      expect(written.riskWeight).not.toBe(5);
    }
  });
});

describe("Reset", () => {
  it("keeps the weights it kept, twice over", async () => {
    // Reset gives the panel back to the host and keeps the weights, which
    // means it has to keep OWNING them: ownership is what carries a weight
    // through the next Reset. Dropping it takes two steps to show — reset,
    // edit, reset — and then the weights leave both the page and the row,
    // which is the silent change the code above that line forbids.
    const api = fakeApi();
    const store = rowStore();
    renderTrialMatches(api, { preferences: store });
    await waitFor(() => expect(api.listRequests().length).toBeGreaterThan(0));
    await openDialog();
    await userEvent.clear(field("Risk Weight"));
    await userEvent.type(field("Risk Weight"), "70");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(store.read().riskWeight).toBe(70));

    // The dialog is done with; Reset lives in the filter panel behind it.
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await userEvent.click(
      await screen.findByRole("button", { name: /Filter Results|Filters \(/ }),
    );
    const filterThenReset = async (title: string) => {
      // Reset is disabled until something is filtered — the weights are not
      // filters and the badge beside it has never counted them.
      await userEvent.type(await screen.findByLabelText("Title"), title);
      await waitFor(() => expect(store.read().searchTitle).toBe(title));
      await userEvent.click(screen.getByRole("button", { name: "Reset filters" }));
    };
    await filterThenReset("daratum");
    await filterThenReset("carfilzomib");

    await waitFor(() => expect(store.read().riskWeight).toBe(70));
    await openDialog();
    expect(field("Risk Weight").value).toBe("70");
  });
});
