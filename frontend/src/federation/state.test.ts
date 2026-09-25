import { describe, expect, it, vi } from "vitest";
import type { AxiosInstance } from "axios";

import { canEditFields, createPromopState } from "./state";

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

describe("the statuses a patient must not overwrite", () => {
  const clientFor = (byStatus: Record<string, string[]>) => {
    const get = vi.fn((_url: string, config?: { params?: Record<string, string> }) =>
      Promise.resolve({
        data: { trial_ids: byStatus[config?.params?.status ?? ""] ?? [], count: 0 },
      }),
    );
    return { get } as unknown as AxiosInstance & { get: ReturnType<typeof vi.fn> };
  };

  it("asks for entered and completed separately, because ?status= takes one", async () => {
    const client = clientFor({ entered: ["1"], completed: ["2"] });
    const map = await adapter(client).listAdvancedEnrollments();
    expect(map).toEqual({ "1": "entered", "2": "completed" });
    expect(client.get.mock.calls.map((c) => c[1].params.status).sort()).toEqual([
      "completed",
      "entered",
    ]);
  });

  it("keeps the two apart", async () => {
    // They are not interchangeable on screen: one says the patient is
    // taking part, the other that they took part.
    const client = clientFor({ entered: [], completed: ["7"] });
    expect(await adapter(client).listAdvancedEnrollments()).toEqual({
      "7": "completed",
    });
  });

  it("is empty when the patient has no advanced enrollment", async () => {
    // `{}` and not a rejection: this decides whether a WRITING control is
    // drawn, and "no advanced rows" is a real answer, not a failure.
    const client = clientFor({});
    expect(await adapter(client).listAdvancedEnrollments()).toEqual({});
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
      { params: { person_id: "9001" }, headers: {} },
    );
  });

  it("resets through its own call, not by saving an empty object", async () => {
    // Not because a partial update merges key by key — it does not, which is
    // what promop#1201 had to say out loud — but because a body that omits
    // `preferences` leaves the stored object untouched. `savePreferences({})`
    // sends the field carrying `{}` and WOULD clear; the separate call is so
    // "reset" is one request the caller cannot get subtly wrong.
    const client = fakeClient();
    await adapter(client).resetPreferences();
    expect(client.patch).toHaveBeenCalledWith(
      "/api/v1/trial-search-preferences/reset/",
      {},
      { params: { person_id: "9001" }, headers: {} },
    );
  });

  it("builds the version from updated_at, because a list carries no ETag", async () => {
    // One header cannot describe a collection, which is why promop#1312 made
    // the entity-tag a value the body already carries — quoted verbatim.
    const client = fakeClient({
      results: [{ preferences: { phase: "PHASE3" }, updated_at: "2026-09-14T13:46:38.625960Z" }],
    });
    expect(await adapter(client).preferenceVersioning!.read()).toEqual({
      filters: { phase: "PHASE3" },
      version: '"2026-09-14T13:46:38.625960Z"',
    });
  });

  it("says 'cannot describe' for a row that carries no updated_at", async () => {
    // This is where the third state is born, and collapsing it into `null`
    // told the transport "no row" — which sent `If-None-Match: *` at a row
    // that exists, was refused, re-read the same answer and was refused
    // again, so saved filters stopped working for that patient permanently.
    const client = fakeClient({ results: [{ preferences: { phase: "PHASE3" } }] });
    expect(await adapter(client).preferenceVersioning!.read()).toEqual({
      filters: { phase: "PHASE3" },
      version: undefined,
    });
  });

  it("reports no version when there is no row", async () => {
    // Distinct from "a row whose version I have not read": the first is
    // `If-None-Match: *`, the second cannot be written safely at all.
    expect(await adapter(fakeClient({ results: [] })).preferenceVersioning!.read()).toEqual({
      filters: {},
      version: null,
    });
  });

  it("sends the precondition it was given", async () => {
    const client = fakeClient();
    const versioning = adapter(client).preferenceVersioning!;

    await versioning.write({ phase: "PHASE3" }, { kind: "ifMatch", version: '"v1"' });
    expect(client.patch.mock.calls[0][2].headers).toEqual({ "If-Match": '"v1"' });

    await versioning.write({ phase: "PHASE3" }, { kind: "ifNoneMatch" });
    expect(client.patch.mock.calls[1][2].headers).toEqual({ "If-None-Match": "*" });

    await versioning.clear({ kind: "ifMatch", version: '"v2"' });
    expect(client.patch.mock.calls[2][2].headers).toEqual({ "If-Match": '"v2"' });
  });

  it("turns a 412 into something the caller can act on", async () => {
    // A refusal is the mechanism working, not a transport failure, and the
    // current tag comes back so the retry needs no extra round trip.
    const client = fakeClient();
    client.patch.mockRejectedValueOnce({
      response: {
        status: 412,
        headers: { etag: '"from-header"' },
        data: { etag: '"from-body"', error: "preferences have changed since they were read" },
      },
    });
    const versioning = adapter(client).preferenceVersioning!;

    await expect(
      versioning.write({ phase: "PHASE3" }, { kind: "ifMatch", version: '"stale"' }),
    ).rejects.toMatchObject({
      isPreconditionFailed: true,
      // The body over the header: it is the one that survives a proxy which
      // strips headers, and it is null exactly when there is no row.
      version: '"from-body"',
    });
  });

  it("lets a rejection that is not an object through unchanged", async () => {
    // This function is the sole error gateway for four call sites, and
    // reading `.response` off a null rejection would replace the real
    // failure with a TypeError the caller cannot act on.
    const client = fakeClient();
    client.patch.mockRejectedValueOnce(null);
    await expect(
      adapter(client).preferenceVersioning!.write({}, { kind: "none" }),
    ).rejects.toBeNull();
  });

  it("lets a failure that is not a 412 through unchanged", async () => {
    // A 500 or a dropped connection is not a precondition answer, and
    // dressing it as one would have the caller retry a broken transport.
    const client = fakeClient();
    const boom = { response: { status: 500 }, message: "boom" };
    client.patch.mockRejectedValueOnce(boom);
    await expect(
      adapter(client).preferenceVersioning!.write({}, { kind: "none" }),
    ).rejects.toBe(boom);
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

describe("asking what may be edited", () => {
  it("asks for this patient, not for the deployment", async () => {
    // Without `person_id` the endpoint answers "could someone write this",
    // which is a different question: an analyst has read-only access to every
    // patient and would be shown a box whose every save is refused.
    const client = fakeClient({ hemoglobin_g_dl: { kind: "direct", writable: true } });
    const fields = await adapter(client).getWritableFields!();
    expect(client.get).toHaveBeenCalledWith(
      "/api/v1/patient-records/writable-fields/",
      { params: { person_id: "9001" } },
    );
    expect(fields.hemoglobin_g_dl.writable).toBe(true);
  });

  it("reads an empty body as no fields rather than undefined", async () => {
    const client = fakeClient(null);
    expect(await adapter(client).getWritableFields!()).toEqual({});
  });
});

describe("writing", () => {
  const patching = (data: unknown) => {
    const patch = vi.fn().mockResolvedValue({ data });
    return { patch } as unknown as AxiosInstance & { patch: ReturnType<typeof vi.fn> };
  };
  const one = async (
    client: AxiosInstance,
    field: string,
    value: unknown,
  ) => (await adapter(client).setPatientFields!({ [field]: value }))[field];

  it("patches the record with just the fields it was given", async () => {
    const client = patching({ hemoglobin_g_dl: 12 });
    await adapter(client).setPatientFields!({ hemoglobin_g_dl: 12 });
    expect(client.patch).toHaveBeenCalledWith(
      "/api/v1/patient-records/9001/",
      { hemoglobin_g_dl: 12 },
    );
  });

  it("sends the person in the path only, not twice", async () => {
    // The endpoint reads the person from the path and nothing from the query
    // string, so a `person_id` param would encode the same id a second time
    // with only one of the two consulted.
    const client = patching({ hemoglobin_g_dl: 12 });
    await adapter(client).setPatientFields!({ hemoglobin_g_dl: 12 });
    expect(client.patch.mock.calls[0][2]).toBeUndefined();
  });

  it("sends several fields as one request", async () => {
    // Every write re-derives the projection and rescores the match, so a
    // reader filling two gaps should not pay for two of each.
    const client = patching({ hemoglobin_g_dl: 12, platelet_count: 200 });
    await adapter(client).setPatientFields!({
      hemoglobin_g_dl: 12,
      platelet_count: 200,
    });
    expect(client.patch).toHaveBeenCalledTimes(1);
    expect(client.patch.mock.calls[0][1]).toEqual({
      hemoglobin_g_dl: 12,
      platelet_count: 200,
    });
  });

  it("judges each field on its own", async () => {
    // One field the record kept differently says nothing about the others,
    // and a batch verdict would make a row of good writes look wrong.
    const client = patching({ hemoglobin_g_dl: 12, platelet_count: 999 });
    const outcomes = await adapter(client).setPatientFields!({
      hemoglobin_g_dl: 12,
      platelet_count: 200,
    });
    expect(outcomes.hemoglobin_g_dl.status).toBe("saved");
    expect(outcomes.platelet_count).toEqual({ status: "differs", value: 999 });
  });

  it("reports a value the server echoed back as saved", async () => {
    expect(await one(patching({ hemoglobin_g_dl: 12 }), "hemoglobin_g_dl", 12))
      .toEqual({ status: "saved", value: 12 });
  });

  it("accepts a decimal column's trailing scale", async () => {
    // `numeric(5,2)` answers "12.50" for the 12.5 that was sent. Compared as
    // text those differ, and the page would report a successful write as
    // dropped.
    expect((await one(patching({ hemoglobin_g_dl: "12.50" }), "hemoglobin_g_dl", 12.5)).status)
      .toBe("saved");
  });

  it("does not let the numeric comparison swallow false or an emptied field", async () => {
    // `Number(false)` and `Number("")` are both 0, so a careless numeric path
    // makes "no" equal "0" and an erased value equal zero — for a lab result,
    // the difference between not measured and measured as none.
    expect((await one(patching({ meets_crab: "0" }), "meets_crab", false)).status)
      .toBe("differs");
    expect((await one(patching({ hemoglobin_g_dl: 0 }), "hemoglobin_g_dl", "")).status)
      .toBe("differs");
  });

  it("catches the silent no-op: 200, unchanged value", async () => {
    // PROMOP drops a read-only or unknown field without complaint — the older
    // 405 was removed deliberately. Reported as success, the page would say
    // "saved" over a value the record never took.
    expect(await one(patching({ bmi: 24 }), "bmi", 31))
      .toEqual({ status: "differs", value: 24 });
  });

  it("says unconfirmed for a field the response does not mention, and only it", async () => {
    // Not a failure: the record is a projection re-derived from the OMOP facts
    // the write produced, so silence is not the same as refusal — and one
    // silent field says nothing about the others.
    const client = patching({ hemoglobin_g_dl: 12 });
    const outcomes = await adapter(client).setPatientFields!({
      hemoglobin_g_dl: 12,
      platelet_count: 200,
    });
    expect(outcomes.hemoglobin_g_dl.status).toBe("saved");
    expect(outcomes.platelet_count).toEqual({ status: "unconfirmed" });
  });

  it("compares JSON values structurally, not as [object Object]", async () => {
    // `String({...})` is "[object Object]" for every object alive, so a
    // stringified comparison calls any two of them equal. The record has 62
    // JSON columns and several are writable.
    expect((await one(
      patching({ sct_eligibility: { eligible: false } }),
      "sct_eligibility", { eligible: true },
    )).status).toBe("differs");
    // Key order differs: jsonb does not preserve the order it was given, so
    // insisting on it would report every JSON write as differing.
    expect((await one(
      patching({ sct_eligibility: { eligible: true, note: "x" } }),
      "sct_eligibility", { note: "x", eligible: true },
    )).status).toBe("saved");
  });

  it("compares a list of objects element by element", async () => {
    expect((await one(
      patching({ genetic_mutations: [{ gene: "TP53" }] }),
      "genetic_mutations", [{ gene: "BRCA1" }],
    )).status).toBe("differs");
  });

  it("accepts a list echoed back comma-joined, which is how it is stored", async () => {
    // `cytogenetic_markers` is a TextField whose serializer reads it back
    // `", ".join(...)` whatever the write sent.
    expect((await one(
      patching({ cytogenetic_markers: "del17p, t(4;14)" }),
      "cytogenetic_markers", ["del17p", "t(4;14)"],
    )).status).toBe("saved");
  });

  it("splits that echo the way PROMOP does — not on commas inside brackets", async () => {
    // `inv(3)(q21,q26)` is ONE marker.
    expect((await one(
      patching({ cytogenetic_markers: "del17p, inv(3)(q21,q26)" }),
      "cytogenetic_markers", ["del17p", "inv(3)(q21,q26)"],
    )).status).toBe("saved");
  });

  it("still notices when the joined echo holds different markers", async () => {
    expect((await one(
      patching({ cytogenetic_markers: "del17p, t(11;14)" }),
      "cytogenetic_markers", ["del17p", "t(4;14)"],
    )).status).toBe("differs");
  });

  it("accepts a single value echoed back wrapped in a list", async () => {
    expect((await one(
      patching({ cytogenetic_markers: ["del17p"] }), "cytogenetic_markers", "del17p",
    )).status).toBe("saved");
  });

  it("does not accept the reverse: a list sent, a scalar echoed", async () => {
    // Wrapping is normalised one way. A list that comes back as a scalar
    // means the record holds a different shape than was sent.
    expect((await one(
      patching({ cytogenetic_markers: "del17p, t(4;14)" }),
      "cytogenetic_markers", [["del17p", "t(4;14)"]],
    )).status).toBe("differs");
  });

  it("treats clearing a field as saved when the server agrees it is empty", async () => {
    expect((await one(patching({ hemoglobin_g_dl: null }), "hemoglobin_g_dl", null)).status)
      .toBe("saved");
  });
});

describe("the editing pair is all or nothing", () => {
  const base = createPromopState({ client: fakeClient(), personId: 1 });

  it("is on when a host implements both halves", () => {
    expect(canEditFields(base)).toBe(true);
  });

  it("is off for a host that implements neither", () => {
    const { getWritableFields, setPatientFields, ...rest } = base;
    expect(canEditFields(rest as typeof base)).toBe(false);
  });

  it("is off for a descriptor with no writer — pencils that lead nowhere", () => {
    const { setPatientFields, ...rest } = base;
    expect(canEditFields(rest as typeof base)).toBe(false);
  });

  it("is off for a writer with no descriptor — that is the guess we avoid", () => {
    const { getWritableFields, ...rest } = base;
    expect(canEditFields(rest as typeof base)).toBe(false);
  });

  it("is off when there is no adapter at all", () => {
    expect(canEditFields(undefined)).toBe(false);
  });
});

describe("the weights wizard flag", () => {
  it("reads it off the same row the filters come from", async () => {
    const client = fakeClient({
      results: [{ preferences: { sort: "distance" }, weights_wizard_offered: true }],
    });
    expect(await adapter(client).weightsWizard!.wasOffered()).toBe(true);
    expect(client.get).toHaveBeenCalledWith("/api/v1/trial-search-preferences/", {
      params: { person_id: "9001" },
    });
  });

  it("reads a patient with no row as never offered", async () => {
    // The commonest shape of the reader the wizard exists for. Not an error,
    // and not a reason to stay quiet.
    expect(await adapter(fakeClient({ results: [] })).weightsWizard!.wasOffered()).toBe(
      false,
    );
  });

  it("reads a row WITHOUT the column as a server that cannot remember", async () => {
    // The widget's gate asks whether the store has a `weightsWizard`, and
    // `createPromopState` always supplies one — so it cannot tell a PROMOP
    // that has shipped the column from one that has not. Behind it,
    // `record()` PATCHes an unknown field, DRF answers 200, nothing is
    // stored, and the reader is asked again on every visit for ever: the
    // exact outcome the gate exists to prevent.
    //
    // The row is the only thing that can say. An existing row with the key
    // ABSENT is therefore answered "already offered" — the deployment asks a
    // patient at most once, on the visit that creates their row, instead of
    // every time.
    expect(
      await adapter(fakeClient({ results: [{ preferences: {} }] })).weightsWizard!
        .wasOffered(),
    ).toBe(true);
  });

  it("still reads an explicit false as never offered", async () => {
    // The distinction the line above turns on: absent is not false.
    expect(
      await adapter(
        fakeClient({ results: [{ preferences: {}, weights_wizard_offered: false }] }),
      ).weightsWizard!.wasOffered(),
    ).toBe(false);
  });

  it("reads the row off an unpaginated list too", async () => {
    // `readRow` accepts both shapes — `{results: [...]}` and a bare array —
    // because pagination is a deployment setting, not an API contract. The
    // bare branch had no test on either reader, and it now decides whether a
    // patient is ever asked.
    expect(
      await adapter(
        fakeClient([{ preferences: {}, weights_wizard_offered: true }]),
      ).weightsWizard!.wasOffered(),
    ).toBe(true);
    expect(
      await adapter(
        fakeClient([{ preferences: {}, weights_wizard_offered: false }]),
      ).weightsWizard!.wasOffered(),
    ).toBe(false);
    // And an empty one is a patient with no row, not a server without the
    // column: nothing to find the key in.
    expect(await adapter(fakeClient([])).weightsWizard!.wasOffered()).toBe(false);
  });

  it("writes it alone, and without a precondition", async () => {
    // Alone: the weights go through `savePreferences`, which keeps this
    // store's belief of what is saved true. Unconditional: an `If-Match`
    // would let a filter save racing the answer refuse it, and the column
    // takes one value, so there is nothing to disagree about.
    const client = fakeClient({ results: [] });
    await adapter(client).weightsWizard!.record();
    expect(client.patch).toHaveBeenCalledWith(
      "/api/v1/trial-search-preferences/upsert/",
      { weights_wizard_offered: true },
      { params: { person_id: "9001" } },
    );
  });
});
