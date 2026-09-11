import { describe, expect, it, vi } from "vitest";
import type { AxiosInstance } from "axios";

import { createPromopState } from "./state";

function fakeClient(data: unknown = {}) {
  const get = vi.fn().mockResolvedValue({ data });
  const patch = vi.fn().mockResolvedValue({ data: {} });
  return { get, patch } as unknown as AxiosInstance & {
    get: ReturnType<typeof vi.fn>;
    patch: ReturnType<typeof vi.fn>;
  };
}

const adapter = (client: AxiosInstance) =>
  createPromopState({ client, personId: 9001 });

describe("reading the ids", () => {
  it("asks for the bookmarked ones", async () => {
    const client = fakeClient({ trial_ids: ["1", "2"], count: 2 });
    expect(await adapter(client).listFavoriteIds()).toEqual(["1", "2"]);
    expect(client.get).toHaveBeenCalledWith("/api/v1/trial-enrollments/ids/", {
      params: { person_id: "9001", is_favorite: "true" },
    });
  });

  it("asks for the registered ones by status, not by a second endpoint", async () => {
    const client = fakeClient({ trial_ids: ["3"], count: 1 });
    expect(await adapter(client).listRegisteredIds()).toEqual(["3"]);
    expect(client.get).toHaveBeenCalledWith("/api/v1/trial-enrollments/ids/", {
      params: { person_id: "9001", status: "registered" },
    });
  });

  it("reads an absent list as empty rather than undefined", async () => {
    // The caller distinguishes `[]` (none) from "not loaded yet"; a missing
    // key must not be allowed to look like the latter.
    const client = fakeClient({});
    expect(await adapter(client).listFavoriteIds()).toEqual([]);
  });
});

describe("writing", () => {
  it("bookmarks through the upsert, which creates the row if needed", async () => {
    // A patient may bookmark a trial they were never enrolled in, and the
    // POST that would create an enrollment is closed to them — PATCH on this
    // action is the only route (promop#1142).
    const client = fakeClient();
    await adapter(client).setFavorite("7", true);
    expect(client.patch).toHaveBeenCalledWith(
      "/api/v1/trial-enrollments/upsert/",
      { trial_id: "7", is_favorite: true },
      { params: { person_id: "9001" } },
    );
  });

  it("un-bookmarks by sending false, not by deleting anything", async () => {
    const client = fakeClient();
    await adapter(client).setFavorite("7", false);
    expect(client.patch.mock.calls[0][1]).toEqual({
      trial_id: "7",
      is_favorite: false,
    });
  });

  it("registers interest as a status", async () => {
    const client = fakeClient();
    await adapter(client).setRegistered("7", true);
    expect(client.patch.mock.calls[0][1]).toEqual({
      trial_id: "7",
      status: "registered",
    });
  });

  it("withdraws rather than erasing the fact of having registered", async () => {
    // PROMOP's enum carries `withdrawn` precisely so the record survives
    // the patient changing their mind. Deleting the row, or flipping back
    // to `interested`, would lose that a registration ever happened.
    const client = fakeClient();
    await adapter(client).setRegistered("7", false);
    expect(client.patch.mock.calls[0][1]).toEqual({
      trial_id: "7",
      status: "withdrawn",
    });
  });

  it("sends only the field it is changing", async () => {
    // The server leaves untouched what it is not sent, so a bookmark must
    // not carry a status along with it and silently reset one.
    const client = fakeClient();
    await adapter(client).setFavorite("7", true);
    expect(Object.keys(client.patch.mock.calls[0][1] as object)).toEqual([
      "trial_id",
      "is_favorite",
    ]);
  });
});

describe("the saved filters", () => {
  it("reads the first row's payload", async () => {
    const client = fakeClient({ results: [{ preferences: { phase: "PHASE3" } }] });
    expect(await adapter(client).getPreferences()).toEqual({ phase: "PHASE3" });
  });

  it("reads a bare list response too", async () => {
    // The endpoint answers with a plain array today; a page wrapper later
    // should not silently return "no filters".
    const client = fakeClient([{ preferences: { phase: "PHASE3" } }]);
    expect(await adapter(client).getPreferences()).toEqual({ phase: "PHASE3" });
  });

  it("reads no row at all as no filters", async () => {
    expect(await adapter(fakeClient({ results: [] })).getPreferences()).toEqual({});
  });

  it("saves through upsert, which creates the row on first use", async () => {
    const client = fakeClient();
    await adapter(client).savePreferences({ phase: "PHASE3" });
    expect(client.patch).toHaveBeenCalledWith(
      "/api/v1/trial-search-preferences/upsert/",
      { preferences: { phase: "PHASE3" } },
      { params: { person_id: "9001" } },
    );
  });

  it("resets through its own call, not by saving an empty object", async () => {
    // A partial update merges, so saving `{}` would leave every key in place
    // — the reset would appear to do nothing.
    const client = fakeClient();
    await adapter(client).resetPreferences();
    expect(client.patch).toHaveBeenCalledWith(
      "/api/v1/trial-search-preferences/reset/",
      {},
      { params: { person_id: "9001" } },
    );
  });
});

describe("the base path", () => {
  it("can be moved for a host that mounts the API elsewhere", async () => {
    const client = fakeClient({ trial_ids: [] });
    const state = createPromopState({ client, personId: 5, basePath: "/promop" });
    await state.listFavoriteIds();
    expect(client.get.mock.calls[0][0]).toBe("/promop/trial-enrollments/ids/");
  });
});
