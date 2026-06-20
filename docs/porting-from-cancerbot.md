# Porting search/matcher changes from CancerBot (CB) → EXACT

**Audience:** AI agents (and humans) porting changes from CancerBot into EXACT.
**Status:** rules of engagement. Follow them; they exist to keep ports cheap and correct.

## Why this exists
- **CB is the primary / upstream** for search & matcher logic
  (`/Users/lm/work/biblum/cancerbot`). EXACT is **downstream** and receives ported changes
  periodically.
- The therapy matcher/queryset/config code is **~95% identical** between the two repos. The cheap
  thing is to keep it that way. The expensive thing — and the #1 source of port pain — is
  **divergence**, especially *structural* divergence.
- EXACT additionally needs an **OMOP** variant of therapy matching (see
  `.gstack/omop_therapy_migration_plan.md`). The whole point of the rules below is to keep that OMOP
  delta from leaking into the code that gets ported.

## Golden rules (non-negotiable)
1. **Port verbatim. Do not "improve" during a port.** Apply the CB diff to EXACT as-is. Refactoring,
   renaming, re-styling, or restructuring ported code mid-port is forbidden — it creates drift that
   makes every *future* port conflict.
2. **Legacy-mode EXACT must stay byte-identical to CB.** The OMOP feature flag defaults **OFF**.
   With the flag off, EXACT's therapy matching must produce exactly what CB produces. A port must
   never silently change legacy behavior.
3. **All OMOP-specific divergence lives behind the seam** (`TherapyMatchProfile`, below) and the
   feature flag — never hardcoded into matcher/queryset/config bodies.
4. **Enabling refactors are upstreamed to CB first.** If a change is needed to make the seam work,
   make it a *behavior-preserving* refactor in CB, merge it there, then port it down. Never create
   an EXACT-only abstraction that CB doesn't have — CB owns the stable extension point.
5. **EXACT is stateless and read-only.** Never add patient persistence; never replicate OMOP vocab
   tables or hierarchy expansion in EXACT (patient OMOP concepts are supplied by the API consumer;
   trial OMOP columns are owned and filled by CB).

## The seam: `TherapyMatchProfile`
All therapy differences (legacy vs OMOP) are expressed by selecting a profile, not by editing logic.
The profile is a small dataclass/module with **two instances** (legacy, omop) — not a plugin
framework. It must cover **every profile-dependent operation**, because OMOP leaks through "small"
hardcoded spots otherwise:

- **Field-name groups** — required/excluded field names for therapies, therapy_components,
  therapy_types, planned, supportive (legacy `therapies_required` … vs `omop_therapies_required` …).
- **No-prior-therapy predicate fields** — the empty-list filter in
  `eligible_for_therapy_related_things_from_lines` (`trials/querysets/trial.py:~1036`) currently
  hardcodes the three legacy `*_required` fields; it must read them from the profile.
- **Patient concept-set builder** — returns the three explicitly named sets (therapies / components /
  types) **plus an answered/unknown/no-therapy state**, NOT bare lists (see semantics rule below).
  - legacy impl = today's internal-M2M expansion (therapy → components → categories).
  - omop impl = read the consumer-provided OMOP concepts flat (no expansion in EXACT).
- **Display-label / detail resolver** — matcher detail output
  (`user_to_trial_attr_matcher.py:~202`) builds labels/display maps, not just raw sets; these are
  profile-dependent too.
- **Generic field access** — the generic matcher access point
  (`user_to_trial_attr_matcher.py:~339`) and config/count/detail metadata
  (`THERAPIES_ATTRS_UNDERSCORED`, `TRIAL_ATTRS_JSON_AS_A_LIST`, `SUBFORM_ATTRS_MAPPING`) must resolve
  through the profile.

Keep it thin: an operations profile with the items above, two concrete profiles, no method forking.

## Semantics that MUST be preserved (easy to break)
- Distinguish three patient states end-to-end: **"no therapy"** vs **"unknown / not answered"** vs
  **"resolved to an empty set"**. The matcher's matched / not_matched / unknown outcomes depend on
  this (`therapy_related_things_mismatch_status()`, `_match_therapy_things()`). Under OMOP an empty
  consumer set must NOT be silently read as "unknown".
- Required-empty = trial imposes no requirement (match). Excluded overlap = hard reject. Patient
  empty/unknown = do not filter in queryset; matcher may return `unknown`.
- Authoring-level symmetry: trial fields (CB-authored) and patient concepts (consumer-supplied) must
  use the same OMOP leveling, especially for exclusions — an over-broad patient set can spuriously
  trigger `*_excluded`.

## Port workflow (do this every time)
1. Identify the CB commit/diff range to port.
2. Apply the CB diff to EXACT **as-is** for matcher/queryset/config bodies.
3. Resolve conflicts ONLY in: (a) the profile/seam module, (b) EXACT's known structural adaptations
   (dispatch tables — see below). Do **not** alter ported logic to resolve a conflict.
4. If the CB change introduces a **new therapy/trial field or a new field reference**, add it to the
   profile in **both** repos (upstream to CB first), never hardcode it in EXACT.
5. If the CB change touches **patient-therapy set construction**, route it through the profile's
   set-builder; ensure the OMOP profile has a matching (often no-op / consumer-provided) variant.
6. Run verification (below). Keep the OMOP flag OFF for the port itself.

## Known intentional divergences (do NOT "fix" or mistake for drift)
These already differ from CB on purpose. Leave them; do not try to re-sync them to CB:
- EXACT uses **dispatch tables** for custom_search (`trials/querysets/trial.py` `_CUSTOM_SEARCH_*`,
  matcher `_CUSTOM_SEARCH_NAMED_HANDLERS`) vs CB's `if/elif` chain.
- EXACT extracted `_match_therapy_related_things` / `_match_therapy_things` helpers.
- `websearch_to_tsquery` (EXACT) vs legacy tsquery (CB).
- `expand_receptor_values()` for ER/PR/HR (EXACT only).
- Parent-stage regex matching (EXACT only).
- DB routing / read-only trials DB / OMOP fields (EXACT only).
Maintain an up-to-date inventory of these so agents can tell *intended* divergence from *drift*.

## Anti-patterns (never do during a port)
- Restyling/restructuring ported code to EXACT conventions.
- Hardcoding `omop_*` (or legacy `therapies_required` etc.) field names in active matcher/queryset/
  config code.
- Building a general plugin/strategy framework where a 2-instance profile suffices.
- Adding patient state to EXACT, or porting CB's internal-M2M expansion into EXACT's OMOP path.
- Letting a port flip the OMOP flag or change legacy output.

## Guardrails / CI (detect drift early)
1. **Drift detector** — a CI script comparing normalized therapy-relevant files (or AST/function
   bodies) between CB and EXACT, with an **allowlist** for the profile module and the known
   structural divergences above. Fails when unexplained divergence grows.
2. **Hardcoded-field scanner** — fails if active matcher/queryset/config code introduces raw
   `therapies_required`, `therapy_components_required`, `therapy_types_required`, etc. (or their
   `omop_*` forms) **outside** the profile module.
3. **Parity test** — legacy-mode EXACT output must equal CB output on a shared corpus; OMOP-mode
   tested separately.

## Verification before declaring a port done
- `python manage.py makemigrations --check` → "No changes detected" (if models touched).
- Therapy matcher/queryset/config tests pass.
- Drift detector + hardcoded-field scanner pass.
- Legacy parity unchanged; OMOP flag still OFF by default.
