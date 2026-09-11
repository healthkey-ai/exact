# Find Trials: CancerBot Parity Plan

**EXACT federated remote — gap analysis and phased plan**

What diverges between CancerBot `ui.v2` and the federated EXACT remote — in layout, UX, and API — and the order in which to close it, keeping the remote self-contained for both hosts (HT PHR and CB).

*9 September 2026 · Sources: CB `ui.v2/client`, EXACT `frontend/src/federation`, EXACT/CB `trials/api`, `ht-phr/frontend`*

---

## 1. Decisions from the interview

| Question | Decision | Consequence |
|---|---|---|
| **Target host** | Both; the remote is self-contained | HT PHR (`/curehub/trials`) and the CB-vendored widget. Page chrome (tabs, filters, pagination) lives inside the remote; the host supplies only auth and a container. |
| **State owner** | PROMOP | Confirmed by codex. EXACT stays stateless; favorites and saved filters are person-scoped in PROMOP. |
| **Scope for v1** | Parity + Favorites | Core (tabs / sort / pagination / filters) plus favorites plus the extra widgets. Registered is deferred pending Adam. |
| **Visual layer** | Scoped `exact.css` | Extend the hand-written CSS under `.exact-root` with CB tokens. No Tailwind inside the remote — the host-agnostic contract holds. |

### Two corrections to the starting assumptions

**ht-phr does have its own Django backend** (`backend/apps/{accounts,health,patient_profile}`, port 8000 in the stack), so "the host stores it" is technically possible — but it still loses, because the storage would have to be duplicated in CB as well.

**PROMOP already has** `PatientTrialEnrollment(person, trial_id, nct_id, status, status_date, notes)` with full CRUD at `/api/v1/trial-enrollments/?person_id=` and a docstring stating that trial metadata is fetched from EXACT by `trial_id`. Half of the Registered feature is already modelled — favorites bolts onto it with a single field.

---

## 2. Baseline — what the remote does today

About 2.8k lines in `frontend/src/federation/` (2,569 excluding tests; `tooltips.ts` is 653 of them and `exact.css` 472): `TrialMatches` (list, Eligible/Potential groups, "Load more"), `TrialCard` and `TrialDetailPage` — both already copied from CB's layout, `FilterBar` (5 controls), `bits.tsx` (ScorePill/Field with CB's 80/60 thresholds), `exact.css` (472 lines, CB tokens), static field tooltips, plus `widget.tsx` on branch `2omop-federated-UI` — an isolated React 19 mount for a React 18 host.

Data: `POST /trials/match/` with an inline payload, or `GET /trials/?person_id=`; details via `POST /trials/{id}/match/`. Filters live entirely in React state; nothing is persisted.

---

## 3. Gap matrix A — search page

| CancerBot | Remote today | Status | Action |
|---|---|---|---|
| "Your Trials" heading + Eligible / All / Registered / Favorites tabs with server-side counts | none; Eligible/Potential sections instead | **missing** | CB tabs with a flat list inside each; counts from a new `/trials/counts/` |
| Eligible/Potential grouping | computed over the *loaded page*, not the whole result set | **bug** | Drop client-side grouping; move to server counts |
| Sort: Suitability / Matching / Distance | no UI; `filters.sort` exists in the types but **does nothing** — `/trials/match/` binds to `list`, whose ordering is fixed at `order_by('-match_score', '-posted_date', 'id')` | **bug** | `TrialsSortControl` + a POST alias for `search` |
| Filter panel: 12 fields, Trial-purpose multiselect, Reset, "Filters (N)" badge, debounce + write queue | 5 inline controls (title, recruitment, trial type, distance, validatedOnly), no reset, no badge | **partial** | Port the panel onto `.exact-filters`; missing: purpose, treatment, sponsor, phase, register, lastUpdate, and a country control |
| Numbered pagination, 10 per page, `?page=` in the URL, scroll-to-top | "Load more", 20 per page | **missing** | CB pagination; URL state via an optional host routing adapter |
> **On the country filter.** CB has a country dropdown; the remote deliberately does not — `TrialMatches` derives `filters.country` from `patientInfo.country` so the list scopes to the patient's geography without a click (see the comment in `FilterBar.tsx`). Adding the control is therefore a behaviour change toward cross-border search, not gap-filling, and wants a deliberate product call.

| List / Map toggle + `TrialsMap` (Google Maps, pins, sticky, expand) | none | **missing** | Phase 3. Data is already there: `closestLocationGeoPoint` in the serializer |
| Export CSV (`/trials/export/`) | none | **missing** | Port the action into EXACT, add the button |
| Explore Trials → Knowledge Graph | none | **missing** | Backend **already exists**: `/trials-graph/graph/`. UI only |
| Suitability Preferences (benefit / burden / risk / distance weights) | none | **missing** | Backend **already accepts** `benefitWeight` et al. Needs UI + storage for the weights |
| TrialPurposeNotice (explains the default narrowing to treatment) | none, and the purpose filter itself is missing | **missing** | Ships with the purpose multiselect |
| ProfileCompletionCard ("complete your profile, %") | none | **missing** | Compute the percentage host-side / in PROMOP; the remote exposes a slot |
| Updating indicator, empty/error states | present, simpler | **present** | Cosmetic |
| Tooltips on every control (admin-editable, DB-backed) | static, detail page only | **partial** | Keep them static; editable tooltips are a CB-only feature |

---

## 4. Gap matrix B — detail page

| CancerBot | Remote today | Status | Action |
|---|---|---|---|
| Header, score pills, meta rows, Summary, Required / Your Value table | present, layout taken from CB | **present** | — |
| Bookmark (favorite) | none | **missing** | Phase 2, via the state adapter |
| Inline editing of "Your Value": pencil controls, subform dialogs, autosave with a queue, saving indicator, error toast | read-only | **missing** | Phase 4. Write **only** through the OMOP path (below) |
| `subform_details` (composite fields) | ignored, although EXACT sends them | **missing** | At minimum a view-only dialog |
| `highRiskMclCriteriaBreakdown` panel | EXACT returns it from `retrieve`; the remote ignores it | **missing** | Cheap panel port |
| `matchingType: not_evaluated` (sane_range — criterion was never checked) | the status does not exist in EXACT at all | **missing** | Port CB #4850 into EXACT + render the third state |
| Share / Invite, Register Interest, "Missing Attributes" chat | none | **missing** | Registered is on hold; chat is CB-only |
| Compare to Standard of Care (modal + link from the Suitability tooltip) | none | **missing** | HT has a separate `soc` remote — link to it, don't duplicate |
| Addressable trial link (`/t/:id`), router back button | detail view is local state + a synthetic `pushState`; the link cannot be shared | **partial** | Optional routing adapter: the host supplies `getTrialParam` / `setTrialParam` |

---

## 5. Gap matrix C — API

| Gap | Where | Status | Detail |
|---|---|---|---|
| `?phase=` is ignored | EXACT `services/study_preferences.py` | **bug** | `StudyPreferences` has a `phase` field and the queryset filters on it (`by_phase`), but `study_preferences_from_query_params()` never populates it. One line. |
| `?type=favorites` crashes | EXACT `trials_views.py:158` | **bug** | `queryset.filter(favorite=True)` — EXACT's `Trial` model has no `favorite` field (a leftover from CB, where it is annotated). FieldError → 500. |
| No POST alias for `search` | EXACT | **missing** | `match` (→ list) and `match_detail` (→ retrieve) exist. Neither sorting nor tab counts is reachable from the inline-payload path — nor from the `?person_id=` GET path, which also targets `list`. Needs `POST /trials/search/match/` bound to `search`. |
| No filter by id list | EXACT | **missing** | `trial_ids` in the body of the `match` actions, with a hard cap (≤500) — this is what implements the Favorites tab. |
| No `/trials/counts/` | EXACT | **missing** | CB returns tab counts in one request; otherwise the remote issues four `limit=1` requests. Near-collision worth naming: `/trials/count/` (singular) already exists and returns one total for the current filters. CB carries the same pair, so the plan keeps both rather than overloading one. |
| No `/trials/export/` | EXACT | **missing** | Port CB's action (streaming CSV), without persisted preferences. |
| `recruitmentStatus` absent from form-settings | EXACT | **partial** | The `statuses` key is the invitation enum, not the recruitment state. The remote already hard-codes the list; better to add a real key to `all_options()`. |
| `distanceUnits` drift | EXACT ↔ CB | **partial** | CB sends `kilometers`; EXACT only compares against `miles` (anything else means km) — so filtering works, but the serializer echoes "743 kilometers" back into the UI. The remote should send `km` / `miles`. |
| Favorites storage | PROMOP | **missing** | Add `is_favorite` to `PatientTrialEnrollment` (not a new status — statuses describe participation, and a bookmark is orthogonal) + an `?is_favorite=true` filter. |
| Filter storage | PROMOP | **missing** | A small new model `TrialSearchPreferences(person, preferences json)` + GET/PATCH/reset and a server-computed `nonDefaultFilterCount` — otherwise the "Filters (N)" badge has to be computed client-side and will drift from CB. |

---

## 6. Architecture — three seams

**1. A state adapter in the remote.** `TrialMatches` takes an optional `state` prop: `{ listFavorites, toggleFavorite, getPreferences, savePreferences, resetPreferences }`. The default implementation is a PROMOP REST client (built from a `promopClient` prop plus `personId`). The CB host injects its own implementation hitting a CB endpoint on top of the in-process promop plugin. With no adapter the remote degrades to localStorage, so the demo and the dev harness keep working.

**2. One schema, in PROMOP.** A single source of truth for both HT and CB; cross-device support comes free. EXACT gains no patient-scoped table.

**3. The intersection with the matcher happens inside EXACT.** This is the crux: the Favorites tab must be filtered, sorted, and paginated *within* EXACT's queryset, or the counts and ordering lie. So the bookmarked ids travel into EXACT as an explicit parameter.

```
  PROMOP                        Remote                         EXACT
  ──────                        ──────                         ─────
  GET /api/v1/                  POST /trials/search/match/     narrows the queryset
  trial-enrollments/       →    { patient_info,           →    BEFORE scoring →
  ?person_id=&                    trial_ids: [...] }           normal paginated
  is_favorite=true                                             response, correct
  → favorite ids + count                                       itemsTotalCount
```

> **Why the body, not the query string.** The id list can get long, and the inline patient payload already travels in the body. A cap on the list length is mandatory and validated on entry: without it this is a ready-made vector for a request that expands into thousands of WHERE clauses.

> **Do not attach `trial_ids` to the detail request.** `match_detail` shares `get_queryset`, so an id list that excludes the trial being opened turns its detail page into a 404. That is the right behaviour for a filter, and a live footgun for a host that keeps the favorites ids in a store and lets an interceptor add them to every match POST: every non-bookmarked trial would stop opening. The list request carries them; the detail request does not.

> **Inline editing: write OMOP facts, not PatientRecord.** In PROMOP, `PatientRecord` is a read-only projection re-derived by a signal from the OMOP tables. There are two sanctioned write paths: granular CRUD on the OMOP tables (`/api/v1/measurements/`, `/conditions/`, …) and `PATCH /api/v1/patient-records/{person_id}/`, which does *not* write to the projection but translates each field into the matching OMOP table write and triggers the re-derivation. Inline editing should use the second — but every form field needs to be checked for an existing OMOP mapping; fields without one stay read-only rather than "saving" into nothing.

---

## 7. Phases

### Phase 0 — fix what is already broken · P0 · ~0.5 day

- **exact** — **Map `phase`** in `study_preferences_from_query_params()` + a test that `?phase=` actually narrows the result set.
- **exact** — **Remove `filter(favorite=True)`** from `search`: today `?type=favorites` is a 500. It is replaced by `trial_ids` in phase 2; until then, return an explicit 400 with a clear message.
- **exact** — **`POST /trials/search/match/`** — a `search` alias binding `self.action = 'search'`, modelled on the existing `match` / `match_detail`. Without it, sorting and tabs are unreachable from the inline path.
- **exact** — `/trials/counts/` — eligible / potential / (later) favorites counts in one request.
- **exact** — `recruitmentStatus` in `ValueOptions.all_options()`, so the remote stops hard-coding the list.

### Phase 1 — core parity: tabs, sort, pagination, filter panel · P1 · ~4–5 days

- **exact/frontend** — **Page chrome**: heading + `.exact-tabs` with counts, `.exact-sort`, `.exact-pagination`. Client-side Eligible/Potential grouping is dropped — the tabs take over that job.
- **exact/frontend** — **Filter panel** of 12 fields in CB's layout (1/2/3/4-column grid), trigger button with a "Filters (N)" badge, Reset. The Trial-purpose multiselect uses native `<details>` + checkboxes, no Radix (the remote has none and needs none).
- **exact/frontend** — Move requests onto the `search` path: `useInfiniteQuery` → `useQuery` + `keepPreviousData`, as in CB.
- **exact/frontend** — Extend `exact.css`: tabs, filter grid, pagination, sort, trigger buttons. This is the bulkiest piece — CB leans on Tailwind + shadcn here; we write it by hand.
- **exact/frontend** — **Routing adapter** (optional prop): the trial page and page number land in the host's URL. Without an adapter, the current `pushState` fallback stands.

### Phase 2 — favorites and saved filters · P1 · ~3–4 days

- **promop** — **`is_favorite`** on `PatientTrialEnrollment` (+ migration, `?is_favorite=` filter, a light "ids + count only" endpoint). Participation statuses are left alone — Registered waits for Adam.
- **promop** — **`TrialSearchPreferences`**: person + JSON, GET/PATCH/reset, server-computed `nonDefaultFilterCount`.
- **exact** — **`trial_ids`** in the body of the `match` actions, validated and capped; the Favorites tab is an ordinary search narrowed by that list.
- **exact/frontend** — **State adapter** + the default PROMOP implementation; bookmark on the card and in the detail view; Favorites tab; filters read/written through the adapter with debounce and a write queue (in CB that queue solves genuine races — copy the logic, not just the look).
- **ht-phr / CB** — Pass `promopClient` into the remote (HT) and a CB adapter over the in-process plugin.

### Phase 3 — extra widgets · P2 · ~4–5 days

- **exact** — `/trials/export/` — port CB's streaming-CSV action.
- **exact/frontend** — **Map**: List/Map toggle, pins from `closestLocationGeoPoint`, sticky panel and expand. The Google Maps key arrives from the host as a prop — the remote carries no key of its own.
- **exact/frontend** — **Knowledge graph** on top of the ready `/trials-graph/graph/`.
- **exact/frontend** — **Suitability weights**: UI for the four weights the backend already accepts; stored in the same `TrialSearchPreferences`.
- **exact + fe** — `highRiskMclCriteriaBreakdown` panel and the `not_evaluated` status port (CB #4850) — today an unchecked criterion renders as "matched".

### Phase 4 — inline editing of "Your Value" · P2 · ~5–7 days, decided separately

- **audit** — **The mapping table first**: for every editable eligibility field, does a write path into OMOP exist via `PATCH /patient-records/{id}/`? Fields without a mapping stay read-only.
- **exact/frontend** — Port `FieldEditControl` / `SubformDialog` / the autosave queue (per-field batching, one PATCH in flight, flush on unmount, error toast) — CB earned this behaviour the hard way; copy the behaviour, not just the markup.
- **exact/frontend** — After a successful write, invalidate the match so scores and row statuses recompute.

### Deferred — Registered / Register Interest · on hold, pending Adam

The PROMOP model already exists (`status ∈ {interested, registered, entered, completed, withdrawn}`), so the open question is a product one, not a technical one: who sees what after "I'm interested", and does a notification reach the coordinator.

---

## 8. Risks

- **Two hosts, two ways of authenticating to PROMOP.** In HT it is an OAuth mint; in CB, the in-process plugin. This is precisely why the state adapter has to be a seam rather than a hard-wired client.
- **CSS volume.** The filter panel, popovers, and tabs by hand are the main schedule risk in phase 1. If it slips, the first thing to cut is the purpose multiselect (temporarily single-select).
- **Registered vs Favorites in one PROMOP table.** If Adam decides a bookmark is also a participation status, the migration has to be redone. Hence `is_favorite` as a separate field rather than a new enum value.
- **Pagination + the favorites intersection.** The `trial_ids` cap means "very many bookmarks" needs an explicit degradation message, not a silent truncation.
- **Sort ordering.** CB and EXACT have already diverged on the secondary sort when goodness scores tie; turning on the sort control will surface it. Verify against a shared corpus.

---

*Prepared against the code as of 2026-09-09: EXACT `dev` and `2omop`, plus branch `2omop-federated-UI`, CB `ui.v2`, PROMOP `API_SURFACE.md`. The state-owner recommendation was cross-checked with codex.*

*Delivery: every phase lands on the `cb-like-trials` integration branch, which forks from `dev`. Note that the two lines place the trial queryset differently — `trials/querysets/trial.py` on `dev`, the packaged `matcher_app/exact_matching/` on `2omop` — so the backend edits in phases 0 and 2 are written against `dev`'s layout and will need porting if this work is later merged into the `2omop` line.*
