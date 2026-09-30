// Saved search filters: where they are kept, and how writes are sequenced.
//
// The filter panel writes on every change. Two things make that harder than
// it looks, and CB earned both the hard way:
//
//   * Ticking two checkboxes in one frame issues two writes. Without
//     sequencing they can land out of order and the server keeps the older
//     one — the panel then shows a filter the backend does not have.
//   * Reset has to win. A save already on the wire when Reset is pressed
//     would otherwise complete afterwards and restore what was just cleared.
//
// So writes go through a queue: one request in flight, at most one write
// waiting behind it (a newer one replaces the waiting one — nobody needs the
// intermediate states), and a generation counter that retires anything from
// before a Reset. Debouncing sits in front of that to keep the common case —
// dragging a slider — down to one request.
//
// The transport is injected. With a `TrialStateAdapter` it is PROMOP; with no
// adapter at all it is `localStorage`, so the dev harness and any host that
// supplies no state still keep filters across a reload instead of losing the
// feature.

import { sameValue } from "./filters";
import { PreconditionFailed } from "./state";
import type { Precondition, TrialPreferenceStore } from "./state";
import { IdentityChanged, sameIdentity } from "./identityFingerprint";
import type { FilterState } from "./types";

/** The preference half, which is now its own interface: `TrialPreferenceStore`
 *  (a `TrialStateAdapter` is one, and so is a host that has nothing else).
 *
 *  Narrowed on purpose: the caller holds the adapter behind a ref (it can be
 *  rebuilt every render), so it passes three bound functions rather than the
 *  object — and asking for the whole interface would force a cast that claims
 *  more than is true. */
export type PreferenceMethods = TrialPreferenceStore;

/** How long to coalesce rapid edits. Long enough to swallow a slider drag,
 *  short enough that a deliberate change feels saved. */
export const FILTER_DEBOUNCE_MS = 400;

export interface PreferenceTransport {
  get(): Promise<FilterState>;
  save(filters: FilterState): Promise<void>;
  reset(): Promise<void>;
  /** Forget the cached ETag, because somebody wrote this row by another
   *  route.
   *
   *  The weights wizard records "this reader has been asked" as a column on
   *  the same row, through `upsert`, not through this transport. That bumps
   *  `updated_at` — which IS the tag — so the next save here quotes one the
   *  server has moved past and is refused. The refusal is survivable (re-read,
   *  re-apply, retry once) but it is two extra round trips on every save
   *  afterwards, and a second tab on the same patient can turn the retry into
   *  a dropped edit. Unknown is the truthful state and it costs one read.
   *
   *  Only the TAG. The row's `preferences` payload is untouched by that write,
   *  so `stored` is still right and re-reading it would be the round trip
   *  this is trying to make cheap. */
  forget(): void;
}

/** PROMOP, through the host-supplied state adapter.
 *
 *  The endpoint does NOT merge key by key, which is what this module was
 *  originally written for. `preferences` is a single JSON column and
 *  `TrialSearchPreferencesSerializer` has no custom `update`, so DRF assigns
 *  the whole dict: a PATCH of `{"a": 1}` followed by `{"b": 2}` leaves the row
 *  holding only `b`. Measured, both in PROMOP's serializer and against DRF's
 *  `partial=True` directly, because the whole design turns on it.
 *
 *  Two consequences, and the second is why this matters:
 *
 *  - sending `null` for a key that has gone away is pointless. Under a
 *    wholesale replace a key disappears by being absent, and `FilterPanel`
 *    represents a cleared control as `undefined`, which JSON drops for us.
 *  - every write must carry the COMPLETE set, or it deletes what it omits.
 *    So the transport remembers what the server holds and merges the
 *    reader's payload over it. The case that needs this is the one where the
 *    caller's set is a strict subset through no fault of its own: a slow load
 *    that arrived after the reader had already edited, and was dropped rather
 *    than revert their edit. Their saved sponsor and phase are still their
 *    preferences — the panel simply never showed them — and a save that
 *    omits them is not a clear, it is a loss.
 *
 *  A filter the reader DID clear still clears, because `userOwnedFilters`
 *  emits it present-and-`undefined`: the merge keeps the key, JSON drops it,
 *  and the stored dict comes back without it.
 */
export function adapterPreferences(
  state: PreferenceMethods,
  /** Who the credential names, right now (`identityFingerprint.ts`).
   *
   *  A BELIEF ABOUT A ROW BELONGS TO THE READER IT WAS READ FOR. Everything
   *  cached below — `stored`, `believed`, `version` — describes one row, and
   *  which row that is depends on the credential. When the credential names
   *  somebody else, all three describe a row this transport is no longer
   *  writing to, and the next save merges the previous reader's filters into
   *  the new reader's row (#603).
   *
   *  #583's guard cannot see this. It compares the credential a write was
   *  QUEUED under against the one it is SENT under, and here the two agree:
   *  the new reader queued and sent their own edit. What is wrong is the
   *  merge BASE, not the attribution.
   *
   *  Nothing else can see it either. The transport is rebuilt when the
   *  hook's key moves, and #583 measured four shapes where the identity
   *  changes and no key does — nothing in the React tree learns anything.
   *
   *  Omitted means no check, and the caller behaves as it did. */
  identity?: () => string | undefined,
  /** The same question, ASKED rather than remembered — see
   *  `credentialIdentityNow` in `types.ts`.
   *
   *  Required for the guard to work at all on the one read that matters
   *  most. The cached reading above is written by the client's own
   *  interceptor, so at mount it is still unknown: the seeding read IS the
   *  first request to fetch a token. Round 2 of the #603 review measured
   *  eleven mount stamps and every one of them was unknown at issue time. */
  identityNow?: () => Promise<string | undefined> | string | undefined,
): PreferenceTransport {
  // What the server is believed to hold — seeded by `get`, kept current by
  // every successful write. Under a wholesale replace this is not an
  // optimisation: it is the only thing standing between a partial payload and
  // the rest of the reader's saved filters.
  let stored: FilterState = {};
  // Whether that belief rests on anything. An empty `stored` because the read
  // came back empty and an empty `stored` because the read FAILED are the same
  // value and opposite situations: the first means there is nothing to
  // protect, the second means we have no idea what we would be overwriting.
  // Writing in the second case replaces the row with whatever the reader
  // happens to be holding.
  let seeded = false;
  // Bumped by `reset`. A read in flight when the reader presses Reset resolves
  // afterwards holding the values they just cleared, and seeding from it puts
  // them in the next payload — so the row comes back. The generation says
  // which era a read belongs to.
  let generation = 0;
  // The first read, so a save cannot overtake it. A reader who edits inside
  // the debounce window on a slow connection would otherwise PATCH before
  // `stored` was seeded — and under a wholesale replace that payload IS the
  // row, so everything they had saved would go with it.
  let firstRead: Promise<unknown> | null = null;
  // Whose belief the cache is. Written ONLY by `nowBelievedFor`, which is
  // also the only thing that sets `seeded` — see the rule stated there.
  let believedFor: string | undefined = undefined;
  // The row's entity-tag as last seen. Three states, and collapsing any two
  // of them produces a wrong precondition:
  //
  //   a tag       — write with `If-Match`.
  //   `null`      — the read said there is NO row, so `If-None-Match: *`.
  //   `undefined` — we do not know: nothing has read it yet, a write could
  //                 not report the new one, or a retry was refused so the
  //                 tag we hold no longer describes `stored`. Read before
  //                 writing. Guessing `If-None-Match: *` here would 412
  //                 against a row that exists, and `If-Match` needs a tag we
  //                 do not have.
  //
  // This is what `stored` alone could never be. `stored` is a belief only
  // this instance updates, so another tab writing the row leaves it stale and
  // confident; the tag makes the SERVER the one that decides whether the
  // belief still holds. Without it the merge below faithfully reconstructs a
  // set that is no longer true and writes it back over the other tab's edit.
  let version: string | null | undefined = undefined;
  // What the PANEL was showing when we last processed a save — which is NOT
  // `stored`, what the server holds. Conflating the two makes the delta right
  // for exactly one save: after a 412 the transport adopts the other tab's
  // row, the panel is never told (nothing reconciles it back into React
  // state), and the next keystroke therefore measures the panel's unchanged
  // value against a row that no longer has it. It scores as a genuine edit,
  // goes out with a VALID `If-Match`, returns 200 — and puts back the filter
  // the other tab cleared, with no 412 and nothing on `onError`.
  //
  // "Did the reader change this?" is a question about the panel, so it is
  // asked against the panel's own last-known state.
  let believed: FilterState = {};
  // Merged, never replaced. A payload carries only the fields the panel
  // OWNS, so replacing would forget everything the reader did not submit
  // this time — and a later clear of one of those fields then reads as
  // "nothing to clear" and silently never fires. Spreading keeps an explicit
  // `undefined`, which is what makes a tombstone stick for the one save that
  // should act on it and no later one.
  const remember = (submitted: FilterState) => {
    believed = { ...believed, ...submitted };
  };
  // Absent on a host adapter written before promop#1312, and on the
  // `localStorage` transport, which has no second writer to race. Both then
  // take the unconditional path unchanged.
  const versioning = state.preferenceVersioning;
  // A WRITE answers with the row's new tag, or `null` when the transport
  // cannot say. That `null` must not land in `version`, where it would read
  // as "there is no row" and aim the next `If-None-Match: *` at a row the
  // write itself just left in place. Applies to `clear` for the same reason:
  // PROMOP's reset EMPTIES the row, it does not delete it.
  const normalise = (tag: string | null): string | undefined =>
    tag ?? undefined;
  // Nulls are keys an older build cleared by writing one, not set values.
  // Used by the unknown-version refresh and the 412 re-read; the read and
  // the blind retry strip inline, in the same loop that copies. The three
  // places that assign merge output are not stripped and do not need to be:
  // `FilterState` has no nullable member, so the reader's own payload cannot
  // introduce one.
  const withoutNulls = (from: FilterState | undefined): FilterState => {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(from ?? {})) {
      if (v !== null && v !== undefined) out[k] = v;
    }
    return out as FilterState;
  };
  //
  // NOT handled here, on purpose: clearing a filter the HOST seeded via
  // `initialFilters` does not persist across a remount. The host's seed is its
  // scope for that mount and reasserts itself — which is the same reason the
  // baseline is excluded from what gets saved at all. Representing "the reader
  // cleared the host's value" would need a sentinel that survives reads, and
  // that is a product decision about whose scope wins, not a defect.
  /** Drop everything if the credential has changed hands.
   *
   *  `sameIdentity` rather than `!==`, for the two reasons it exists: a
   *  rotated token is the same reader, and an unknown identity on either
   *  side is not a mismatch — a host with no credential at all would
   *  otherwise re-read on every call, which is the deployment this guard
   *  is least able to help and most able to slow down.
   *
   *  Everything, not just the tag. `forget()` above clears the tag alone
   *  because the row's payload is untouched by the write that moved it;
   *  here the row itself is a different one, so `stored` and `believed` are
   *  about somebody else and `generation` has to move so a read already in
   *  flight cannot seed the new reader from the old one's row.
   *
   *  Read synchronously, from the cached fingerprint rather than by
   *  fetching a token. That is sound because of the ORDER: `PreferenceWriter`
   *  does its own send-time check immediately before calling `save` here,
   *  and that check fetches a token and refreshes the cache. For `get` there
   *  is no such guarantee and none is needed — a stale answer there costs
   *  one extra read later, not a wrong row.
   */
  const stillTheSameReader = () => {
    if (belongsTo(believedFor, identity?.())) return;
    stored = {};
    believed = {};
    seeded = false;
    version = undefined;
    firstRead = null;
    // Cleared rather than set to the new reader: nothing is believed yet,
    // and the read that seeds it is what will say whose it is.
    believedFor = undefined;
  };

  /** WHO A REQUEST IS ABOUT TO GO OUT AS, asked rather than remembered.
   *
   *  The one question the two previous attempts at this guard could not
   *  answer, and the reason each of them was wrong in its own way:
   *
   *    * stamped from the cached reading BEFORE the read — unknown at
   *      mount, because the cache is written by the client's interceptor
   *      and the seeding read is the first request to fetch a token. The
   *      cache was then never stamped and the guard was off for a reader
   *      who loads their filters and never saves.
   *    * stamped from the cached reading AFTER the read — by then it names
   *      whoever the host has swapped in since. A read issued as one and
   *      resolving after the swap stamped the cache with TWO while holding
   *      ONE's row, every later check agreed, and the merge went ahead.
   *      Measured end to end through the real bridge, round 2.
   *
   *  Both spellings share a premise: that the identity of a request can be
   *  recovered by inspecting a cache some OTHER request may have moved. It
   *  cannot. So it is established here, awaited before the request is
   *  issued, and carried by the caller to the place that records the
   *  belief. */
  const whoThisRequestIsFor = async (): Promise<string | undefined> => {
    try {
      // NO FALLBACK to the cached reader, and that is the correction this
      // whole round is about. `identityNow` absent means the host cannot
      // be asked; answering from the cache instead makes "we asked" and
      // "we looked at a value somebody else may have moved" the same
      // thing, which is the premise all three defects here shared. With
      // no fallback the answer is honestly unknown, the guard is off for
      // that host, and nothing pretends otherwise.
      return await identityNow?.();
    } catch {
      // A host whose `getToken` rejects has told us nothing, and nothing
      // is what gets recorded — an unknown stamp leaves the guard off for
      // this cache, which is the same place a credential-less host sits.
      // It must not REJECT: this promise is started beside the request and
      // awaited after it, so throwing here would leave the read unhandled.
      return undefined;
    }
  };

  /** Record a belief about the row, and whose row it is, TOGETHER.
   *
   *  The only thing that sets either, and that is the invariant: `seeded`
   *  without a stamp switches the guard off for good, because
   *  `sameIdentity(undefined, anyone)` is true. `reset()` used to reach
   *  exactly that state — it clears `believedFor` on a swap and then claims
   *  `seeded` on its way out — and every reader after it was waved through.
   *
   *  `who` is what `whoThisRequestIsFor()` answered BEFORE the request that
   *  produced this belief. Unknown is allowed and means the guard is off
   *  for this cache: a host with no credentials has nothing to guard, and
   *  an honest "we could not tell" is not the same as a confident wrong
   *  name. What is not allowed is filling it in from anywhere else. */
  const nowBelievedFor = (who: string | undefined) => {
    seeded = true;
    believedFor = who;
  };

  /** Whether something attributed to `whose` may be used for `now`.
   *
   *  Plain `sameIdentity`, including its carve-out: unknown on either side
   *  is a match. An earlier revision made the unknowns asymmetric so that
   *  "we could not attribute this" would refuse a reader who CAN be named.
   *  That existed for one host shape — `credentialIdentity` supplied and
   *  `credentialIdentityNow` not — and that shape is no longer supported
   *  (see `whoThisRequestIsFor`). With it gone, "unknown" means only "this
   *  deployment has no credentials", which has nothing to guard and must
   *  not be made to pay; the asymmetric rule then had no reachable case
   *  except to refuse the reader's own saved filters on a plain page load.
   */
  const belongsTo = (whose: string | undefined, now: string | undefined) =>
    sameIdentity(whose, now);

  /** Whether a read that went out for `issuedFor` may still be adopted.
   *
   *  A read carries what the server held for whoever its request named; if
   *  the host has signed somebody else in since, that row is a stranger's
   *  and the answer is to drop it on the floor rather than cache it OR
   *  hand it back.
   *
   *  This replaces a counter (`readerEra`) that moved whenever the cache
   *  was dropped. That is the wrong clock: it moves on the first read of
   *  every host that has credentials at all, so gating on it discarded the
   *  seeding read or let a stale one through depending on which side of
   *  the bump it was captured. Both spellings were tried; both were dead
   *  under ablation, twice, by two different reviewers. The question is
   *  not "how many times has the cache changed hands" but "is this row's
   *  reader still here", and that is answerable directly. */
  const stillTheirs = async (issuedFor: string | undefined) => {
    // ASKED, not read off the cache. The cache was last written when this
    // read's own request went out, so consulting it here compares the
    // issue-time answer with itself and agrees every time — measured, and
    // the row went to the arriving reader anyway. Asking costs one
    // `getToken`, which the host serves from its own cache, and it is the
    // only thing that can see a swap that happened while the read was in
    // the air.
    //
    // Safe to await here in a way it is not before a request: the read has
    // already landed, so nothing can overtake it.
    return belongsTo(issuedFor, await whoThisRequestIsFor());
  };

  /** A host that names its reader but cannot be ASKED afresh.
   *
   *  `credentialIdentity` without `credentialIdentityNow`: permitted by the
   *  prop contract, and the one shape in which the transport genuinely
   *  cannot see a swap coming. The cached reading only moves when something
   *  fetches a token, and between two saves nothing does — so a swap is
   *  invisible right up until the write goes out under the new credential,
   *  by which time the row is gone. Generated interleavings found it in the
   *  first three seeds.
   *
   *  The answer is to stop trusting the cache across a save: the re-read
   *  below both attributes the row afresh and, by going through the client,
   *  refreshes what the cached reading can see. The reader loses at most
   *  the one edit that straddles the swap and the next one lands correctly.
   *
   *  A host with no identity at all is NOT this: there is nobody to
   *  confuse with anybody, and making it pay a round trip per save would be
   *  a cost borne entirely by the deployment the guard does nothing for.
   *  The local stand is that host. */

  /** Refuse to write if the row this payload was composed for is no longer
   *  the row a write would land in.
   *
   *  CALLED IMMEDIATELY BEFORE EVERY WRITE, not once near the top of
   *  `save`. One check up there covers the synchronous entry and nothing
   *  else: below it are the first read, the re-read when nothing was
   *  seeded, the unknown-version refresh and the 412 recovery re-read —
   *  four round trips, each one a window in which the host can swap the
   *  account. Measured in that window: reader one's edit went out under
   *  reader two's credential and `onSuccess` reported it as saved.
   *
   *  Refused rather than re-based on the arriving reader's row: the payload
   *  was composed for a panel showing the PREVIOUS reader's filters, so
   *  re-basing would send those into the new row, which is the whole of
   *  #603. The reader loses this one edit, can make it again, and is told
   *  — `PreferenceWriter` surfaces it through `onError`.
   *
   *  ASKS AFRESH rather than reading the cached fingerprint. An earlier
   *  version read the cache and argued that every call site sat just after
   *  a request through the same client, so the cache had been refreshed by
   *  it. That argument is true of the two writes below a read and false of
   *  the first write of a save whose cache was already seeded — nothing has
   *  spoken to the server since the swap, so the cache still names the
   *  reader who has gone. The transport was then correct only in the
   *  company of `PreferenceWriter`, which asks on its behalf.
   *
   *  Generated interleavings found it in seconds; the argument had survived
   *  two reviews. A transport that is correct on its own is worth one token
   *  read, which the host's `getToken` serves from its own cache anyway. */
  const refuseIfTheReaderChanged = async () => {
    if (belongsTo(believedFor, await whoThisRequestIsFor())) return;
    stillTheSameReader();
    throw new Error(
      "the signed-in account changed while these filters were being saved",
    );
  };

  return {
    forget: () => {
      // NOT `stillTheSameReader()`. This is called from the wizard's flag
      // write, which is not serialised behind the save queue, and the
      // forget's bump could land inside a save's refresh read — the save
      // then abandoned quietly and `onSuccess` fired over a write that
      // never happened. The next `get` or `save` does the identity check
      // anyway; all this one owes is the tag.
      version = undefined;
    },
    get: async () => {
      stillTheSameReader();
      // BOTH STARTED IN THIS TICK, and that is load-bearing in both
      // directions.
      //
      // The ask has to happen, because the cached fingerprint is written by
      // the client's own interceptor and at mount it has never been written
      // — this read is the first request to fetch a token. Stamping from
      // the cache instead is what round 1 and round 2 of the review each
      // got wrong in their own way.
      //
      // But the request must not WAIT for it. Awaiting the ask first
      // delayed the read by a microtask, and a measured consequence
      // followed: when the host swaps one store for another for the same
      // patient, the previous writer's unmount flush goes through `live()`
      // into the NEW store, and in that microtask it got there first. The
      // read then came back holding what the flush had just written rather
      // than the new store's own row (`savedFiltersRace.test.tsx`, the
      // delivery control at the end of the Reset test).
      //
      // Issued together, the ask names whoever the request is going out as
      // — both fetch the token in the same moment — and nothing is delayed.
      const asking = (async () => whoThisRequestIsFor())();
      const reading = versioning
        ? versioning.read()
        : (async () => ({
            filters: (await state.getPreferences()) ?? {},
            version: null as string | null,
          }))();
      // A handler NOW, not when it is awaited. `reading` is started beside
      // the ask and awaited after it, and in that gap a rejection has
      // nobody listening — Node reports an unhandled rejection even though
      // the caller does catch `get()`. The original promise is still the
      // one awaited below, so the failure is not swallowed, only
      // acknowledged.
      void reading.catch(() => undefined);
      // THE RESET ERA IS READ HERE, synchronously, beside the request it
      // describes. "This read predates the Reset" is a statement about when
      // the read was ISSUED, so taking it after an await answers a different
      // question: measured, `reset()` bumps synchronously and the reader's
      // cleared filters came straight back.
      //
      // WHOSE read this is, by contrast, is not a clock at all: it is
      // `issuedFor`, carried on the closure and checked against the reader
      // when the row lands. See `stillTheirs`.
      const era = generation;
      const read = (async () => {
        const issuedFor = await asking;
        // Now that the ask has refreshed what is knowable. The check at the
        // top of `get` was taken against a cache that, at mount, had never
        // been written.
        stillTheSameReader();
        const fromServer = await reading;
        const out: FilterState = {};
        for (const [k, v] of Object.entries(fromServer.filters ?? {})) {
          // A null is a key an older build cleared by writing one, not a set
          // value. Nothing writes them any more.
          if (v !== null && v !== undefined) (out as Record<string, unknown>)[k] = v;
        }
        if (!(await stillTheirs(issuedFor))) {
          // Not merely "do not cache it" — do not HAND IT BACK either. The
          // caller paints what this resolves to into the panel (`hooks.ts`,
          // the load effect), so returning the departed reader's row draws
          // their saved search in front of the arriving one, whose next
          // keystroke then sends the whole panel. Declining to seed while
          // still returning it moved the leak one caller along.
          //
          // Nothing is seeded, so the arriving reader's first save takes
          // `save`'s re-read path and builds against THEIR row. They lose
          // nothing; the row they never saw is simply never adopted.
          return {};
        }
        if (era === generation) {
          stored = { ...out };
          // What the panel is about to show — ALMOST. The caller holds back
          // the fields the reader overruled while this read was in flight
          // (#537), so for those this is a claim about a box that says
          // something else. It stays harmless only because the caller also
          // claims their KEYS, so the next save carries an opinion about
          // every one of them — a value, or a tombstone — and corrects the
          // record. Narrow that claim and narrow this alongside it.
          believed = { ...out };
          version = fromServer.version;
          nowBelievedFor(issuedFor);
        }
        return out;
      })();
      firstRead = read;
      return read;
    },
    save: async (filters) => {
      stillTheSameReader();
      // Serialised behind the first read — see `firstRead` above. A save that
      // overtakes it would build its payload against an empty `stored` and
      // replace the row with the one key the reader has touched.
      if (firstRead) await firstRead.catch(() => {});
      if (!seeded) {
        // One more attempt, because a read that failed once may not fail
        // twice, and the alternative is losing the reader's edit.
        //
        // Asked alongside the read rather than before it, exactly as in
        // `get` and for both of the reasons stated there.
        const asking = (async () => whoThisRequestIsFor())();
        // Through the versioning transport when there is one, so `stored`
        // and `version` are seeded by the SAME read. Seeding only `stored`
        // here left the version at "no row yet", and the next write then
        // sent `If-None-Match: *` at a row that plainly exists — a
        // guaranteed 412 and a wasted round trip, from the one place the
        // two were filled in by different code.
        const reading = versioning
          ? versioning.read()
          : (async () => ({
              filters: (await state.getPreferences()) ?? {},
              version: undefined,
            }))();
        // Same as in `get`: acknowledged now, awaited below.
        void reading.catch(() => undefined);
        // Beside the request, for the reason spelled out in `get`.
        const era = generation;
        const issuedFor = await asking;
        stillTheSameReader();
        try {
          const fromServer = await reading;
          const out: FilterState = {};
          for (const [k, v] of Object.entries(fromServer.filters ?? {})) {
            if (v !== null && v !== undefined) (out as Record<string, unknown>)[k] = v;
          }
          // Same era check as `get`: a Reset during the retry wins.
          if (era === generation) {
            stored = out;
            // The panel was loaded from whatever the caller showed; this
            // read is the first thing we know about the row, so it is also
            // the best available account of what the panel is showing.
            // Leaving `believed` empty here made a clear unrecognisable —
            // `before[k]` is undefined, so "nothing to clear" — and the
            // reader's clear silently never fired.
            believed = { ...out };
            version = fromServer.version;
            nowBelievedFor(issuedFor);
          }
        } catch {
          // Still blind. Refusing costs the reader this one edit, which they
          // can make again; writing costs them every filter they ever saved,
          // which they cannot. `PreferenceWriter` reports it through
          // `onError` like any other failed write.
          throw new Error("saved filters could not be read, so they were not overwritten");
        }
      }
      // ASKED AGAIN, after the awaits above. The check at the top of `save`
      // is taken synchronously, and everything since — the first read, the
      // re-read when nothing was seeded — is one or two round trips during
      // which the account can change. Refusing costs the reader this one
      // edit, which they can make again and which `PreferenceWriter`
      // reports through `onError`; writing costs somebody else their saved
      // search. Refused rather than re-based on the new reader's row: the
      // payload was composed for a panel showing the PREVIOUS reader's
      // filters, so re-basing would send those into the new row, which is
      // the whole of #603.
      await refuseIfTheReaderChanged();

      // The reader's payload over what the server holds. A key they cleared is
      // present-and-`undefined` and drops out on the way through JSON; a key
      // they never saw survives.
      const merge = (base: FilterState): FilterState => {
        const out: Record<string, unknown> = { ...base };
        for (const [k, v] of Object.entries(filters)) {
          if (v === undefined) delete out[k];
          else out[k] = v;
        }
        return out as FilterState;
      };

      // What this save actually CHANGED, as opposed to what it carries.
      //
      // The caller hands over the whole panel (`TrialMatches.handleFiltersChange` persists
      // `userOwnedFilters`, every field the reader owns), not the one control
      // they touched. Re-applying all of it over a re-read is therefore not
      // "re-apply the reader's edit" — it re-asserts the panel's stale view
      // and puts back exactly what the other tab cleared. Measured in a real
      // browser, two tabs, a real server: the refusal fired, the retry went
      // out with the correct fresh tag, and the cleared filter came back
      // anyway. The precondition was working; the payload defeated it.
      //
      // The difference against what the server was believed to hold IS the
      // edit, and it is derivable here without changing the caller.
      const edited = (against: FilterState): FilterState => {
        const out: Record<string, unknown> = {};
        const before = against as Record<string, unknown>;
        for (const [k, v] of Object.entries(filters)) {
          // `undefined` is how a cleared control arrives; it is a change only
          // if the key was there to clear.
          if (v === undefined) {
            // A clear is an edit only if the panel HELD something to clear.
            // Not `k in before`: once a clear lands, `believed` records the
            // tombstone, and `userOwnedFilters` keeps emitting it for as long
            // as the field stays owned — so presence-of-key would re-issue
            // the delete on every later save, against whatever the other tab
            // has since put there.
            if (before[k] !== undefined) out[k] = undefined;
          } else if (!sameValue(before[k], v)) {
            // `sameValue`, not `!==`. A multi-select arrives as a NEW array
            // every render, so reference equality calls an untouched
            // `trialPurpose` an edit — and then re-applies the reader's stale
            // copy of it over the other tab's change, which is the whole
            // failure this function exists to stop. `filters.ts` says as much
            // in its own docstring; this walked into it anyway.
            //
            // It is order- and case-insensitive, so a REORDER of the same
            // selection is not an edit and does not reach the server. That
            // follows `filters.ts`'s reasoning — the backend ORs the codes,
            // so a reordering is not a different search — but it does mean
            // the stored order can drift from the panel's.
            out[k] = v;
          }
        }
        return out as FilterState;
      };

      const mergeEdit = (base: FilterState, edit: FilterState): FilterState => {
        const out: Record<string, unknown> = { ...base };
        for (const [k, v] of Object.entries(edit)) {
          if (v === undefined) delete out[k];
          else out[k] = v;
        }
        return out as FilterState;
      };

      if (!versioning) {
        const payload = merge(stored);
        const era = generation;
        await refuseIfTheReaderChanged();
        if (era !== generation) return;
        await state.savePreferences(payload);
        // AFTER it resolves. Recording a payload that failed means the next
        // one is built against a row that does not exist.
        stored = { ...payload };
        // No `believed` here: `versioning` is captured once, so a transport
        // that took this branch never takes the conditional one, and nothing
        // ever reads it.
        return;
      }

      // Conditional. `version === null` means the read said there is no row,
      // so this is the first write — the one `If-Match` cannot describe.
      // Total, deliberately. The earlier `version as string` was a cast over
      // a value that really could be `undefined`, and `If-Match: undefined`
      // is not a weak precondition — axios DELETES a header with an
      // undefined value, so the request went out unconditional and a
      // concurrent writer's edit was destroyed with no 412 and nothing on
      // `onError`. A mechanism that fails open in silence is worse than no
      // mechanism, because the caller believes it is protected.
      const precondition = (): Precondition =>
        typeof version === "string"
          ? { kind: "ifMatch", version }
          : version === null
            ? { kind: "ifNoneMatch" }
            : // Still unknown: either a refresh has just run and the server
              // would not describe the row, or a refusal carried no tag and
              // neither did the re-read. Nothing to quote either way. Writing
              // unconditionally is what every client did before #1312 and is
              // the same thing a host adapter without versioning does — but
              // it IS a degradation, and it is here rather than hidden
              // behind a cast so the next reader can see it.
              { kind: "none" };

      if (version === undefined) {
        // A write that could not report its new tag, or a retry that was
        // refused, leaves us here. One read costs a round trip; writing on a
        // guess costs the reader whatever the other tab had just saved.
        const era = generation;
        const refreshed = await versioning.read();
        if (era !== generation) {
          // A Reset landed while we were looking. Returning is the point:
          // merely declining to adopt the refresh would leave this save to
          // write the pending edit against the version Reset installed,
          // which resurrects exactly what Reset removed. The refusal path
          // below already returns here; this one used to fall through.
          return;
        }
        stored = withoutNulls(refreshed.filters);
        version = refreshed.version;
      }
      // The edit, measured once against what was believed when it was made.
      const edit = edited(believed);
      // …and applied to the best-known server state, for the FIRST write as
      // well as the retry. `merge(stored)` here would put the panel's whole
      // stale view on the wire, and when the refresh above has just fetched
      // a current tag that write SUCCEEDS — no 412, no retry, the other
      // tab's change gone. Guarding only the retry left that door open.
      //
      // With no refresh in between this is the same payload `merge` would
      // have built: the keys it omits are exactly the ones already equal to
      // what `stored` holds.
      const payload = mergeEdit(stored, edit);
      // The tag the refusal reported, kept for the recovery below.
      let refusedWith: string | null | undefined;
      const writeEra = generation;
      // The unknown-version refresh above is a full round trip, and the
      // reader can change inside it.
      await refuseIfTheReaderChanged();
      // AND the ask itself is a round trip, during which a Reset can land.
      // The check after the write is too late: by then the pre-reset
      // payload has gone out quoting the tag Reset installed, so the
      // precondition PASSES and everything the reader cleared comes back.
      // Every await before a write needs this in front of it, not only the
      // ones that fetch.
      if (writeEra !== generation) return;
      try {
        const tag = normalise(await versioning.write(payload, precondition()));
        if (writeEra !== generation) {
          // A Reset landed while this write was on the wire. Recording its
          // payload would carry the reader's pre-reset filters forward into
          // the next save, with a precondition that passes. The retry below
          // and the two refresh paths guard the same way.
          return;
        }
        version = tag;
        stored = { ...payload };
        remember(filters);
        return;
      } catch (error) {
        if (!PreconditionFailed.is(error)) throw error;
        refusedWith = (error as PreconditionFailed).version ?? undefined;
      }

      // Someone else wrote between our read and our write. Re-read, re-apply
      // the reader's OWN edit over what is there now, and try once more.
      //
      // Re-applying over the fresh row is the whole point: resending `payload`
      // would be the lost update with an extra round trip, since it was built
      // from a `stored` we have just been told is out of date.
      const era = generation;
      const fresh = await versioning.read();
      if (era !== generation) {
        // A Reset landed while we were re-reading. Its clear is the newer
        // intent; putting this edit back on top of what Reset removed is
        // what `generation` exists to prevent.
        return;
      }
      stored = withoutNulls(fresh.filters);
      // The read is the authority on CONTENT, but it may be unable to
      // describe the row — see `VersionedPreferences.version`. The refusal we
      // are recovering from carried the tag the server had at that moment;
      // it is a better answer than "unknown", and it is what stops a row
      // without `updated_at` failing every save forever.
      version = fresh.version === undefined ? refusedWith : fresh.version;
      // Only what changed, over what is there NOW. `merge(stored)` here
      // would re-assert the whole stale panel — see `edited` above.
      const retried = mergeEdit(stored, edit);
      const retryEra = generation;
      // Same again: the 412 recovery re-read is another round trip, and so
      // is the ask inside the refusal.
      await refuseIfTheReaderChanged();
      if (retryEra !== generation) return;
      try {
        const tag = normalise(await versioning.write(retried, precondition()));
        if (retryEra !== generation) {
          // A Reset landed while the retry was on the wire. Same reason as
          // the first write: recording this payload would carry the
          // reader's pre-reset filters into the next save.
          return;
        }
        version = tag;
        stored = { ...retried };
        remember(filters);
      } catch (error) {
        if (!PreconditionFailed.is(error)) throw error;
        // Twice in a row is not a stale cache, it is live contention, and a
        // third attempt would be a loop rather than a fix. `PreferenceWriter`
        // surfaces this through `onError`, which exists for the one path that
        // deliberately drops an edit.
        // NOT the tag the server just reported. `stored` still holds the
        // row from the FIRST re-read, so adopting the newer tag would leave
        // the two describing different states — and the tag is the newer
        // one, so the next save's precondition would PASS and overwrite a
        // row this client never read. That is the lost update again, one
        // step further along, which is the whole thing being prevented.
        // Unknown is the truthful answer, and it makes the next save read.
        version = undefined;
        throw new Error(
          "saved filters were changed elsewhere while saving, so this edit was not applied",
        );
      }
    },
    reset: async () => {
      stillTheSameReader();
      // Bumped SYNCHRONOUSLY, before anything is awaited. A read already in
      // flight reads `generation` to decide whether Reset superseded it, and
      // deferring the bump by even a microtask hands that read a window in
      // which it still looks current.
      generation += 1;
      // Asked beside the clear, never before it — `get` states both halves
      // of that rule. This call establishes a belief ("the row is empty"),
      // so it needs a stamp: without one, a reset following a swap cleared
      // `believedFor` and then claimed `seeded` on its way out, leaving the
      // cache seeded and unstamped. `sameIdentity(undefined, anyone)` is
      // true, so every reader after that was waved through and the guard
      // was off for the life of the transport.
      const asking = (async () => whoThisRequestIsFor())();
      // `stored` cleared REGARDLESS of the outcome, which is the opposite of
      // what a merging endpoint would want. Under a replace, a `stored` still
      // full of the old values means the next save puts them all back — the
      // reader watches their filters disappear and finds them again on the
      // next mount. Clearing first means the next save writes only what they
      // are holding, which repairs a reset that failed.
      //
      // `seeded` is NOT claimed here, though. A reset that fails on a
      // transport that never read the row leaves us knowing nothing about it
      // while believing we know it is empty, and the next save replaces it
      // blind — the very thing `save` refuses to do. A reset that fails after
      // a good read is different: the old contents are still known, and
      // writing only what the reader holds is the repair.
      const knew = seeded;
      const knownVersion = version;
      stored = {};
      // Reset empties the panel as well, so the next save's delta is measured
      // against an empty one.
      believed = {};
      if (!versioning) {
        const clearing = state.resetPreferences();
        void clearing.catch(() => undefined);
        const issuedFor = await asking;
        // Through the pair, so the stamp cannot drift away from the flag.
        // Before the await as well as after: `knew` carries the previous
        // answer forward, and a reset that FAILS must not leave the cache
        // claiming to know an empty row it never wrote.
        if (knew) nowBelievedFor(issuedFor);
        else seeded = false;
        await clearing;
        nowBelievedFor(issuedFor);
        return;
      }
      // Issued in this tick, like every other request here, so awaiting the
      // ask cannot let anything overtake it.
      const clearing = (async () => {
        try {
          // A reset with no known version is still worth sending: there is
          // nothing to protect, since clearing is what a concurrent writer
          // would lose anyway and the reader asked for it explicitly.
          return normalise(
            await versioning.clear(
              typeof knownVersion === "string"
                ? { kind: "ifMatch", version: knownVersion }
                : { kind: "none" },
            ),
          );
        } catch (error) {
          if (!PreconditionFailed.is(error)) throw error;
          // The row moved under us. A reset does not need to preserve what
          // it is about to delete, so clear unconditionally rather than
          // spending a read to earn a tag we would only use to delete the
          // row anyway — the reader's intent has not changed.
          return normalise(await versioning.clear({ kind: "none" }));
        }
      })();
      // Same as in `get`.
      void clearing.catch(() => undefined);
      const issuedFor = await asking;
      if (knew) nowBelievedFor(issuedFor);
      else seeded = false;
      version = await clearing;
      nowBelievedFor(issuedFor);
    },
  };
}

/** The no-adapter fallback.
 *
 *  Per-browser rather than per-person, which is the honest limit of storage
 *  the host did not give us: it is keyed by patient so two patients in one
 *  browser do not share filters, but it does not follow the patient to
 *  another device. Losing the feature entirely would be worse — the panel
 *  would silently forget on every reload.
 */
export function localStoragePreferences(key: string): PreferenceTransport {
  // Hashed, because `key` is the patient key — for an inline `patientInfo`
  // that is the serialized payload itself (disease, country, labs, dates).
  // It was a react-query cache key living in memory; putting it in a storage
  // key would leave a patient record on disk, per browser, unexpired, for
  // every patient ever viewed, since `reset` only removes the current one.
  const storageKey = `exact.filters.${hashKey(key)}`;
  const storage = (): Storage | null => {
    try {
      // Absent in SSR, and throws outright in a sandboxed iframe or with
      // site data blocked. Either way the caller gets the in-memory default.
      return typeof localStorage === "undefined" ? null : localStorage;
    } catch {
      return null;
    }
  };
  const readRaw = (store: Storage): Record<string, unknown> => {
    try {
      const parsed: unknown = JSON.parse(store.getItem(storageKey) ?? "");
      return parsed && typeof parsed === "object" ? (parsed as Record<string, unknown>) : {};
    } catch {
      // Absent, or a corrupt entry: a half-written value, or a shape from an
      // older build. Treat it as "no saved filters" rather than throwing on
      // mount and taking the list down with it.
      return {};
    }
  };
  return {
    // Nothing to forget: this transport has no precondition, because there is
    // no other writer to lose a race with.
    forget: () => {},
    get: async () => {
      const store = storage();
      if (!store) return {};
      const out: FilterState = {};
      for (const [k, v] of Object.entries(readRaw(store))) {
        // A null is a key the reader cleared, not a set one.
        if (v !== null && v !== undefined) (out as Record<string, unknown>)[k] = v;
      }
      return out;
    },
    save: async (filters) => {
      try {
        const store = storage();
        if (!store) return;
        // Merged over what is on disk, with an explicit null for a cleared
        // key. Same OUTCOME as the adapter, reached differently: the adapter
        // merges in memory and sends a complete dict because the server
        // replaces; this one merges on disk because it is the storage. A
        // wholesale overwrite here would delete keys the caller never knew
        // about — exactly the state a deliberately-discarded load leaves it
        // in. `get` strips the nulls again.
        const existing = readRaw(store);
        for (const [k, v] of Object.entries(filters)) {
          existing[k] = v === undefined ? null : v;
        }
        store.setItem(storageKey, JSON.stringify(existing));
      } catch {
        // Quota, or private mode. A filter that fails to persist is not a
        // reason to break the search that is already on screen.
      }
    },
    reset: async () => {
      try {
        storage()?.removeItem(storageKey);
      } catch {
        /* as above */
      }
    },
  };
}

/** A short stable digest of the patient key. djb2 — not a security boundary,
 *  just enough that a storage key is not a readable patient record. */
function hashKey(key: string): string {
  let h = 5381;
  for (let i = 0; i < key.length; i += 1) h = ((h << 5) + h + key.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

/** What is waiting behind the request in flight.
 *
 *  Two fields rather than one slot, because a reset and a save are not
 *  alternatives: if the reader presses Reset and then changes a filter, BOTH
 *  have to happen and in that order. Collapsing them into one slot let the
 *  later save overwrite the reset — and the reset is what empties the row and
 *  clears the transport's memory of it, so dropping it leaves the next save
 *  writing the old values back alongside the new edit.
 */
interface Pending {
  reset: { by: string | undefined } | null;
  save: { value: FilterState; by: string | undefined } | null;
}

/** One unit of work for the queue.
 *
 *  `by` is who was signed in when the reader ASKED for it — at the keystroke
 *  for a save, at the click for a reset — carried all the way to the send so
 *  the two can be compared. See `identityFingerprint.ts` and #583. */
type Write =
  | { kind: "save"; value: FilterState; by: string | undefined }
  | { kind: "reset"; by: string | undefined };

export interface PreferenceWriterOptions {
  debounceMs?: number;
  /** Reported instead of thrown: a filter that failed to save must not take
   *  down the list that is already rendered. */
  onError?: (error: unknown) => void;
  /** Called after a write lands. The counterpart to `onError`: without it a
   *  caller showing "couldn't save" has no way to learn that the NEXT write
   *  succeeded, and goes on saying it. */
  onSuccess?: () => void;
  /** Who is signed in, RIGHT NOW, as a fingerprint
   *  (`identityFingerprint.ts`).
   *
   *  Read when a write is asked for and again when it goes out; a write
   *  whose two readings disagree is not sent. EXACT keys the saved-filters
   *  row on the token, so sending it would write one reader's search into
   *  another's row — #583.
   *
   *  Simpler here than in `patientWriter`: this queue carries one whole
   *  value, last-write-wins, so there is nothing to partition. A stranded
   *  write takes the existing failure path — reported, not retried, and
   *  whatever is queued behind it still drains.
   *
   *  Omitted means no guard, and the caller behaves exactly as it did. */
  identity?: () => string | undefined;
  /** Who the credential names AT SEND TIME, read afresh.
   *
   *  The cached `identity` above is right for the capture — it is read on a
   *  keystroke — but it only changes when somebody fetches a token, and
   *  between an edit and its flush there may be no request at all. Compared
   *  against the cache, a swap inside the debounce window is invisible: the
   *  write passes and the transport then sends it under the new credential.
   *  See `patientWriter.ts`, which carries the same pair and the same note
   *  about the microsecond race that remains. */
  identityNow?: () => Promise<string | undefined> | string | undefined;
}

/**
 * Debounce in front, a one-deep queue behind.
 *
 * `save` collapses rapid edits into one request. While a request is in
 * flight a further `save` does not start a second one — it replaces whatever
 * was waiting, because only the newest filter state matters. `reset` jumps
 * the debounce (it is a deliberate click, not a drag) and invalidates every
 * write issued before it.
 */
export class PreferenceWriter {
  private readonly transport: PreferenceTransport;
  private readonly debounceMs: number;
  private readonly onError: (error: unknown) => void;
  private readonly onSuccess: () => void;
  private readonly identity: () => string | undefined;
  private readonly identityNow: () => Promise<string | undefined> | string | undefined;

  private timer: ReturnType<typeof setTimeout> | null = null;
  private debounced: FilterState | null = null;
  /** Who made the edit sitting in the debounce. Captured at the keystroke,
   *  because that is the moment the reader chose the value; by the time the
   *  timer fires, the account may not be theirs any more. */
  private debouncedBy: string | undefined = undefined;
  private inFlight: Promise<void> | null = null;
  private pending: Pending = { reset: null, save: null };
  /** Bumped by `reset`. A write started under an older generation has its
   *  result ignored, and a queued one is dropped. */
  private generation = 0;

  constructor(transport: PreferenceTransport, opts: PreferenceWriterOptions = {}) {
    this.transport = transport;
    this.debounceMs = opts.debounceMs ?? FILTER_DEBOUNCE_MS;
    this.onSuccess = opts.onSuccess ?? (() => {});
    this.identity = opts.identity ?? (() => undefined);
    this.identityNow = opts.identityNow ?? (() => this.identity());
    this.onError =
      opts.onError ??
      ((error: unknown) => {
        // Not silence by default. A persistently failing adapter otherwise
        // presents as "filters just don't save", with nothing anywhere to say
        // why — and this is a best-effort write, so nothing else reports it.
        console.warn("[exact] saved filters could not be written", error);
      });
  }

  /** Persist these filters, eventually. */
  save(filters: FilterState): void {
    this.debounced = filters;
    this.debouncedBy = this.identity();
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = setTimeout(() => {
      this.timer = null;
      const value = this.debounced;
      const by = this.debouncedBy;
      this.debounced = null;
      // Cleared with it. Nothing live reads the stale one — `flush` bails on
      // a null value first, and `save` overwrites both — but the two are one
      // fact and leaving half of it behind invites a reader to trust it.
      this.debouncedBy = undefined;
      if (value !== null) this.enqueue({ kind: "save", value, by });
    }, this.debounceMs);
  }

  /** Clear them, now, and retire anything already on the wire. */
  reset(): void {
    this.cancelDebounce();
    this.generation += 1;
    this.enqueue({ kind: "reset", by: this.identity() });
  }

  /** Send a pending debounced write immediately. For unmount: a filter
   *  changed in the last few hundred milliseconds should not be lost because
   *  the user navigated away. */
  flush(): void {
    if (this.timer === null) return;
    // Read before cancelling: `cancelDebounce` clears the pending value too,
    // so taking it afterwards always reads null and flush sends nothing.
    const value = this.debounced;
    const by = this.debouncedBy;
    this.cancelDebounce();
    if (value !== null) this.enqueue({ kind: "save", value, by });
  }

  /** Resolves when nothing is in FLIGHT. A write still sitting in the debounce
   *  is not covered — call `flush()` first if you need that too, which is what
   *  a host awaiting a save before navigating wants. */
  async settled(): Promise<void> {
    while (this.inFlight) await this.inFlight;
  }

  private cancelDebounce(): void {
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    this.debounced = null;
    this.debouncedBy = undefined;
  }

  private enqueue(write: Write): void {
    if (this.inFlight) {
      if (write.kind === "reset") {
        // A reset supersedes a queued save — that save was issued before it.
        this.pending.reset = { by: write.by };
        this.pending.save = null;
      } else if (this.pending.reset) {
        // Waits behind the reset, not instead of it.
        this.pending.save = { value: write.value, by: write.by };
      } else {
        // Only the latest save matters; the intermediate states do not.
        this.pending.save = { value: write.value, by: write.by };
      }
      return;
    }
    void this.run(write);
  }

  private async run(write: Write): Promise<void> {
    const generation = this.generation;
    // WHOSE WRITE THIS IS, decided before the transport is touched and
    // against the credential as it is NOW, not as it was cached. A rotated
    // token and an unknown identity pass; a known stranger does not —
    // `identityFingerprint.ts` has why those differ.
    //
    // Thrown into the chain rather than given a path of its own: `onError`
    // reports it, `onSuccess` does not fire, and the tail still drains,
    // which is every behaviour a stranded write wants and none of them
    // newly written.
    //
    // The async wrapper also subsumes what the old try/catch here was for —
    // a host adapter that throws SYNCHRONOUSLY rather than rejecting. Left
    // outside a chain that bypassed `onError`, never assigned `inFlight`,
    // and surfaced only as an unhandled rejection, while this class promises
    // that a failed write is reported and not thrown. A rejection rather
    // than a reported-and-resolved substitute, because a resolved one
    // travels the SUCCESS path below and the failure would be announced as
    // a success.
    const send = (now: string | undefined): Promise<void> => {
      if (!sameIdentity(write.by, now)) throw new IdentityChanged();
      return write.kind === "reset"
        ? this.transport.reset()
        : this.transport.save(write.value);
    };
    let request: Promise<void>;
    try {
      const now = this.identityNow();
      // Awaited only when there is something to await. A reader that answers
      // synchronously — the default, and every caller that supplies none —
      // keeps the transport call in the SAME task, which is a contract this
      // queue already had: a reset "jumps the debounce", and a caller that
      // advances its timers and then reads `calls` is entitled to see the
      // request. Wrapping unconditionally in an async function moved every
      // send a microtask later and broke nine existing tests, which is the
      // cheap version of breaking a caller.
      request =
        typeof (now as { then?: unknown })?.then === "function"
          ? (now as Promise<string | undefined>).then(send)
          : send(now as string | undefined);
    } catch (error: unknown) {
      request = Promise.reject(error);
    }

    this.inFlight = request
      .then(() => {
        this.onSuccess();
      })
      .catch((error: unknown) => {
        this.onError(error);
      })
      .then(() => this.next(generation));
  }

  /** Unlock the queue and start whatever was waiting behind this write.
   *
   *  Extracted so the stranded path above reaches it too: a write refused
   *  for the wrong account must not also wedge the queue behind it. */
  private next(generation: number): void {
    this.inFlight = null;
    const reset = this.pending.reset;
    if (reset) {
      this.pending.reset = null;
      void this.run({ kind: "reset", by: reset.by });
      return;
    }
    const save = this.pending.save;
    this.pending.save = null;
    if (save === null) return;
    // Drop a save issued before a reset: sending it now would restore
    // exactly what the reset cleared.
    if (generation !== this.generation) return;
    void this.run({ kind: "save", value: save.value, by: save.by });
  }
}
