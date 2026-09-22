// Federated `./TrialMatches` export (#104, part of #101). Renders the
// patient's trial matches grouped by `matchingType`, with a filter bar,
// inline detail view, and host-agnostic axios injection. The host
// supplies either `patientInfo` (inline payload — matches the existing
// CB contract) or `personId` (CTOMOP federation path added in #102).
import { useCallback, useEffect, useLayoutEffect, useMemo, useState, useRef } from "react";

/** Debounced, unless `immediate` — then the value passes straight through
 *  AND the held value is kept in step behind it.
 *
 *  The delay exists so a keystroke is not a request. A value that arrives
 *  from the patient's SAVED filters is not a keystroke: held back, it is
 *  absent for the first 400ms, so the Sponsor box reads "Acme", the badge
 *  counts it, and the request does not carry it.
 *
 *  Keeping the held value in step is the half that is easy to miss. Without
 *  it, the reader's first edit flips `immediate` off and hands back a held
 *  value that never caught up — the same window, arriving later. */
function useDebounced<T>(value: T, delay: number, immediate = false): T {
  const [debounced, setDebounced] = useState<T>(value);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (immediate) {
      // No timer to drop, here or in the branch below: React runs the
      // previous effect's cleanup before this one, so whatever was armed is
      // already cleared. Clearing it again was a second mechanism for the
      // same thing, and the suite confirmed both copies were dead.
      setDebounced(value);
      return;
    }
    timer.current = setTimeout(() => setDebounced(value), delay);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [value, delay, immediate]);
  return immediate ? value : debounced;
}
import { hashKey, QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { ActionTooltip } from "./bits";
import { FilterPanel } from "./FilterPanel";
import { TrialCard } from "./TrialCard";
import { TrialDetailPage } from "./TrialDetailPage";
import { Pagination } from "./Pagination";
import { SortControl } from "./SortControl";
import { SuitabilityPreferences } from "./SuitabilityPreferences";
import type { SegmentSource } from "./SegmentedControl";
import { ViewModeControl } from "./ViewModeControl";
import { TrialsGraph } from "./TrialsGraph";
import { TrialsMap } from "./TrialsMap";
import { EXPORT_URL_LIFETIME_MS, exportIsComplete, exportTrials } from "./api";
import { Tabs } from "./Tabs";
import { hasInlinePatient } from "./api";
import {
  baselineFilters,
  countActiveFilters,
  countryFor,
  normalizeFilterState,
  sameValue,
  userOwnedFilters,
} from "./filters";
import { MAX_TRIAL_IDS, canEditFields } from "./state";
import {
  DEFAULT_WEIGHT,
  WEIGHT_FIELDS,
  isUsableWeight,
  weightsToSave,
  type WeightKey,
} from "./weights";
import {
  DEFAULT_SORT,
  PAGE_SIZE,
  sortOptionsFor,
  tabValueForType,
  tabsFor,
  wideControlsRow,
  type TabValue,
} from "./listChrome";
import {
  canReadAdvanced,
  useAdvancedEnrollments,
  useSavedFilters,
  useQueuedPatientFields,
  useWritableFields,
  useSetTrialState,
  useStateIds,
  useTrials,
  useTrialsGraph,
} from "./hooks";
import { injectStyles, warnMissingExactTokens } from "./injectStyles";
import { ACTION_TOOLTIPS } from "./tooltips";
import type {
  FilterState,
  PatientInfo,
  TabCounts,
  TrialMatch,
  TrialMatchesProps,
} from "./types";

type StateKind = "favorites" | "registered";
/** Which trials have a write in flight, and which have one that failed. */
interface WriteState {
  pending: string[];
  failed: string[];
}

/** The stable half of an inline payload's identity, across the spellings a
 *  host may use. EXACT's own `PatientInfo` is camelCase over the wire and
 *  names the patient `externalId`; ht-phr's payload carries `person_id` and
 *  `id` as well. With none of them there is nothing stable to key on and the
 *  whole payload has to stand in — a refresh then reads as a new patient,
 *  which is the safe direction to be wrong in. */
function inlinePatientHandle(
  patientInfo: PatientInfo | null | undefined,
  patientInfoKey: string | null,
): string {
  for (const field of [
    "personId",
    "person_id",
    "externalId",
    "external_id",
    "patientId",
    "patient_id",
    "id",
  ]) {
    const value = patientInfo?.[field];
    if (typeof value === "string" || typeof value === "number") return String(value);
  }
  return String(patientInfoKey ?? "");
}

/** The four weights with any that are at the server's default removed. */
function atDefaultsDropped(filters: FilterState): FilterState {
  const out: FilterState = {};
  for (const { key } of WEIGHT_FIELDS) {
    out[key] = isUsableWeight(filters[key]) && filters[key] !== DEFAULT_WEIGHT
      ? filters[key]
      : undefined;
  }
  return out;
}

/** The weights the READER owns: set here, or loaded from their own row.
 *
 *  Not "every weight in `filters`". A host may mount with weights of its own
 *  in `initialFilters`, and those are the host's scope for this mount, not the
 *  reader's standing preference — stored, they would outlive the mount that
 *  asked for them and follow the reader to hosts that did not. This is the
 *  same distinction `userOwnedFilters` draws for the panel's fields, drawn
 *  from the same `owned` set. */
function ownedWeights(filters: FilterState, owned: ReadonlySet<string>): FilterState {
  const out: FilterState = {};
  for (const { key } of WEIGHT_FIELDS) {
    if (owned.has(key) && isUsableWeight(filters[key])) out[key] = filters[key];
  }
  return out;
}

/** A save payload that carries both halves of what is remembered: the panel
 *  fields the reader owns, and the weights. Either saver alone would retire
 *  the other's queued write — see `handleWeightsChange`. */
function withWeights(
  owned: FilterState,
  from: FilterState,
  ownedKeys: ReadonlySet<string>,
): FilterState {
  return { ...owned, ...ownedWeights(from, ownedKeys) };
}

function TrialMatchesInner({
  apiClient,
  patientInfo,
  personId,
  initialFilters,
  onTrialSelect,
  renderMap,
  state,
  preferences,
}: Omit<TrialMatchesProps, "queryClient">) {
  useEffect(() => {
    injectStyles();
  }, []);

  /** This mount's own root, for the canary below. */
  const exactRootRef = useRef<HTMLDivElement>(null);
  const canarySung = useRef(false);
  // The canary: did `exact.css` reach the page this list is on?
  //
  // Run after `injectStyles`, never before — the sheet has to be in the
  // document for the answer to mean anything — and against THIS mount's
  // root rather than the first `.exact-root` in the document: a second
  // EXACT instance that is fine would otherwise answer for one that is not,
  // and a root inside a shadow tree is not in `document` at all, where a
  // head-injected sheet cannot reach it either.
  //
  // No dependency list, because the root is not there on every first render
  // (the list renders a plain notice while the profile is loading); the
  // latch is what keeps it to one look.
  //
  // `DEV` is stripped when the REMOTE is built, not when a host is — the two
  // are compiled separately, which is the point of a remote. So this ships
  // disabled in the remote's production build, and a production host wired
  // to a development remote does get the warning. That host is
  // misconfigured, and a line in the console is the friendliest thing that
  // can happen to it.
  //
  // Not under the unit tests, whose `MODE` is "test": jsdom parses
  // stylesheets and resolves nothing from them, so every token reads empty
  // there and the canary would cry on every rendering test while meaning
  // nothing. What it checks is covered directly in `injectStyles.test.tsx`.
  useEffect(() => {
    if (canarySung.current || !exactRootRef.current) return;
    if (!import.meta.env.DEV || import.meta.env.MODE === "test") return;
    canarySung.current = true;
    warnMissingExactTokens(exactRootRef.current);
  });

  // Normalized on the way in: a host compiles separately, so a pre-#428
  // `trialPurpose` string in `initialFilters` is not caught by `tsc` here
  // and would spread into its own letters on the first click (#428).
  const [filters, setFilters] = useState<FilterState>(() =>
    normalizeFilterState(initialFilters),
  );
  // The id, and the list row when there is one. A trial can be opened from
  // the graph, which draws up to fifty trials while the list holds one page —
  // so "which trial" is always answerable and "which row" is not.
  const [selectedTrial, setSelectedTrial] = useState<{
    trialId: number;
    row?: TrialMatch;
  } | null>(null);
  // Seeded from the host's `initialFilters.type` rather than defaulted: the
  // prop is public API, and a host that mounts the remote asking for the
  // potential subset must not silently get the default tab's result set.
  const [activeTab, setActiveTab] = useState<TabValue>(() =>
    tabValueForType(initialFilters?.type),
  );
  const [sort, setSort] = useState<string>(initialFilters?.sort ?? DEFAULT_SORT);
  /** Whether the sort is being walked with the arrow keys; see
   *  `handleSortChange`. */
  const [sortTyping, setSortTyping] = useState(false);
  const [page, setPage] = useState(1);
  const [filtersOpen, setFiltersOpen] = useState(false);

  // Reset detail view when patient context changes so we don't keep a
  // stale trial open from a previous patient. We key on a stable derived
  // identifier (`personId` or the JSON-serialised payload) instead of the
  // `patientInfo` reference directly — otherwise a host that re-creates
  // the payload object on every render (the default in React without
  // `useMemo`) would collapse the detail view on every parent re-render.
  const patientInfoKey = useMemo(
    () => (patientInfo ? JSON.stringify(patientInfo) : null),
    [patientInfo],
  );

  // BOTH props, not the precedence winner.
  //
  // `patientIdentity` answers "which prop names this patient", which is the
  // right question for the matcher and the wrong one for a cache key. With
  // both supplied it resolves to the inline payload — so two readers whose
  // payload is the same minimal `{disease}` share a key, and the second one
  // is served the first one's bookmarks for as long as they stay fresh.
  // A cache key wants maximum discrimination: any difference in either prop
  // is a different key.
  const stateKey = `${personId ?? ""}|${patientInfoKey ?? ""}`;

  // The bar depends on whether a host gave us somewhere to keep bookmarks.
  const tabs = useMemo(() => tabsFor(state != null, activeTab), [state, activeTab]);
  // A host can take the adapter away — on logout, or when reconfiguring it.
  // The tab the reader was on then stops existing, and falling back only in
  // `activeTabDef` would list the default tab's trials while no tab in the
  // bar is marked current: the reader is somewhere the UI cannot name.
  // Adjusting during render rather than in an effect, so the request that
  // goes out is the one the visible tab describes.
  if (!tabs.some((t) => t.value === activeTab)) {
    setActiveTab(tabs[0].value);
  }
  const activeTabDef = tabs.find((t) => t.value === activeTab) ?? tabs[0];

  const favorites = useStateIds(state, "favorites", stateKey);
  const registered = useStateIds(state, "registered", stateKey);
  // Not a display concern: this is what stops the register control being
  // drawn for a trial whose enrollment a study team has already advanced,
  // where "I'm Interested" would write `registered` over `entered`.
  const advanced = useAdvancedEnrollments(state, stateKey);
  // The descriptor is about the patient, not the trial, so it is read here
  // once rather than on every detail page. Its absence is the safe state:
  // until it arrives, no row draws a control.
  const writableFields = useWritableFields(state, stateKey);
  const patientFields = useQueuedPatientFields(state, stateKey);
  // Saved filters. Applied over the host's `initialFilters` rather than in
  // place of them: the seeded country is the baseline the reader never chose,
  // and a saved set that omits it must not silently widen the search to every
  // country. Keys the reader did save win.
  // Which fields are the reader's rather than the host's. Sticky: seeded from
  // whatever was loaded, added to on every save, and emptied by Reset. Without
  // it a saved field that happens to equal the current baseline would look
  // like host scope on the next edit and be dropped. See `userOwnedFilters`.
  const ownedFields = useRef<Set<string>>(new Set());
  // `state` carries the settings too, and wins; `preferences` is for a host
  // that can answer for the settings and nothing else — see the prop's own
  // doc. With neither, they go to this browser's `localStorage` — which
  // outlives the session, and is per browser rather than per patient's
  // record.
  const preferenceStore = state ?? preferences;
  // Named, because a host that switches from one to the other keeps the same
  // patient and the same "there is a store" — and the transport is otherwise
  // blind to a new object for the same patient, on purpose. The two stores
  // do not agree about conditional writes, and the writer's queue belongs to
  // whichever it was built for.
  const preferenceSource = state != null ? "state" : "preferences";
  // What the reader has overruled since a saved set could last have landed.
  //
  // NOT `ownedFields`: that is cumulative and answers "whose field is this",
  // which is a different question from "did they touch it while this read was
  // in flight". Two places where the two come apart, both found in review:
  // Reset empties `ownedFields`, so a stale read landing after it would have
  // passed every value straight back into the panel that was just cleared;
  // and a host swapping one store for another keeps `ownedFields` (same
  // patient), so the new store's saved values would be suppressed as though
  // the reader had just typed them.
  const overruled = useRef<{ fields: Set<string>; all: boolean }>({
    fields: new Set(),
    all: false,
  });
  const savedFilters = useSavedFilters(
    preferenceStore,
    stateKey,
    (saved, applied) => {
      // Not cleared here: a writer has exactly one read, so the only veto
      // that can outlive this callback belongs to the NEXT writer's read,
      // and the effect keyed on `epoch` clears it for that one. Clearing it
      // in both places is a second mechanism for the same thing, and the one
      // below is the one that also covers a read which never calls back.
      const veto = overruled.current;
      // Snapshotted BEFORE the claim below, for the weight overlay further
      // down. That overlay exists to protect a weight the reader chose while
      // this read was in flight — one they owned BEFORE the row landed. The
      // claim is about to hand ownership to every key the ROW carries, and a
      // weight owned only because the row just claimed it is the opposite
      // case: its value in the panel is still the host's mount-time seed, so
      // overlaying it would put the host's number over the reader's saved one
      // and then store it as theirs (#546).
      const ownedBeforeThisRow = new Set(ownedFields.current);
      // The keys are claimed because ownership is what makes a filter
      // clearable later — except after a Reset, where this row describes a
      // panel that no longer exists and the transport has already discarded
      // it. Claiming them there hands the reader ownership of values they
      // just threw away, and a later unrelated edit would write the host's
      // baseline back as their standing preference.
      if (!veto.all) {
        for (const field of Object.keys(saved)) ownedFields.current.add(field);
      }
      // The VALUES are the half it does. `applied` is false when the reader
      // edited while this read was in flight, and merging the row over that
      // edit reverts it in front of them — measured: type "Acme" into
      // Sponsor during a slow read and the box reads "Stored" a moment
      // later, with the search already narrowed by a value they never
      // entered (#537).
      //
      // Held back is what they OVERRULED while this read was in flight, and
      // only that: the rest of the row is theirs too, saved earlier, and
      // dropping all of it would trade one silent change for another. Reset
      // is the exception, below — it overrules the whole row, because an
      // empty panel is what was asked for.
      const incoming: FilterState = applied
        ? saved
        : veto.all
          ? // Reset overrules the whole row: the reader asked for an empty
            // panel, and handing them a stale one back is the same silent
            // change in a louder place. The transport usually agrees — its
            // generation moves and the row is refused into `stored` and
            // everything behind the same guard (`believed`, the version) —
            // but not always: `PreferenceWriter.reset()` bumps only its OWN
            // generation and queues the transport's behind a write already
            // in flight. And a refused row is still RETURNED to us either
            // way. So this is the half that keeps it off the panel, and it
            // does not lean on the other one.
            //
            // No weights spread here. Reset keeps them, but it is
            // `handleFiltersReset` that does it, by putting them into the
            // panel; adding them again on this branch is at best a no-op
            // over the state they already sit in, and at worst writes the
            // older of two queued values back over the newer.
            {}
          : {
              ...Object.fromEntries(
                Object.entries(saved).filter(([field]) => !veto.fields.has(field)),
              ),
              // The weights on top, and this one is not a no-op: the veto
              // knows what the reader touched during THIS read, while a
              // weight they own was theirs before it — and a row that
              // predates the write still on its way must not put the old
              // score back (#517).
              //
              // `ownedBeforeThisRow`, not the set as it is now: see there.
              ...ownedWeights(filters, ownedBeforeThisRow),
            };
      // A saved trial type came from THIS patient's storage, so it is this
      // patient's choice. Without claiming it the staleness rule — which
      // exists to expire a type picked for someone else — would mask the very
      // type just loaded, and a later edit would overwrite it.
      if (saved.trialType !== undefined) setTrialTypeOwner(patientIdentity);
      // Normalized for the same reason as the initial state, and the more
      // likely source: this is what the PREVIOUS build of this remote saved,
      // when `trialPurpose` was a single string.
      setFilters((current) => normalizeFilterState({ ...current, ...incoming }));
    },
    preferenceSource,
  );

  // Whether the panel has been touched since the last time a saved set
  // could have arrived — keyed on the same attempt the gate is, and
  // adjusted during render like `ownedFields` below.
  //
  // Three attempts to get this right, each wrong in its own way. One-way,
  // it stayed true across a patient switch, so the NEXT patient's saved set
  // came through the debounce after all. Keyed on WHICH patient, coming
  // back to them turned it on again and their re-loaded set was debounced
  // for the same 400ms. Keyed on the patient KEY, it missed a host handing
  // over an adapter mid-session — the key never changes there, and that is
  // precisely why the gate next to it is keyed on the writer instead.
  const [typed, setTyped] = useState({ epoch: savedFilters.epoch, yes: false });
  if (typed.epoch !== savedFilters.epoch) {
    setTyped({ epoch: savedFilters.epoch, yes: false });
  }
  const typing = typed.yes;

  useEffect(() => {
    setSelectedTrial(null);
    setPage(1);
  }, [personId, patientInfoKey]);

  const diseaseCode = useMemo(() => {
    const d = (patientInfo as Record<string, unknown> | null | undefined)?.["disease"];
    return typeof d === "string" ? d : undefined;
  }, [patientInfo]);

  // `country` and `trialType` are DERIVED, not stored.
  //
  // Seeding them into filter state — even during render — put a request on
  // the wire before the seeding took effect: the component suite caught two
  // requests on every mount with a patient country, the first of them
  // unscoped. A render-phase `setState` re-runs the component but does not
  // un-send what the query observer has already been told to fetch.
  //
  // `country` also no longer has a control at all — its dropdown could not
  // match anything (#430) — so storing it was storing a copy of a fact.
  // `trialType` does have one; what is derived there is only whether the
  // stored choice still applies to the patient on screen.
  const patientCountry = useMemo(() => {
    const c = (patientInfo as Record<string, unknown> | null | undefined)?.["country"];
    return typeof c === "string" && c.trim() ? c.trim() : undefined;
  }, [patientInfo]);
  const country = countryFor(patientCountry, initialFilters);

  // A trial type belongs to the PATIENT it was chosen for, not to their
  // disease. Carried into someone else it narrows their list by a choice
  // they never made — and across diseases it is worse, because the option
  // does not exist in their list at all and `by_trial_type` has no leniency
  // for a value that is not there: an empty result set from a control
  // rendering blank. Keyed on the disease this leaked between any two
  // patients who shared one.
  //
  // `hasInlinePatient` decides which prop names the patient, because it is
  // the same function the request uses to decide which one it sends.
  const patientIdentity = hasInlinePatient(patientInfo)
    ? patientInfoKey
    : personId != null
      ? String(personId)
      : null;
  // Who the reader is looking at, for anything that must survive the payload
  // being REFRESHED. `patientIdentity` and `stateKey` both hash the whole
  // inline payload, so a host re-reading the profile — same person, new
  // object — changes them; a draft keyed on that is discarded for nothing.
  // The person's own id is the stable half when the payload carries one.
  const patientHandle =
    personId != null ? String(personId) : inlinePatientHandle(patientInfo, patientInfoKey);

  // "Unclaimed" is `undefined`, NOT `null` — and the distinction is
  // load-bearing, because `null` is a real owner here: `patientIdentity` is
  // `null` for a host placeholder like `patientInfo={}`, or for the render
  // before the profile arrives, and the panel is live in that window. While
  // the two shared a sentinel, a type picked there claimed `null`, read back
  // as unclaimed, never went stale, and followed the reader into every
  // patient afterwards, across diseases included.
  //
  // Unclaimed is also how a host's `initialFilters.trialType` outlives a
  // patient arriving a render later: nobody has claimed it, so nothing makes
  // it stale.
  const [trialTypeOwner, setTrialTypeOwner] = useState<string | null | undefined>(
    undefined,
  );
  const trialTypeIsStale =
    trialTypeOwner !== undefined && trialTypeOwner !== patientIdentity;
  // A stale choice falls back to the BASELINE's type, not to nothing.
  //
  // What goes stale is the reader's own pick, which was made for one
  // patient. `initialFilters.trialType` is a different thing — a scope the
  // host set when it mounted the remote — and `baselineFilters` already
  // treats a host filter as something Reset restores rather than discards.
  // Dropping to `undefined` threw it away silently, and cost a second click
  // besides: Reset wrote the masked baseline into state while clearing the
  // owner, so the next render's unmasked baseline disagreed with what had
  // just been stored, the badge counted that disagreement and the button
  // stayed armed.
  //
  // Falling back to the baseline's value makes the two agree by
  // construction, so the baseline itself needs no mask at all.
  const trialType = trialTypeIsStale
    ? initialFilters?.trialType
    : filters.trialType;

  const effectiveFilters = useMemo(
    () => ({ ...filters, country, trialType }),
    [filters, country, trialType],
  );

  const baseline = useMemo(
    () => baselineFilters(patientCountry, initialFilters),
    [patientCountry, initialFilters],
  );
  const activeFilterCount = countActiveFilters(effectiveFilters, baseline);

  const debouncedTitle = useDebounced(filters.searchTitle, 400, !typing);
  const debouncedTreatment = useDebounced(filters.searchTreatment, 400, !typing);
  const debouncedSponsor = useDebounced(filters.sponsor, 400, !typing);
  const debouncedDistance = useDebounced(filters.distance, 400, !typing);
  const debouncedDistanceUnits = useDebounced(filters.distanceUnits, 400, !typing);
  // Shorter than the 400ms the text boxes take: this is one keypress per
  // option, not a word being typed.
  const debouncedSort = useDebounced(sort, 250, !sortTyping);
  // Ownership is per patient: what the previous one had saved is not evidence
  // about this one. Kept in step with `stateKey` — the same key the saved-set
  // load is keyed on, so the clear lands before that patient's answer does.
  // (The reader's session FILTERS are deliberately kept across the switch; it
  // is the claim about who owns them that does not carry over.)
  useEffect(() => {
    // The weights are the exception, and for the same reason the session
    // filters are kept across the switch: they are the reader's, not the
    // patient's. Cleared here, they stayed live on the wire and on the
    // trigger while quietly ceasing to be owned — so the next Reset dropped
    // them from the page and wrote nothing back, which is exactly the
    // silent change this control is not allowed to make.
    ownedFields.current = new Set(
      WEIGHT_FIELDS.map(({ key }) => key).filter((key) => ownedFields.current.has(key)),
    );
  }, [stateKey]);
  // Per READ, not per patient. A read that fails or is cancelled never calls
  // back, so without this its veto outlives it — and the next read, from a
  // store the host swapped in for the same patient, would find fields marked
  // as overruled that nobody touched while IT was in flight.
  useEffect(() => {
    overruled.current = { fields: new Set(), all: false };
  }, [savedFilters.epoch]);

  // NOTE on switching patients in place: the reader's session filters are
  // deliberately KEPT, and only the trial-type ownership is re-decided (see
  // `setTrialTypeOwner` below and "a trial type belongs to the patient it was
  // chosen for"). Review flagged the saved-set overlay as leaking filters from
  // one patient to the next; resetting to the seed instead breaks that tested
  // decision. The filters belong to the reader's search, not to the patient —
  // what belongs to the patient is the SAVED set, and the overlay applies the
  // new patient's own saved keys over the top.

  const setFavorite = useSetTrialState(state, "favorites", stateKey);
  const setRegistered = useSetTrialState(state, "registered", stateKey);

  // What each trial's writes are doing, remembered here rather than read
  // off the mutation.
  //
  // One `useMutation` stands in for a per-trial operation, and it answers
  // only for the LAST one submitted. Both of its flags were wrong for the
  // same reason:
  //
  //   - `isError` answers "did the last write fail", not "did the write for
  //     THIS trial fail". Filtering by `variables.trialId` addresses the
  //     message to the right trial but does not make it durable: a rejection
  //     that lands after the reader has moved on has no trial on screen to
  //     belong to, and the next click on any trial discards it. Both cases
  //     end with a patient who was shown "Saving…" and never told otherwise.
  //
  //   - `isPending` with the same filter goes FALSE for trial A as soon as a
  //     write for trial B is submitted, because `variables` is B's. Reopen A
  //     and its button is live again, with A's PATCH still on the wire —
  //     the two-writes-in-flight race the guard exists to prevent.
  //
  // And it belongs to a PATIENT as much as to a trial. A host can swap
  // `personId` or the inline payload at any moment, including while a write
  // is on the wire; unkeyed, the previous patient's failure became this
  // one's error message, and a trial id both have in common stayed busy.
  //
  // Two mechanisms, and they are not the same one twice: the record is
  // DROPPED when the patient changes, and a callback that arrives after the
  // change finds a key that no longer matches and writes nothing. Neither
  // covers the other's case.
  const [writes, setWrites] = useState<
    { key: string } & Record<StateKind, WriteState>
  >(() => ({
    key: stateKey,
    favorites: { pending: [], failed: [] },
    registered: { pending: [], failed: [] },
  }));
  // Adjusted during render, like the tab fallback above, so the messages on
  // screen belong to the patient on screen. An effect would paint one frame
  // of the previous patient's failures first.
  if (writes.key !== stateKey) {
    setWrites({
      key: stateKey,
      favorites: { pending: [], failed: [] },
      registered: { pending: [], failed: [] },
    });
  }
  const mark = (
    key: string,
    kind: StateKind,
    field: keyof WriteState,
    trialId: string,
    on: boolean,
  ) =>
    setWrites((prev) => {
      if (prev.key !== key) return prev;
      const list = prev[kind][field];
      if (list.includes(trialId) === on) return prev;
      return {
        ...prev,
        [kind]: {
          ...prev[kind],
          [field]: on ? [...list, trialId] : list.filter((id) => id !== trialId),
        },
      };
    });
  const write = (
    kind: StateKind,
    mutation: typeof setFavorite,
    trialId: string,
    on: boolean,
  ) => {
    if (writes[kind].pending.includes(trialId)) return;
    const key = stateKey;
    mark(key, kind, "pending", trialId, true);
    mutation.mutate(
      { trialId, on },
      {
        onError: () => mark(key, kind, "failed", trialId, true),
        onSuccess: () => mark(key, kind, "failed", trialId, false),
        onSettled: () => mark(key, kind, "pending", trialId, false),
      },
    );
  };

  const stateCounts = {
    favorites: favorites.data?.length,
    registered: registered.data?.length,
  };

  // Which ids narrow the list, if the active tab is one of the state tabs.
  //
  // `undefined` while the ids are still loading — NOT `[]`, which the server
  // reads as "none of them" and would answer with an empty list a moment
  // before the real one arrives. The query waits instead.
  const stateTab = activeTabDef.needsState;
  const stateIdsQuery = stateTab === "registered" ? registered : favorites;
  const savedIds = stateTab ? (stateIdsQuery.data as string[] | undefined) : undefined;
  // The server refuses a list past its cap, so sending one means the tab
  // simply never loads while its badge cheerfully reports the count. Say
  // what happened instead — the plan called for an explicit degradation
  // here and this is it.
  const tooManySavedIds = savedIds != null && savedIds.length > MAX_TRIAL_IDS;
  const trialIds = tooManySavedIds ? undefined : savedIds;
  // What a set of counts is ABOUT: this patient, and this tab.
  //
  // Coarser than the query key on purpose. A sort or a page turn does not
  // change what the counts describe, and blanking the bar through every
  // refetch would trade one flicker for a worse one — the rows stay up
  // through those windows too, dimmed. The filters are left out on the same
  // terms, and that one IS a real difference rather than a no-op: a filter
  // change does change `tabCounts`, so the badge holds the previous
  // response's number until the new answer lands, exactly as the rows do.
  // What it does NOT leave out is the tab: `itemsTotalCount` belongs to the
  // tab that asked, so a deep-linked Fully-matched response painted the
  // corpus badge with the eligible-only total on the way back.
  //
  // Through `hashKey`, the function React Query hashes the query key with,
  // and not `JSON.stringify`: that one is sensitive to the ORDER of an
  // object's keys and `hashKey` sorts them. A host handing us the same
  // `patientInfo` spelled `{ref, disease}` instead of `{disease, ref}` gets
  // the same cache entry — so nothing refetches — and would have got a
  // different scope here, which never then agrees with the stored one. The
  // bar goes blank and stays blank, with the rows still on screen. Measured
  // before this line: no badge at 100ms, 500ms, 1s, 2s, and recovery only
  // when the reader changes the sort.
  //
  // `hashKey` and not the host's client, which is the other way to spell
  // this (`queryClient.defaultQueryOptions({ queryKey }).queryHash` follows
  // a custom `queryKeyHashFn`). Deliberate: a host installing a COARSER
  // hash has a cache that answers one patient with another's response, and
  // a scope derived from it would agree and paint those counts. Computed
  // here it disagrees instead, and the bar goes quiet. Blank under a broken
  // cache is the right way round; the whole point of this file is that a
  // wrong number costs more than a missing one.
  //
  // The saved ids are deliberately NOT in here: a narrowed response is
  // refused outright below, so their contents could do no work — and it is
  // 500 ids through a hash on every render to reach the same answer.
  const countScope = hashKey([personId ?? null, patientInfo ?? null, activeTab]);
  // `isPending`, not `data === undefined`: a rejected fetch also has no
  // data, and treating that as "still loading" left the tab on
  // "Loading trials…" for ever, with the trials query disabled so even its
  // error branch could never speak.
  const waitingForIds = stateTab != null && stateIdsQuery.isPending;
  // `data === undefined` too: a failed REFETCH keeps the ids it had, and
  // narrowing by slightly stale bookmarks beats blanking the tab and saying
  // it could not be loaded when it could.
  const idsFailed =
    stateTab != null && stateIdsQuery.isError && stateIdsQuery.data === undefined;
  // Mutually exclusive by construction: a query that has rejected is no
  // longer pending. Guarding the loading line with `!idsFailed` as well
  // would be a second mechanism for the same thing, and would make the
  // first untestable — which is how it was written the first time.
  const queryFilters = useMemo(
    () => ({
      ...effectiveFilters,
      searchTitle: debouncedTitle,
      searchTreatment: debouncedTreatment,
      sponsor: debouncedSponsor,
      distance: debouncedDistance,
      distanceUnits: debouncedDistanceUnits,
      type: activeTabDef.param,
      sort: debouncedSort as FilterState["sort"],
      // A weight sitting at the server's own default says nothing, and the
      // wire already omits it. Left in here it would still move the key:
      // opening the dialog and pressing Save unchanged wrote 25s over
      // absences, which is a different FilterState with identical params —
      // a second full matcher run, and the reader thrown back to page 1.
      ...atDefaultsDropped(effectiveFilters),
    }),
    [
      effectiveFilters,
      debouncedTitle,
      debouncedTreatment,
      debouncedSponsor,
      debouncedDistance,
      debouncedDistanceUnits,
      activeTabDef.param,
      debouncedSort,
    ],
  );

  // What a COUNT is true for: this patient and these filters, minus the
  // three things that narrow or order a request without changing how many
  // trials match — the tab's own `type`, the saved ids a state tab sends,
  // and the sort. A different question from `countScope`, which asks whether
  // the response in hand belongs to the view on screen.
  const { type: _tabNotInCorpus, sort: _orderNotInCorpus, ...corpusFilters } =
    queryFilters;
  const corpusKey = hashKey([personId ?? null, patientInfo ?? null, corpusFilters]);

  const query = useTrials({
    apiClient,
    patientInfo,
    personId,
    filters: queryFilters,
    page,
    limit: PAGE_SIZE,
    trialIds,
    scope: countScope,
    corpus: corpusKey,
    // `!idsFailed` too: without it a failed Favorites read still fires an
    // ordinary unfiltered search behind the error message — rows nobody
    // shows, and a full matcher run to produce them.
    enabled:
      !waitingForIds &&
      !tooManySavedIds &&
      !idsFailed &&
      // Held until the saved set is in. Otherwise the opening search goes
      // out with the defaults and a second one follows once it lands — two
      // matcher runs, and a flash of unfiltered results for a reader who
      // had narrowed them. A failed read releases it too, so an unreachable
      // PROMOP costs the defaults, not the list.
      !savedFilters.pending,
  });

  // While the ids are loading, the rows on screen belong to the previous
  // tab. React Query is serving them from its cache — `trialIds` is still
  // `undefined`, which is the *default* tab's query key — so this is not a
  // request that can be prevented, it is a render that must not happen:
  // showing the eligible list under the Favorites heading says those trials
  // are bookmarked.
  // On a state tab, rows are shown only when they are THIS tab's rows.
  //
  // `waitingForIds` alone closed just the first half of the window: once
  // the ids arrive it goes false in the same render that changes the query
  // key, and `keepPreviousData` then hands back the previous key's rows —
  // so the eligible list was painted under the Favorites heading, which is
  // precisely the claim it was written to prevent. Worse on a second visit,
  // where the ids are already cached and the flag is false from the start.
  // `isPlaceholderData` alone, and only while the query can still resolve.
  //
  // Two corrections live in this line. An earlier version also tested
  // `isFetching`, added while the leak test was failing for an unrelated
  // reason (the test's fake server ignored the id filter, so the right and
  // wrong rows were identical). Once that was fixed the clause turned out
  // to be unnecessary — nothing could be made to leak without it — and it
  // is not free: it blanked the list on every background refetch, which
  // includes the window-focus one real hosts have on and the one that
  // follows every bookmark.
  //
  // And the two states that disable the query with nothing of their own to
  // resolve them are excluded, because a disabled query still resolves
  // `keepPreviousData`: with no cached data under the fallback key,
  // `isPlaceholderData` stays true for ever and nothing will ever fetch it,
  // so "Loading trials…" sat next to the error message permanently. (Both
  // do clear eventually — a refetch succeeds, a bookmark is removed — but
  // only on something the READER does.) `savedFilters.pending` disables it
  // too and is not excluded, because a timer resolves it either way; it is
  // in `staleResponse` below instead.
  const showingOtherTabsRows =
    stateTab != null && !idsFailed && !tooManySavedIds && query.isPlaceholderData;
  // `savedFilters.pending` belongs here too, not only in `enabled`.
  //
  // Disabling a query does not clear what it is holding: with a shared
  // QueryClient, or across a patient switch with placeholder data, the
  // previous rows stay on screen under "Loading trials…" — the unfiltered
  // flash the gate exists to prevent, arriving from the cache instead of
  // from the network.
  //
  // Everything painted FROM that response goes behind the same flag, which
  // is the part that took two goes to get right: the rows, the total, the
  // tab counts and the pager. A pager left live is not merely wrong-looking
  // — it is four clickable pages belonging to the previous patient, and
  // clicking one sends the NEXT patient's first search as `page=3`.
  // Written once: three places asked this question and each spelled it out
  // for itself, which is how the loading line came to disagree with the
  // message underneath it.
  const hasPatient = patientInfo != null || personId != null;
  // Two questions, not one.
  //
  // `staleResponse` — what is in hand describes a different view, or no
  // view at all. Everything painted FROM it goes behind this: the rows, the
  // total, the tab counts and the pager.
  //
  // `staleRows` adds the placeholder window on a state tab, where the rows
  // in hand belong to another tab.
  //
  // The PAGER needs a third answer, and neither flag is it. Hidden through
  // the whole placeholder window, it unmounts the button under the
  // reader's cursor on an ordinary page turn — a keyboard user is dropped
  // to `<body>` on every page they turn. Shown through it, a tab switch or
  // a filter change paints the PREVIOUS view's page count, and those
  // numbers are clickable: page 3 of a one-page view is a 404 from DRF,
  // recovered only afterwards. What distinguishes the two is whether the
  // count in hand was fetched for the view now on screen — see
  // `countIsForThisView` below.
  const staleResponse =
    savedFilters.pending || waitingForIds || idsFailed || tooManySavedIds;
  const staleRows = staleResponse || showingOtherTabsRows;
  const trials = staleRows ? [] : query.data?.results ?? [];
  // Both questions are asked of the RESPONSE, not of the state around it.
  // Two ways the two come apart, both measured:
  //
  //   - leave Favorites, change the sort, come back: `keepPreviousData`
  //     still holds the narrowed response, and the corpus badge was painted
  //     from counts taken over the saved ids — "Eligible & Potential, 1
  //     trial" against a corpus of 19.
  //   - switch patient with the new request in flight: the badge held the
  //     PREVIOUS patient's 19 for the whole matcher round trip.
  //
  // Neither is reachable by asking which tab is active or which patient is
  // mounted, because by then both have already changed.
  const countsDescribeThisView = query.data?.scope === countScope;
  const totalCount =
    staleResponse || query.data?.narrowed || !countsDescribeThisView
      ? null
      : query.data?.itemsTotalCount ?? null;
  const tabCounts =
    staleResponse || query.data?.narrowed || !countsDescribeThisView
      ? undefined
      : query.data?.tabCounts;

  // The last counts that described this corpus, kept for the tabs that
  // cannot ask for them. A state tab's request comes back narrowed to the
  // saved ids, so its counts are refused above — and the bar then empties
  // entirely, which is right when there is nothing true to show and wasteful
  // when there is. Switching tabs does not change the corpus.
  //
  // Remembered against the corpus the RESPONSE was fetched for, not the one
  // on screen. The two come apart for a whole round trip: `keepPreviousData`
  // holds the previous filters' response while the new ones are in flight,
  // and `countScope` lets it through on purpose — the rows it carries are
  // the rows being shown. Painting its count beside those rows is honest;
  // FILING it under the new filters is not, and it would then outlive the
  // window, on a state tab, as a number for a corpus nobody counted.
  //
  // One slot, so a reader who filters and comes back to the previous set
  // gets a blank until the answer lands rather than the number they had a
  // moment ago. Keeping more would mean keeping them per filter set, which
  // is unbounded; what this is for is the tab switch, which does not change
  // the corpus at all.
  const rememberedCounts = useRef<{ key: string; counts: TabCounts } | null>(null);
  if (
    tabCounts &&
    query.data?.corpus === corpusKey &&
    // Both fields, because this is unvalidated wire data and the corpus
    // count is their SUM. `barCounts` refuses to paint the NaN that a
    // missing one makes, but stored it would poison this slot and blank
    // every state tab for this corpus — a bad response outliving itself.
    Number.isFinite(tabCounts.eligible) &&
    Number.isFinite(tabCounts.potential)
  ) {
    rememberedCounts.current = { key: corpusKey, counts: tabCounts };
  }
  const countsForBar =
    tabCounts ??
    (rememberedCounts.current?.key === corpusKey
      ? rememberedCounts.current.counts
      : undefined);

  // Reset to the first page whenever the *effective* query changes — tab,
  // sort, or a filter that has finished debouncing. Adjusting state during
  // render rather than in an effect (the pattern React documents for derived
  // state) so the reset is part of the same render that changes the filter:
  // an effect would let one request go out for page N of the new filter
  // first, and a request for a page past the new end is a 404 from DRF's
  // paginator, not an empty list.
  //
  // Keyed on the debounced filters for the same reason: resetting the page
  // the instant a key is pressed would fire a request for page 1 of the
  // *previous* filter, which the user sees as a flash of unfiltered results.
  // The tab and the ids belong in this signature, not just the filters.
  // `queryFilters.type` is `undefined` for the default tab AND for both
  // state tabs — `JSON.stringify` drops undefined keys, so all three
  // hashed identically and switching between them never reset the page.
  // A reader on page 2 then asked for page 2 of their bookmarks.
  // `stateKey` is in here because the patient is part of "the view": React
  // Query's own key carries it, so a switch between two patients who share
  // a country and a trial type leaves everything else identical — and the
  // pager, which asks this question to decide whether the count in hand is
  // this view's, would have painted the previous patient's page count.
  // One spelling, two readers. Written twice, the export's copy stops
  // tracking the day a fifth dimension joins the list's — which is the bug
  // the comment above is about, reopened from the other end.
  const viewKeyFor = (forFilters: FilterState) =>
    JSON.stringify([stateKey, forFilters, activeTab, trialIds ?? null]);
  const queryKey = viewKeyFor(queryFilters);

  // The export is a file, not a view: no cache, no retry, and a status the
  // reader can see. React Query would serve the same bytes back on a second
  // click, which for a download means the reader gets a stale file.
  const [exportState, setExportState] = useState<
    "idle" | "working" | "failed" | "incomplete"
  >("idle");
  // Which view is on screen RIGHT NOW, for an export that settles later. A
  // reader who exports Favorites and then switches tabs must not be handed the
  // previous tab's rows, nor shown a failure under a view they never exported.
  // Written during render, so by the time an in-flight export resolves it
  // already describes where the reader is.
  const viewKeyRef = useRef<string>("");
  // On a state tab, `trialIds` is `undefined` in three different ways — the
  // saved ids are still loading, the read failed, or there are more than the
  // server will filter by. The list already refuses to search in all three.
  // The export has to refuse too: sending no ids reaches the same endpoint
  // with the same filters and returns the whole matched corpus, delivered as
  // a file the tab has labelled "Favorites". That is the wrong-set answer the
  // server's own `type=favorites` refusal exists to prevent, arrived at from
  // the other side.
  // Two ways the export cannot answer. A state tab whose ids are unavailable
  // is the first. The second is no patient at all — a supported mode for the
  // list, but the remote never sends `?type=all`, so every export in it is a
  // guaranteed 400 surfaced as "please try again", which is advice that cannot
  // work.
  const exportUnavailable =
    (stateTab != null && trialIds === undefined) ||
    (!hasInlinePatient(patientInfo) && personId == null);
  const exportUnavailableReason =
    stateTab != null && trialIds === undefined
      ? "Your saved trials aren't available right now, so this tab can't be exported."
      : "An export needs a patient, and this view has none.";
  const handleExport = async () => {
    if (exportUnavailable) return;
    setExportState("working");
    const issuedFor = exportViewKey;
    try {
      const { blob, filename } = await exportTrials({
        apiClient,
        patientInfo,
        personId,
        // What the reader is looking at, tab included — the file has to be
        // the answer to the question on screen, not to an unnarrowed one.
        // The narrowing comes from the list, the order from the control:
        // inside the 250ms hold those disagree, and the segment is the half
        // the reader can see. See `exportFilters`.
        filters: exportFilters,
        trialIds,
      });
      // The view moved while this was in flight. Dropping it costs the reader
      // a click; handing it over downloads one tab's rows while the screen
      // shows another's, under a filename that says neither.
      if (viewKeyRef.current !== issuedFor) return;
      const complete = await exportIsComplete(blob);
      // Checked again on the far side of that await: scanning a large export
      // takes long enough for the reader to change tabs while it runs, and the
      // file would then be handed over under a view it does not describe.
      if (viewKeyRef.current !== issuedFor) return;
      if (!complete) {
        // The server said so on the last line, because its 200 was spent
        // before the first row. Saving it anyway hands over a file that looks
        // complete and is not — and this one goes to an appointment.
        setExportState("incomplete");
        return;
      }
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      // Attached before the click: Firefox has historically ignored a
      // download click on an anchor that is not in the document.
      document.body.appendChild(link);
      link.click();
      link.remove();
      // Revoked on a timer, not immediately. Some browsers — Safari the one
      // that bites — start the download after the click handler has returned,
      // and a URL revoked by then yields an empty or failed download. Not
      // never, either: the blob is held for the life of the document
      // otherwise, and an export of a few thousand trials is not small.
      setTimeout(() => URL.revokeObjectURL(url), EXPORT_URL_LIFETIME_MS);
      setExportState("idle");
    } catch {
      if (viewKeyRef.current !== issuedFor) return;
      setExportState("failed");
    }
  };

  // WHO the rows are about is carried by `stateKey`, inside the key both of
  // these are built from: a host switching patients with the filters
  // untouched would otherwise leave the key identical, and the previous
  // patient's file would pass both staleness checks and be handed over under
  // the new patient's name — the wrong rows, about the wrong person.
  //
  // The export asks for the order the CONTROL shows, not the one the list has
  // caught up to: an arrow-key change is held back 250ms for the list and not
  // at all for the segment, so for that quarter second they disagree, and a
  // file the reader asks for then should answer the order they can see.
  const exportFilters = { ...queryFilters, sort: sort as FilterState["sort"] };
  // The order is the one dimension deliberately LEFT OUT of the export's key.
  //
  // Keyed on it, an export dies at both edges of that 250ms: held back, the
  // key moves when the hold lands, and taken from the control, it moves on
  // the keypress while a file is already on the wire. Either way the arriving
  // file is read as belonging to a view the reader has left, so it is dropped
  // and the button returns to idle — a click, a wait, and nothing said. The
  // keypress version is worse: idle again means enabled again, so a second
  // full-corpus export can go out beside the first.
  //
  // Leaving it out is sound because the sort cannot change WHICH trials are
  // in the file: an export is the whole matched set, never the page, so the
  // order moves rows around inside the CSV and takes none out. Everything
  // that does change the membership — tab, patient, filters, the saved-id
  // set — is still in the key and still drops a file that has stopped
  // describing the screen.
  const { sort: _orderNotInKey, ...exportKeyFilters } = queryFilters;
  const exportViewKey = viewKeyFor(exportKeyFilters);
  viewKeyRef.current = exportViewKey;
  // The failure belonged to the view it happened in. Left standing, it sits
  // under a list the reader has since changed and reads as a fresh failure of
  // what they are looking at now.
  //
  // The order is the exception, and deliberately: it is not in this key, so
  // re-sorting leaves the message up. It still describes the same rows — the
  // sort reorders the file, it does not change what is in it — and clearing
  // it here would mean keying on the order again, which is what drops the
  // reader's file mid-flight.
  useEffect(() => {
    setExportState("idle");
  }, [exportViewKey]);

  // Which view the count in hand was fetched for. `queryKey` excludes the
  // page number by construction (it is what resets the page when anything
  // else changes), so "the same key" means "the same view, a different
  // page" — exactly the window where the previous count is still the right
  // one. Recorded in an effect rather than during render; the fresh-data
  // path does not consult it.
  const countKey = useRef<string | null>(null);
  useEffect(() => {
    if (!query.isPlaceholderData) countKey.current = queryKey;
  }, [query.isPlaceholderData, queryKey]);
  // `!query.isPlaceholderData ||` first, and not as a shortcut: the ref is
  // written exactly when that is false and read exactly when it is true.
  // Those two being disjoint is what makes reading a ref during render
  // safe here — the render that reads it never needs a write that has not
  // happened yet. Drop the short-circuit and the fresh-data path starts
  // consulting a ref its own effect has not written yet; the pager would
  // blank for that frame, and only a second render (React Query settling
  // `fetchStatus`) would repaint it, which is why the suite cannot see the
  // difference.
  const countIsForThisView =
    !query.isPlaceholderData || countKey.current === queryKey;
  // The server's own page total (`count`), not `ceil(items / PAGE_SIZE)`:
  // the two agree only while the client's page size matches what the
  // server actually applied, and the server is the one that decides.
  const pageCount =
    staleResponse || !countIsForThisView ? 0 : query.data?.count ?? 0;
  const [lastQueryKey, setLastQueryKey] = useState(queryKey);
  if (lastQueryKey !== queryKey) {
    setLastQueryKey(queryKey);
    setPage(1);
  }

  // A page past the end is a 404 (`NotFound` from PageNumberPagination), and
  // `keepPreviousData` leaves the stale page — and its stale pager — on
  // screen, so every further click reproduces it. Recover to a page that
  // exists. Reachable when the host restores a `?page=` from its own URL, or
  // when a click lands during the window where the pager is still showing
  // the previous response's page count.
  const isPageNotFound =
    query.isError &&
    (query.error as { response?: { status?: number } })?.response?.status === 404;
  useEffect(() => {
    if (isPageNotFound && page !== 1) setPage(1);
  }, [isPageNotFound, page]);

  const handleTabChange = (next: TabValue) => setActiveTab(next);
  // Arrows choose as they move, so walking from "Suitability" to "Distance"
  // passes through "Matching" — and every stop on the way is a list request
  // and a full re-rank under the reader. The keyboard's run is held back
  // like a typed filter; a click, which chooses once, is not.
  const handleSortChange = (next: string, source: SegmentSource = "pointer") => {
    setSortTyping(source === "keyboard");
    setSort(next);
  };
  const handleFiltersChange = (next: FilterState) => {
    // The reader picking a type claims it for the patient on screen. The
    // panel is fed `effectiveFilters`, so an unrelated edit hands back the
    // masked value unchanged and this does not fire.
    if (next.trialType !== effectiveFilters.trialType) {
      setTrialTypeOwner(patientIdentity);
    }
    setTyped({ epoch: savedFilters.epoch, yes: true });
    // Exactly what moved, for the read that may be in flight behind this.
    //
    // Against `effectiveFilters`, which is what the panel was HANDED, not the
    // raw state behind it: a stale `trialType` is masked on its way in, so
    // comparing with the raw value reads the mask itself as an edit — and
    // then suppresses the new patient's saved type while claiming the old
    // patient's for them.
    for (const field of new Set([...Object.keys(next), ...Object.keys(effectiveFilters)])) {
      const key = field as keyof FilterState;
      if (!sameValue(next[key], effectiveFilters[key])) overruled.current.fields.add(field);
    }
    setFilters(next);
    // Only what the reader changed. `next` carries the host's mount-time
    // scope too, and saving that would make one mount's scope their standing
    // preference — a later mount with a different scope would lose to it.
    const owned = userOwnedFilters(next, baseline, ownedFields.current);
    for (const field of Object.keys(owned)) ownedFields.current.add(field);
    savedFilters.persist(withWeights(owned, next, ownedFields.current));
  };
  // Reset goes back to the baseline, not to `{}`: clearing the seeded
  // country would silently widen the search to every country in the
  // registry, which is not what "reset" means to the person clicking it.
  // The owner is deliberately NOT cleared here. Once a stale choice falls
  // back to the baseline's type, clearing it changes nothing — the two
  // produce the same value — and a mutation test confirmed the line was
  // dead. It was load-bearing only under the earlier "mask to undefined"
  // rule, which is gone.
  const handleFiltersReset = () => {
    // A button click is the canonical NON-keystroke: with this left set,
    // the cleared panel sat over the narrowed rows for 400ms — an empty
    // box, a badge reading no filters, and the old result set underneath.
    setTyped({ epoch: savedFilters.epoch, yes: false });
    overruled.current = { fields: new Set(), all: true };
    // The weights come through. This button is "Reset filters", the badge
    // beside it has deliberately never counted them, and the score is not a
    // filter — so clearing it here would be a change the reader was not
    // offered and cannot see coming.
    const keptWeights = ownedWeights(filters, ownedFields.current);
    setFilters({ ...baseline, ...keptWeights });
    // Reset gives the PANEL fields back to the host, so nothing there is
    // owned any more. The weights stay owned: they are still the reader's.
    ownedFields.current = new Set(Object.keys(keptWeights));
    // `reset`, not `persist(baseline)`: the row is meant to end up EMPTY, and
    // writing the baseline would store the host's scope as the reader's
    // standing preference instead. It also retires a save already on the
    // wire, which would otherwise land afterwards and restore what was
    // just cleared.
    savedFilters.reset();
    // Written back AFTER the reset, never merged into it: `reset` empties the
    // row and retires whatever was queued, so a weight persisted before it
    // would be cleared by the very call meant to keep the panel's fields out.
    if (Object.keys(keptWeights).length) savedFilters.persist(keptWeights);
  };

  // The weights are not panel fields: the Filters badge does not count them
  // and Reset does not clear them, because they change the ORDER of the list
  // and the number on each card, never which trials are in it. So they are
  // saved here rather than through `userOwnedFilters`, and all four go at
  // once — the store merges, and a partial write would leave the reader with
  // a pair of weights they never chose.
  const handleWeightsChange = (weights: Record<WeightKey, number>) => {
    const saved = weightsToSave(weights);
    const next = { ...filters, ...saved };
    setFilters(next);
    for (const field of Object.keys(saved)) ownedFields.current.add(field);
    // The panel's own fields travel with them. The writer's debounce REPLACES
    // what is queued rather than merging it, so two savers writing disjoint
    // halves inside one 400ms window lose one half each: weights saved and
    // then a filter typed stored no weights at all, and a filter CLEARED and
    // then weights saved dropped the tombstone, so the cleared filter came
    // back on the next mount and narrowed the list again.
    savedFilters.persist(
      withWeights(
        userOwnedFilters(next, baseline, ownedFields.current),
        next,
        ownedFields.current,
      ),
    );
  };

  // What anything keyed on these filters should see: the weights at their
  // default say nothing, and saying it differently from `queryFilters` is
  // what makes two keys for one question.
  const detailFilters = useMemo(
    () => ({ ...effectiveFilters, ...atDefaultsDropped(effectiveFilters) }),
    [effectiveFilters],
  );

  // Opened on demand: the graph is a second full matcher run over the same
  // search, so it is not something to have ready just in case.
  const [graphOpen, setGraphOpen] = useState(false);
  // A state tab whose saved ids are unavailable — loading, failed, or over the
  // 500 cap — cannot be mapped. Disabling the QUERY is not enough: with no ids
  // the key is the default tab's key, so react-query would either hand back
  // the cached whole-corpus map under a tab that says "Favorites", or, with
  // nothing cached, sit on "Building the map…" for ever. The control is what
  // has to refuse.
  const graphUnavailable = stateTab != null && trialIds === undefined;
  const graph = useTrialsGraph({
    apiClient,
    patientInfo,
    personId,
    filters: queryFilters,
    // The same narrowing the list is under. Favorites and Registered narrow
    // ONLY by `trial_ids` — their `type` is undefined — so without this the
    // map drew the whole corpus under a tab that says "Favorites", and its
    // cache key was identical to the default tab's, which meant it was served
    // from that tab's cache without a request.
    trialIds,
    enabled: graphOpen && !graphUnavailable,
  });
  // Selecting a trial from the graph opens the same detail page the cards do.
  // The detail page needs only the id, which is always available; the host's
  // `onTrialSelect` wants the list ROW, which is not — the graph draws up to
  // fifty trials and the list holds one page of ten, so off page one, or
  // under a different sort, the row is simply not here. Opening anyway beats
  // a click that silently does nothing; calling the host with a fabricated
  // row would be worse than not calling it.
  const handleSelectFromGraph = (trialId: number) => {
    const row = (query.data?.results ?? []).find((t) => t.trialId === trialId);
    setGraphOpen(false);
    setSelectedTrial({ trialId, row });
    if (row) onTrialSelect?.(row);
  };

  // List or map, over the same page of trials. The map issues no request of
  // its own: every row already carries its closest site, so a second fetch
  // would be a second matcher run to learn what is in hand.
  const [mapOpen, setMapOpen] = useState(false);

  // Does the controls row have room for the view mode, every order and the
  // three actions side by side? Measured, because this is a remote: the
  // window is the host's, and the same 1280px window gives this list a 900px
  // column in ht-phr and the full width in CB. Without a ResizeObserver
  // (jsdom, an old browser) the answer stays "no", which is the layout that
  // fits either way.
  const observerRef = useRef<ResizeObserver | null>(null);
  const rowRef = useRef<HTMLDivElement | null>(null);
  // The measurement is the state; whether it is wide enough is derived from
  // it. Held the other way round — a boolean set by the observer — a change
  // in how much room is NEEDED (an order the host asked for is a fourth
  // segment) has no measurement to re-read and needs a second pass to
  // re-measure, which paints the wrong layout for a frame first.
  const [rowWidth, setRowWidth] = useState(0);
  const wideThreshold = wideControlsRow(sortOptionsFor(sort).length);
  const wideRow = rowWidth >= wideThreshold;
  const wideRef = useRef(wideRow);
  const thresholdRef = useRef(wideThreshold);
  /** Set when the sort is about to move out from under the keyboard. */
  const sortHadFocus = useRef(false);
  useLayoutEffect(() => {
    wideRef.current = wideRow;
    thresholdRef.current = wideThreshold;
  });

  const applyWidth = useCallback((width: number) => {
    // The sort is about to be rendered in the other slot, which is a
    // different DOM node: React unmounts this one, and focus on it goes to
    // <body>. A reader whose host collapsed a sidebar would find their next
    // Tab starting from the top of the page.
    if (width >= thresholdRef.current !== wideRef.current) {
      const sortGroup = rowRef.current?.querySelector(".exact-seg--grow");
      sortHadFocus.current = !!sortGroup && sortGroup.contains(document.activeElement);
    }
    // A drag on a window edge delivers a stream of widths; React drops the
    // ones that do not change this.
    setRowWidth(width);
  }, []);

  // A ref callback, not an effect on mount: opening a trial returns the detail
  // page from this same component, so the row unmounts while the component
  // does not. An effect with `[]` would keep watching the detached node — it
  // reports 0x0, the row goes narrow, and coming back mounts a row nothing
  // observes, leaving the wide layout dead for the rest of the session however
  // wide the host's column is. React calls this with null on the way out.
  const controlsRef = useCallback(
    (row: HTMLDivElement | null) => {
      observerRef.current?.disconnect();
      observerRef.current = null;
      rowRef.current = row;
      if (!row || typeof ResizeObserver === "undefined") return;
      // Observing the row is safe from feedback: its width comes from the list
      // around it, and moving the sort between its rows does not change it.
      const observer = new ResizeObserver(([entry]) => applyWidth(entry.contentRect.width));
      observer.observe(row);
      observerRef.current = observer;
    },
    [applyWidth],
  );

  // The chosen segment is the group's one tab stop, so it is the one that
  // takes the focus back after the move.
  useLayoutEffect(() => {
    if (!sortHadFocus.current) return;
    sortHadFocus.current = false;
    // The move was decided when the width arrived, and this runs on a later
    // commit: the observer is not a React event, so a reader can Tab or
    // click in between — a host animating a sidebar delivers widths for a
    // quarter of a second. Only focus that the unmount dropped is taken
    // back, and an unmount leaves it on <body>.
    const active = document.activeElement;
    if (active && active !== document.body) return;
    rowRef.current
      ?.querySelector<HTMLElement>('.exact-seg--grow [role="radio"][aria-checked="true"]')
      ?.focus();
  }, [wideRow]);
  const handleSelectFromMap = (trial: TrialMatch) => {
    setMapOpen(false);
    handleSelect(trial);
  };

  const handleSelect = (trial: TrialMatch) => {
    setSelectedTrial({ trialId: trial.trialId, row: trial });
    onTrialSelect?.(trial);
  };

  // When the detail view opens, push a synthetic history entry so the
  // browser ← back button returns to the trial list instead of navigating
  // to the previous host page. The popstate listener tears itself down
  // when the detail closes (effect cleanup) or when the patient context
  // resets (selectedTrial becomes null via the reset effect above).
  useEffect(() => {
    if (!selectedTrial) return;
    window.history.pushState({ exactTrialDetail: selectedTrial.trialId }, "");
    const handler = () => setSelectedTrial(null);
    window.addEventListener("popstate", handler);
    return () => window.removeEventListener("popstate", handler);
  }, [selectedTrial]);

  // Selecting a trial swaps the whole view for the in-remote detail page
  // (CB navigates to its own `/t/:id`; the remote owns the detail itself).
  // `onBack` calls history.back() so the synthetic entry is consumed and
  // the popstate listener above fires setSelectedTrial(null).
  if (selectedTrial) {
    const selectedId = String(selectedTrial.trialId);
    return (
      <TrialDetailPage
        apiClient={apiClient}
        trialId={selectedTrial.trialId}
        patientInfo={patientInfo}
        personId={personId}
        // What actually withholds the controls is the descriptor itself:
        // `fields` undefined answers "unknown" for every row, which is the
        // page exactly as it reads today. That covers loading, a failed read
        // and a host that never had a writer, so no extra guard is needed for
        // any of them — and a guard claiming to provide one would be
        // decoration.
        //
        // The gate below is about `save`, not about the rows: it is there so
        // a callback that would dereference a missing `setPatientFields` is
        // never handed out at all. It matters now in a way it did not before
        // the queue — the callback is held across renders and fired from a
        // timer, long after the render that produced it.
        editing={
          canEditFields(state)
            ? {
                fields: writableFields.data,
                save: patientFields.save,
                outstanding: patientFields.outstanding,
                failed: patientFields.failed,
              }
            : undefined
        }
        // Values and callbacks, not the adapter: this component owns the id
        // lists and the mutations, so it is the only place that can keep the
        // star on the card and the star on the detail page saying the same
        // thing. `undefined` where the answer is not known — the control is
        // then not drawn at all, rather than drawn wrong for a moment.
        trialState={
          state
            ? {
                isFavorite: favorites.data
                  ? favorites.data.includes(selectedId)
                  : undefined,
                favoriteBusy: writes.favorites.pending.includes(selectedId),
                onToggleFavorite: (on) =>
                  write("favorites", setFavorite, selectedId, on),
                favoriteFailed: writes.favorites.failed.includes(selectedId),
                // `data === undefined` too: a query that has errored KEEPS
                // the data it had, so after a failed background refetch this
                // was true while the star was still being drawn from the
                // retained ids — an "unavailable" notice printed underneath
                // a control that is present and works.
                favoritesUnavailable: favorites.isError && favorites.data === undefined,
                isRegistered:
                  registered.data && advanced.data
                    ? registered.data.includes(selectedId)
                    : undefined,
                onToggleRegistered: (on) =>
                  write("registered", setRegistered, selectedId, on),
                // Pending IS read off the mutation, and is scoped the same
                // way: it describes a write in flight, which there can only
                // be one of, and it must not disable a different trial's
                // button.
                registerPending: writes.registered.pending.includes(selectedId),
                registerFailed: writes.registered.failed.includes(selectedId),
                registeredUnavailable:
                  (registered.isError && registered.data === undefined) ||
                  (advanced.isError && advanced.data === undefined) ||
                  !canReadAdvanced(state),
                // Absent until the read answers: drawn against an unknown
                // answer, the control is exactly the one that overwrites an
                // advanced status.
                advancedStatus: advanced.data?.[selectedId],
              }
            : undefined
        }
        // The derived set, not raw state: the detail is scored under the
        // preferences the list used, and `country` no longer lives in
        // `filters`. Passing raw state sent the detail request without the
        // patient's country, so its scores and distance could disagree with
        // the card the reader clicked.
        // Normalised like `queryFilters`: four explicit 25s and four absences
        // ask this endpoint for exactly the same thing, and the detail query
        // is keyed on what it is handed — so a Save that changed nothing
        // would re-fetch the open trial and blank it back to loading.
        filters={detailFilters}
        onBack={() => window.history.back()}
      />
    );
  }

  return (
    <div className="exact-root exact-list" ref={exactRootRef} style={{ padding: "1rem" }}>
      {/* CB's header block: the title and the preferences button on one
          line, the tab strip on its own below. CB puts the button on the
          TAB row from lg up and beside the title below it, rendering it
          twice and hiding one — two tab stops' worth of markup, and a
          dialog that vanishes mid-edit when the breakpoint is crossed.
          One copy, in the position CB uses at the width where the strip
          needs its whole line, and the strip keeps its rule across the row
          at every width. */}
      <div className="exact-list__head">
        <h1 className="exact-list__title">Your Trials</h1>
        <SuitabilityPreferences
          filters={filters}
          patientKey={patientHandle}
          onChange={handleWeightsChange}
        />
      </div>

      <Tabs
        tabs={tabs}
        active={activeTab}
        onChange={handleTabChange}
        // No `stateTab` guard here any more: a narrowed response — the one
        // that would read as the corpus, "Fully matched, 1" for a reader
        // with one bookmarked eligible trial and two hundred matching ones
        // — is refused above, by the response rather than by the tab. The
        // guard here answered about the request about to go out, which is
        // the wrong moment for data already in hand.
        //
        // Refused, and then REPLACED where we have something true: the last
        // counts taken over this same corpus. See `rememberedCounts`.
        counts={countsForBar}
        activeTabTotal={totalCount}
        stateCounts={stateCounts}
      />

      <div className="exact-list__controls" ref={controlsRef}>
        {/* CB's row 1 is the view mode on the left and the actions on the
            right; the sort joins them there when the row is wide enough and
            takes a row of its own when it is not.
            
            Which row it is on decides where it is WRITTEN, not just where it
            is painted. CSS `order` moves the box and leaves the tab stop
            behind, so on the narrow layout the keyboard went from row 1 down
            to the sort and back up to the actions (WCAG 2.4.3). CB solves the
            same problem by rendering the control twice and hiding one; one
            control in the right place is the same layout without a second
            copy of it in the accessibility tree. */}
        <ViewModeControl
          value={mapOpen ? "map" : "list"}
          onChange={(mode) => setMapOpen(mode === "map")}
        />

        {wideRow ? <SortControl value={sort} onChange={handleSortChange} /> : null}

        <div className="exact-list__triggers">
        {/* CB's toolbar tooltips. A control that cannot act says why in its
            tooltip instead, in place of the `title` it used to carry, so
            there is one box, not a styled one and a native one. */}
        <ActionTooltip
          text={
            graphUnavailable
              ? "Your saved trials aren't available right now, so this tab can't be mapped."
              : ACTION_TOOLTIPS.exploreTrials
          }
        >
          {(tipId) => (
            <button
              type="button"
              className={`exact-filters__trigger${graphOpen ? " is-on" : ""}`}
              aria-expanded={graphOpen}
              aria-describedby={tipId}
              disabled={graphUnavailable}
              onClick={() => setGraphOpen((open) => !open)}
            >
              Explore Trials
            </button>
          )}
        </ActionTooltip>

        <ActionTooltip
          text={exportUnavailable ? exportUnavailableReason : ACTION_TOOLTIPS.exportCsv}
        >
          {(tipId) => (
            <button
              type="button"
              className={`exact-list__export${exportState === "working" ? " is-working" : ""}`}
              aria-describedby={tipId}
              onClick={() => void handleExport()}
              // Said out loud rather than left as a dead control: everything else
              // in this file names why it cannot answer.
              disabled={exportState === "working" || exportUnavailable}
            >
              {exportState === "working" ? "Preparing…" : "Export CSV"}
            </button>
          )}
        </ActionTooltip>

        <ActionTooltip text={ACTION_TOOLTIPS.filters}>
          {(tipId) => (
            <button
              type="button"
              className={`exact-filters__trigger${
                filtersOpen || activeFilterCount > 0 ? " is-on" : ""
              }`}
              aria-expanded={filtersOpen}
              aria-describedby={tipId}
              onClick={() => setFiltersOpen((open) => !open)}
            >
              {activeFilterCount > 0
                ? `Filters (${activeFilterCount})`
                : "Filter Results"}
            </button>
          )}
        </ActionTooltip>
        </div>

        {wideRow ? null : (
          <div className="exact-list__sort">
            <SortControl value={sort} onChange={handleSortChange} />
          </div>
        )}
      </div>

      {graphOpen && !graphUnavailable ? (
        graph.isPending ? (
          <p className="exact-graph__status">Building the map…</p>
        ) : graph.isError ? (
          <p className="exact-graph__status" role="alert">
            Couldn't build the map for this search.
          </p>
        ) : (
          <TrialsGraph
            trials={graph.data?.trials ?? []}
            onSelectTrial={handleSelectFromGraph}
            onClose={() => setGraphOpen(false)}
          />
        )
      ) : null}

      {exportState === "failed" || exportState === "incomplete" ? (
        <p className="exact-list__export-error" role="alert">
          {exportState === "incomplete"
            ? "That export stopped partway, so it wasn't saved. Please try again."
            : "Couldn't prepare that file. Please try again."}
        </p>
      ) : null}

      {savedFilters.failed ? (
        <p className="exact-list__save-warning" role="status">
          Your filters are shown here but couldn't be saved for next time.
        </p>
      ) : null}

      {filtersOpen ? (
        <FilterPanel
          apiClient={apiClient}
          filters={effectiveFilters}
          onChange={handleFiltersChange}
          onReset={handleFiltersReset}
          canReset={activeFilterCount > 0}
          diseaseCode={diseaseCode}
        />
      ) : null}

      {/* `savedFilters.pending` belongs here as much as in `enabled`: a
          DISABLED query reports `isLoading` false, so while the saved set
          is being read nothing said the list was loading — and the empty
          state below fired instead, telling the reader there are no trials
          before anything had been asked. */}
      {/* Only when there IS a patient to load for. With none the search
          never runs, and the line sat next to "Pass a patientInfo payload",
          each contradicting the other — through `waitingForIds` as well,
          which is the worse one: the ids query is enabled on the adapter
          alone, so a hanging favorites read pairs them permanently rather
          than for a frame. */}
      {hasPatient &&
      (query.isLoading ||
        savedFilters.pending ||
        waitingForIds ||
        showingOtherTabsRows) ? (
        <p style={{ color: "var(--exact-color-text-muted)" }}>Loading trials…</p>
      ) : null}

      {tooManySavedIds ? (
        <p style={{ color: "var(--exact-color-not-eligible)" }}>
          You have saved {savedIds?.length} trials, and this view can show at
          most {MAX_TRIAL_IDS} at a time. Remove a few, or use the other tabs
          to find them.
        </p>
      ) : null}

      {idsFailed ? (
        <p style={{ color: "var(--exact-color-not-eligible)" }}>
          Couldn't load your saved trials:{" "}
          {(stateIdsQuery.error as Error)?.message ?? "unknown error"}
        </p>
      ) : null}

      {/* The bookmark control is painted from the favorites list, so when
          that read fails every star vanishes — on EVERY tab, not just the
          Favorites one, and with nothing on screen to say why. The reader
          would conclude the feature had been removed. */}
      {favorites.isError && favorites.data === undefined && !idsFailed ? (
        <p style={{ color: "var(--exact-color-not-eligible)" }}>
          Couldn't load your favorites, so bookmarking is unavailable right
          now.
        </p>
      ) : null}

      {/* A write that failed has to say so. The star is painted from the
          server's list, so a rejected PATCH leaves it exactly where it was —
          indistinguishable from a click that never registered.

          From the recorded failures, not from the mutation: a registration
          that rejects after the reader has gone back to the list had no
          surface here at all, so the only thing they ever saw was
          "Saving…". */}
      {writes.favorites.failed.length ? (
        <p style={{ color: "var(--exact-color-not-eligible)" }} role="alert">
          Couldn't update your favorites. Please try again.
        </p>
      ) : null}

      {writes.registered.failed.length ? (
        <p style={{ color: "var(--exact-color-not-eligible)" }} role="alert">
          Couldn't save your interest in {writes.registered.failed.length === 1
            ? "a trial"
            : `${writes.registered.failed.length} trials`}
          . Open the trial to try again.
        </p>
      ) : null}

      {query.isError ? (
        <p style={{ color: "var(--exact-color-not-eligible)" }}>
          Failed to load trials: {(query.error as Error)?.message ?? "unknown error"}
        </p>
      ) : null}

      {/* `isPlaceholderData`, not `isFetching`: the rows on screen belong to
          the previous query only while placeholder data is showing. Keyed on
          `isFetching` this dimmed the whole list on every background refetch
          — including the window-focus one React Query runs by default after
          30s away — for a request the reader never asked for.

          The live region is always mounted and swaps its text: several
          screen readers only announce changes to a region that already
          existed, so a conditionally-rendered `role="status"` is silent. */}
      <div className="exact-list__updating-slot" role="status" aria-live="polite">
        {query.isPlaceholderData ? (
          <span className="exact-list__updating">Updating…</span>
        ) : null}
      </div>

      {mapOpen ? (
        <TrialsMap
          // The rows on screen, not a fresh request: every one carries its
          // closest site already.
          trials={trials}
          renderMap={renderMap}
          onSelectTrial={handleSelectFromMap}
          onClose={() => setMapOpen(false)}
        />
      ) : null}

      {/* The list stays. The map answers "where", the cards answer
          "what" — CancerBot shows both at once for that reason, and the
          sticky places panel only means something beside a list that
          scrolls. */}
      <div
        className={`exact-list__rows${query.isPlaceholderData ? " is-stale" : ""}`}
        aria-busy={query.isPlaceholderData || undefined}
      >
        {trials.map((t) => (
          <TrialCard
            key={t.trialId}
            trial={t}
            onSelect={handleSelect}
            isFavorite={
              favorites.data ? favorites.data.includes(String(t.trialId)) : undefined
            }
            busy={writes.favorites.pending.includes(String(t.trialId))}
            onToggleFavorite={
              state
                ? (on) => write("favorites", setFavorite, String(t.trialId), on)
                : undefined
            }
          />
        ))}
      </div>

      {!query.isLoading && !hasPatient ? (
        <p style={{ color: "var(--exact-color-text-muted)" }}>
          Pass a <code>patientInfo</code> payload or <code>personId</code> to load
          trial matches.
        </p>
      ) : null}

      {!query.isLoading && !staleRows && hasPatient && trials.length === 0 ? (
        <p style={{ color: "var(--exact-color-text-muted)" }}>No trials found</p>
      ) : null}

      <Pagination
        page={page}
        pageCount={pageCount}
        onChange={setPage}
      />
    </div>
  );
}

export function TrialMatches(props: TrialMatchesProps) {
  // If the host provides a QueryClient we use it; otherwise spin up our
  // own. Keeping the local one stable across renders avoids React Query's
  // re-mount thrash when the parent re-renders for unrelated reasons.
  const [ownClient] = useState(() => new QueryClient());
  const client = props.queryClient ?? ownClient;

  return (
    <QueryClientProvider client={client}>
      <TrialMatchesInner {...props} />
    </QueryClientProvider>
  );
}

export default TrialMatches;
