// The per-user state seam.
//
// EXACT holds no per-user trial state and is not going to: it is stateless
// about patients by design. Bookmarks, registered interest and saved filters
// live in PROMOP. But this remote runs in two hosts that reach PROMOP
// differently — HealthTree PHR mints an OAuth token, CB runs the promop apps
// in-process behind its own endpoint — so the component takes an interface
// and the host supplies the transport.
//
// Everything here is optional. A host that passes no `state` gets the list,
// the filters and the detail page exactly as before; what disappears is the
// Favorites and Registered tabs and the bookmark control, because those
// cannot work without somewhere to keep the answer. Rendering them against
// nothing would be a button that forgets.

import type { AxiosInstance } from "axios";

import type { FilterState } from "./types";
import type { WritableFields } from "./writable";

/** Trial ids, as PROMOP stores them — strings, because they are opaque keys
 *  to it. EXACT's `trial_ids` filter accepts numeric strings. */
export type TrialId = string;

/** The most ids EXACT will accept in one `trial_ids` filter.
 *
 *  The server's number, mirrored here so the UI can say something useful
 *  instead of sending a request it knows will be refused. Each id becomes
 *  part of an `IN (...)`, which is why there is a cap at all (EXACT #419).
 */
export const MAX_TRIAL_IDS = 500;

/** The enrollment statuses a patient must not be able to overwrite from
 *  here. Set by a study team, not by the patient. */
export type AdvancedStatus = "entered" | "completed";

export interface TrialStateAdapter {
  /** The patient's bookmarked trial ids. */
  listFavoriteIds(): Promise<TrialId[]>;
  /** Bookmark or un-bookmark one trial. */
  setFavorite(trialId: TrialId, isFavorite: boolean): Promise<void>;
  /** The trials the patient has registered interest in. */
  listRegisteredIds(): Promise<TrialId[]>;
  /** Register (or withdraw) interest in one trial. */
  setRegistered(trialId: TrialId, registered: boolean): Promise<void>;
  /** The trials whose enrollment a study team has already moved past
   *  "registered", by status.
   *
   *  Required, not optional, because what depends on it is not a nicety:
   *  `listRegisteredIds` asks for `status=registered` exactly, so a patient
   *  a coordinator has advanced to `entered` reads back as NOT registered —
   *  and a control that then offers "I'm Interested" writes `registered`
   *  over the advanced status when clicked. An adapter that cannot answer
   *  this cannot safely be given the register control. */
  listAdvancedEnrollments(): Promise<Record<TrialId, AdvancedStatus>>;
  /** The patient's saved search filters, or `{}` when they have none. */
  getPreferences(): Promise<FilterState>;
  /** Store the filters. */
  savePreferences(filters: FilterState): Promise<void>;
  /** Clear them. Deliberately not `savePreferences({})`: the server merges
   *  a partial update, so "reset" has to be its own call. */
  resetPreferences(): Promise<void>;

  /** Which patient attributes this caller may edit for this patient, and how.
   *
   *  Asked rather than derived: EXACT names the attribute a row is about but
   *  cannot know whether PROMOP will accept a write to it — that depends on a
   *  reviewed concept set, on the field's kind, and on who is asking. See
   *  `writable.ts`.
   *
   *  Optional, and paired with `setPatientField`: a host that implements
   *  neither gets the read-only detail page it has today. Implementing only
   *  one is refused by `canEditFields` rather than half-honoured — a
   *  descriptor with no writer draws pencils that lead nowhere, and a writer
   *  with no descriptor has to guess what is writable, which is the guess this
   *  whole seam exists to avoid. */
  getWritableFields?(): Promise<WritableFields>;
  /** Write one attribute. See `WriteOutcome` for why this reports three
   *  states rather than resolving or rejecting. */
  setPatientField?(field: string, value: unknown): Promise<WriteOutcome>;
}

/** What the record showed after a write.
 *
 *  Three states, because PROMOP's `PATCH /patient-records/{id}/` answers 200
 *  for a field it did not write: unknown and read-only fields are dropped
 *  silently (the older 405 was removed deliberately). A seam that reported
 *  that as success would let the page say "saved" over a value the record
 *  never took — the exact failure this phase is here to prevent.
 *
 *  `differs` is deliberately NOT called "rejected", because most of the time
 *  it is not. The record is a projection re-derived from the OMOP facts the
 *  write produced, and the derivation canonicalises on the way back: free
 *  light chains return in mg/L whatever unit they were entered in, WBC units
 *  are normalised, a date comes back as a datetime, and clearing a field may
 *  echo `null` where `""` was sent. All of those are successful writes whose
 *  stored value is not character-for-character what was posted. `differs`
 *  means re-read the record, not tell the reader they failed.
 *
 *  `unconfirmed` means the response did not mention the field at all, which
 *  is rare — the endpoint returns the whole serialized record — but is not a
 *  failure either.
 *
 *  None of the three covers a REFUSED write: the call rejects for those.
 *  403 for a caller without write access, 404 for an unknown person, 400 for
 *  a value the serializer will not take (too many decimal places, an
 *  unrecognised choice). Every caller needs a catch. */
export type WriteOutcome =
  | { status: "saved"; value: unknown }
  | { status: "differs"; value: unknown }
  | { status: "unconfirmed" };

/** Both halves, or neither. Mirrors `canPersistFilters`. */
export function canEditFields(
  adapter: TrialStateAdapter | undefined,
): adapter is TrialStateAdapter &
  Required<Pick<TrialStateAdapter, "getWritableFields" | "setPatientField">> {
  return Boolean(adapter?.getWritableFields && adapter?.setPatientField);
}

interface PromopStateArgs {
  /** Axios instance the host has already authenticated against PROMOP.
   *  HT mints an OAuth token for it; CB points it at its own endpoint over
   *  the in-process plugin. */
  client: AxiosInstance;
  /** Whose state this is. */
  personId: string | number;
  /** Base path, for a host that mounts the API elsewhere. */
  basePath?: string;
}

/** The default adapter, speaking PROMOP's REST API (promop#1142).
 *
 *  A host with a plain PROMOP client can use this directly; one that reaches
 *  the same data another way implements `TrialStateAdapter` instead. */
export function createPromopState({
  client,
  personId,
  basePath = "/api/v1",
}: PromopStateArgs): TrialStateAdapter {
  const person = String(personId);
  const enrollments = `${basePath}/trial-enrollments`;
  const preferences = `${basePath}/trial-search-preferences`;
  const records = `${basePath}/patient-records`;

  const ids = async (params: Record<string, string>): Promise<TrialId[]> => {
    const response = await client.get<{ trial_ids: TrialId[]; count: number }>(
      `${enrollments}/ids/`,
      { params: { person_id: person, ...params } },
    );
    return response.data.trial_ids ?? [];
  };

  // One endpoint sets either field; what is not sent is not touched
  // (promop#1142). PATCH rather than POST because a session-authenticated
  // patient is granted safe methods and PATCH only.
  const upsert = (trialId: TrialId, body: Record<string, unknown>) =>
    client
      .patch(`${enrollments}/upsert/`, { trial_id: trialId, ...body }, {
        params: { person_id: person },
      })
      .then(() => undefined);

  return {
    listFavoriteIds: () => ids({ is_favorite: "true" }),
    setFavorite: (trialId, isFavorite) => upsert(trialId, { is_favorite: isFavorite }),
    // `registered` exactly, which means a patient a coordinator has moved
    // to `entered` drops off the Registered tab — at the moment they most
    // want to see the trial. Whether the label should cover the later
    // states is a product question, filed as EXACT #434; answering it also
    // needs PROMOP's `?status=` to accept more than one value.
    listRegisteredIds: () => ids({ status: "registered" }),
    // Two requests because `?status=` takes one value. Ids only, like the
    // others — the status is the map's value, so the UI can say which of
    // the two it is rather than guessing.
    listAdvancedEnrollments: async () => {
      const [entered, completed] = await Promise.all([
        ids({ status: "entered" }),
        ids({ status: "completed" }),
      ]);
      const out: Record<TrialId, AdvancedStatus> = {};
      for (const id of entered) out[id] = "entered";
      // Completed last: a row can only be one status, but if the two reads
      // straddle a change, the later state is the better answer.
      for (const id of completed) out[id] = "completed";
      return out;
    },
    // Withdrawing is a status of its own rather than a deletion: the row is
    // the record that the patient was once interested, and PROMOP's enum
    // has `withdrawn` precisely so that fact survives.
    setRegistered: (trialId, registered) =>
      upsert(trialId, { status: registered ? "registered" : "withdrawn" }),

    getPreferences: async () => {
      const response = await client.get<{ results?: { preferences?: FilterState }[] }>(
        `${preferences}/`,
        { params: { person_id: person } },
      );
      const rows = response.data?.results ?? (response.data as unknown as
        { preferences?: FilterState }[]);
      const first = Array.isArray(rows) ? rows[0] : undefined;
      return first?.preferences ?? {};
    },
    savePreferences: (filters) =>
      client
        .patch(`${preferences}/upsert/`, { preferences: filters }, {
          params: { person_id: person },
        })
        .then(() => undefined),
    resetPreferences: () =>
      client
        .patch(`${preferences}/reset/`, {}, { params: { person_id: person } })
        .then(() => undefined),

    // `person_id` is not optional here even though the endpoint accepts its
    // absence. Without it the answer describes the deployment — "could
    // someone write this" — and an analyst holding read-only access to every
    // patient would be handed a typeable box whose every save is refused.
    getWritableFields: async () => {
      const response = await client.get<WritableFields>(
        `${records}/writable-fields/`,
        { params: { person_id: person } },
      );
      return response.data ?? {};
    },

    // No `person_id` param here, unlike the calls above: this endpoint takes
    // the person in the path and reads nothing from the query string, so
    // sending it would encode the same id twice with only one of them
    // consulted.
    setPatientField: async (field, value) => {
      const response = await client.patch<Record<string, unknown>>(
        `${records}/${person}/`,
        { [field]: value },
      );
      const body = response.data;
      if (!body || typeof body !== "object" || !(field in body)) {
        return { status: "unconfirmed" };
      }
      const echoed = body[field];
      // Stringified, because the wire does not preserve the distinction: a
      // number sent as 12 comes back as "12" from a decimal column, and an
      // option sent as a string may return as one of several equal spellings
      // of the same value. Both are the value the record took.
      return sameValue(echoed, value)
        ? { status: "saved", value: echoed }
        : { status: "differs", value: echoed };
    },
  };
}

/** Loose equality for a value that has been through JSON and a database.
 *
 *  Element-wise for lists, position included. Wrapping is normalised in ONE
 *  direction only — see the function.
 *
 *  Objects compare structurally rather than through `String`, which renders
 *  every one of them as "[object Object]" and would have called any two of
 *  them equal. That is not academic: the record has 62 JSON columns, several
 *  of them writable — `genetic_mutations`, `sct_eligibility`,
 *  `active_malignancies`, the `genomics_*` family — and PROMOP handles some
 *  of those specially on the way in, so they are the fields most likely to
 *  come back as something other than what was sent. */
function sameValue(echoed: unknown, sent: unknown): boolean {
  // One direction only, and the arguments are named so it stays that way. A
  // single value written to a multi-valued column comes back wrapped, and
  // that is the same value. The reverse is not: a list that comes back as a
  // scalar means the record holds a different shape than was sent, which is
  // worth re-reading rather than confirming.
  if (Array.isArray(echoed) && !Array.isArray(sent) && echoed.length === 1) {
    return sameValue(echoed[0], sent);
  }
  if (Array.isArray(echoed) !== Array.isArray(sent)) return false;
  if (Array.isArray(echoed) && Array.isArray(sent)) {
    // Position included: the record echoes what it stored, and a reordered
    // list is not proof the write landed as sent.
    return echoed.length === sent.length
      && echoed.every((v, i) => sameValue(v, sent[i]));
  }
  if (echoed == null || sent == null) return echoed == null && sent == null;
  if (isObject(echoed) || isObject(sent)) {
    return isObject(echoed) && isObject(sent) && canonical(echoed) === canonical(sent);
  }
  if (isNumeric(echoed) && isNumeric(sent)) return Number(echoed) === Number(sent);
  return String(echoed) === String(sent);
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** JSON with the keys sorted, so two objects that differ only in key order
 *  compare equal — Postgres `jsonb` does not preserve the order they arrived
 *  in, so insisting on it would report every JSON write as differing. */
function canonical(v: unknown): string {
  if (Array.isArray(v)) return `[${v.map(canonical).join(",")}]`;
  if (isObject(v)) {
    const keys = Object.keys(v).sort();
    return `{${keys.map((k) => `${JSON.stringify(k)}:${canonical(v[k])}`).join(",")}}`;
  }
  return JSON.stringify(v ?? null);
}

/** A number, or a string that is one.
 *
 *  Booleans and empty strings are deliberately excluded even though `Number`
 *  is happy to convert them: `Number(false)` is 0 and `Number("")` is 0, so
 *  admitting either would make `false` equal to "0" and an emptied field equal
 *  to zero. For a lab value that is the difference between "not measured" and
 *  "measured as none". */
function isNumeric(v: unknown): boolean {
  if (typeof v === "number") return Number.isFinite(v);
  if (typeof v !== "string" || v.trim() === "") return false;
  return Number.isFinite(Number(v));
}
