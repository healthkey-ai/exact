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
  /** Clear them. Its own call rather than `savePreferences({})` because the
   *  two are different requests on the PROMOP side — `reset` empties the row,
   *  while `upsert` with `{}` is a partial update that leaves `preferences`
   *  untouched. */
  resetPreferences(): Promise<void>;
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
  };
}
