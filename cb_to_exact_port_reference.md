---
name: CB → EXACT Port Reference (MCL + drift)
description: Specific CB source paths and line numbers for porting MCL support and other drift items into EXACT. Used as a lookup when working on the CB-drift GitHub issues.
type: reference
originSessionId: 4860a162-5821-4865-a7a3-f619307b57d4
---
# CB → EXACT Port Reference

CancerBot path: `/Users/lm/work/biblum/cancerbot`
EXACT path: `/Users/lm/work/cancerbot/exact`

This file holds the specific CB references intentionally kept OUT of the GH issues (so issues stay portable). Cross-reference by issue title.

> **Names below are as of the port, not as of today.** CB has renamed some of them since.
> Known drift: `lesion_size_mcl_min` / `lesion_size_mcl_max` / `lesion_size_mcl` were renamed
> upstream to `largest_lesion_size_min` / `largest_lesion_size_max` / `largest_lesion_size`
> (CB migration `0375`, ported here in `trials/migrations/0009_align_with_cb_catalog`) — the
> `_mcl` suffix is gone because "largest lesion size" is a generic measurement. Check the current
> CB models before porting anything from the sections below.

---

## Architectural reminder

EXACT's `PatientInfo` is a **stateless Python class** at `trials/services/patient_info/patient_info.py:237`, built with `_f(field_cls, name, default=None, **kwargs)` factory + `_Meta`. **Do not add a Django model** for patient fields when porting from CB — declare them on the Python class instead, and update `configs.py` + `normalize.py`. Trial-side fields ARE Django model fields and DO need migrations.

CB diseases supported now: MM, FL, BC, CLL, **MCL** (the new one).
EXACT diseases supported: MM, FL, BC, CLL.

---

## Issue: MCL foundation (disease + markers + variants)

CB foundation migration: `trials/migrations/0352_mcl_foundation.py`
- Lines 6–15: 8 protein expression markers (Cyclin D1, SOX11, CD10±, BCL6±, …)
- Lines 17–21: 3 morphologic variants (classic, blastoid, pleomorphic)
- Lines 48–56: TrialTypeDiseaseConnection entries for MCL

CB matcher disease normalizer: `trials/services/user_to_trial_attr_matcher.py:47`
```python
elif disease == 'mantle cell lymphoma': return 'MCL'
```

EXACT targets:
- `trials/models.py` — `Disease` table seed (data migration)
- `trials/services/loaders/` — protein expression + morphologic variant loaders
- `trials/services/user_to_trial_attr_matcher.py` — disease code map

---

## Issue: MCL Trial model fields

CB Trial model: `trials/models.py:578-604` (added via migrations 0353–0367)

Fields to add to EXACT `Trial`:
- `lesion_size_mcl_min`, `lesion_size_mcl_max` — FloatField (CB models.py:597-598)
- `morphologic_variants_required`, `morphologic_variants_excluded` — JSONField list (CB models.py:578-579)
- `disease_behaviors_required` — JSONField (CB models.py:601)
- `disease_subtypes_required` — JSONField (CB models.py:602)
- `extranodal_sites_required` — JSONField (CB models.py:580)
- `bulky_disease_criteria_required` — JSONField (CB models.py:596)
- `mipi_risks_required`, `mipi_c_risks_required` — JSONField (CB models.py:603-604)
- GinIndex updates: CB models.py:562-598

CB-side migrations to inspect for column types/defaults:
- `0353_mcl_lesion_bulky.py`
- `0354_mcl_p53_ihc.py` (note: p53_ihc may have been removed/renamed — check final state)
- `0355_mcl_morphologic_variant.py`
- `0356_*` through `0367_*`

EXACT can collapse these into a single migration (we don't need CB's history).

---

## Issue: MCL fields on stateless PatientInfo

CB PatientInfo model: `trials/models.py:1323-1338` (CB has it as a Django model, EXACT does not)

Fields to add to EXACT `PatientInfo` Python class (`trials/services/patient_info/patient_info.py`):
- `morphologic_variant`
- `lesion_size_mcl`
- `disease_behavior`
- `disease_subtype`
- `extranodal_sites` (list)
- `mipi_risk`
- `mipi_c_risk`
- `bulky_disease_criteria`

Use the `_f(...)` factory pattern — see how existing fields like `disease`, `weight`, etc. are declared.

---

## Issue: MIPI / MIPI-C / bulky disease scoring

CB source: `trials/services/patient_info/patient_info_attributes.py`
- Lines 476–501: `mipi_risk` property — inputs: age, ECOG, WBC, LDH → low/intermediate/high
- Lines 503–516: `mipi_c_risk` property — inputs: MIPI + Ki67 index → 4-tier stratification (low / low_intermediate / high_intermediate / high)
- Lines 518–530+: `bulky_disease_criteria` property — checks lesion_size_mcl, largest_lymph_node_size, spleen_size against thresholds

EXACT target: `trials/services/patient_info/patient_info_attributes.py:PatientInfoAttributes` + wire into `normalize.py`.

CB tests for these: look in CB `tests/` for `test_patient_info_attributes.py` (mirror them).

---

## Issue: USER_TO_TRIAL_ATTRS_MAPPING updates

CB source: `trials/services/patient_info/configs.py`

**Expand existing entries** to include MCL:
- Line 219: `cytogenic_markers` — disease list now includes MCL
- Line 232: `molecular_markers` — disease list now includes MCL
- Line 298: `protein_expressions` — disease changed from `"CLL"` to `["CLL", "MCL"]`
- Line 335: `tp53_disruption` — add MCL
- Lines 347–383: `hepatomegaly`, `lymphadenopathy`, `largest_lymph_node_size`, `splenomegaly`, `spleen_size` — add MCL; type changed from `"min_value"` to `"min_max_value"` for the size-based ones

**New MCL entries** (CB configs.py:455-545):
- `bulky_disease_criteria`
- `lesion_size_mcl`
- `p53_ihc` (verify this still exists)
- `morphologic_variant`
- `disease_behavior`
- `disease_subtype`
- `mipi_risk`
- `mipi_c_risk`
- `extranodal_sites`

`TRIAL_ATTRS_JSON_AS_A_LIST` additions: CB configs.py:103-111.

---

## Issue: value_options.py MCL enums

CB source: `trials/services/value_options.py`
- Lines 1004–1005: `therapies_later_mcl` property
- Lines 1019–1020: `supportive_therapies_mcl` property
- Line 1022+: `concomitant_medications_by_disease_code()` extension for MCL
- Lines 1130–1131: `stages('MCL')` (Lugano staging)
- Lines 1133–1134: `disease_behaviors_mcl`
- Lines 1136–1137: `disease_subtypes_mcl`
- Lines 1139–1140: `protein_expressions_mcl`
- Lines 1142–1143: `morphologic_variants`
- Lines 1145–1149: `mipi_risks`, `mipi_c_risks`

Each needs a property + entry in the central options dict.

---

## Issue: trial_taxonomy.py

CB source: `trials/trial_taxonomy.py` (~130 lines)
- Lines 11–142: full `TRIAL_TAXONOMY` dict + `ALL_TRIAL_TYPES` list
- ~264 MCL references across this file
- Lines 15, 16, 20, 25, 26, 27, 30, 33, 34, 36, 37, 38, 40, 41, 42 — therapy types that apply to MCL (PI3K notably excluded)

CB seeding migrations:
- `0349_seed_trial_types_from_taxonomy.py` — initial seed for MM/FL/BC/CLL
- `0352_mcl_foundation.py:48-56` — MCL TrialTypeDiseaseConnection entries

EXACT target: create `trials/trial_taxonomy.py` mirroring the structure, plus a seed migration.

---

## Issue: by_trial_purpose() queryset

CB source: `trials/querysets/trial.py:407-410`
```python
def by_trial_purpose(self, purpose):
    return self.filter(purpose=purpose)
```

Note: requires `Trial.purpose` FK to a `TrialPurpose` model — check if EXACT has either; likely needs both model + field + queryset method.

---

## Issue: _disease_attr_applies() matcher refactor

CB source: `trials/services/user_to_trial_attr_matcher.py:50-58`

Replaces inline `if disease in [...]` checks with a single helper. Pattern:
```python
def _disease_attr_applies(self, attr_disease, patient_disease):
    if isinstance(attr_disease, list):
        return patient_disease in attr_disease
    return attr_disease == patient_disease
```
Then call sites become `if not self._disease_attr_applies(cfg["disease"], pi.disease): continue`.

---

## Issue: Remove receptor-status parent-code expansion

CB removed in:
- `trials/querysets/trial.py:1048-1071` (deleted the static parent-code map)
- `trials/services/patient_info/configs.py:21-33` (deleted the expansion logic)

EXACT may still carry the old logic in `value_options.py` / queryset filters. This is BC-specific (ER/PR/HER2 hierarchy) and was simplified upstream.

---

## Bugfix drift (CB → EXACT, GH issues #50–#63)

These are CB bugfixes that landed after EXACT was forked. Each EXACT issue links back to the upstream CB commit/PR.

### Correctness — change matcher output

- **#50** `fix: SCT 'None' list-form not recognized` — CB #4333 (`55dbe61e`) + #4340 (`952bbc74`) + #4339 (`98531a23`)
  - Files: `matcher.py:~268`, `querysets/trial.py` (`eligible_for_stem_cell_transplant_history`), `configs.py` (karnofsky + `sct_value_is_none()` helper)
  - Includes rename `user_has_sct` → `user_has_no_sct`
- **#51** `fix: relapse_count=0 treated as blank` — CB #4186 (`135f5108`)
  - Files: `querysets/trial.py:~1190`, three `eligible_for_min_max_value` call sites (lines ~1342, 1349, 1360)
- **#52** `fix: empty-string progression = Unknown` — CB #4306 (`d8e33c80`)
  - Files: `matcher.py:393`
- **#53** `fix: Grade 0 peripheral_neuropathy not matched` — CB #4307 (`8dceb78f`)
  - Files: `configs.py:211` (add `allow_blank_values: True`)
- **#54** `fix: meets_slim / measurable_disease_imwg shown as Ineligible` — CB #4143/#4156 (`9a173d13`)
  - Files: `configs.py:~929` (uncomment, add `under_user_control: True`), `normalize.py` (return None when all IMWG components None)

### Display / dropdowns / wiring

- **#55** ECOG=4 missing — CB `fa77c97b` → `value_options.py:334`
- **#56** Remove Tumor Grade 4 (FL) — CB `a8fd82c2` → `value_options.py:237`
- **#57** Remove Peripheral Neuropathy Grade 5 — CB `887be4d9` → `value_options.py:395`
- **#58** Add 'None' to molecular/cytogenic markers — CB #4216 (`05fba563`) → `value_options.py`
- **#59** Grade 0 + 'Unknown' label in peripheralNeuropathyGrade — CB #4307 (`4ad80a1f`) → `value_options.py:387`
- **#60** BC treatment outcome → CR/PR/SD/PD only — CB #4137 (`383bc946`) → `value_options.py` (add `therapy_outcomes_by_disease_code()`) + `patient_info_details.py`
- **#61** Add 'No planned therapy' option — CB `33f7525a` → `value_options.py:255`
- **#62** Wire meetsLugano/meetsGELF + scope to FL — CB #4144 (`a448fd30`) + #4223 (`62d5f089`)
  - Files: `patient_info.py` (`_f()` for both), `configs.py` (mapping entries with `disease: 'FL'`, `under_user_control: True`), `trial_details/configs.py` (`ATTR_FALSE_VALUE_IS_BLANK`)
- **#63** Disease-aware option lists (FLIPI/Binet/Richter/etc) — CB #4330 (`ddd5a1e9` + `dbcc1a7f`)
  - Lower priority; verify EXACT frontend consumes per-disease keys before wiring

### Recommended starter batch (one PR)

#51, #52, #50, #53, #54 — all correctness, narrow surface, well-tested upstream. Mirror CB's tests.

---

## Skip list (CB-only, do NOT port)

- `UserTrial` model + `add_favorite()` + `add_for_participation()` annotations (CB `querysets/trial.py:79-103`) — favoriting is CB's job, not EXACT's.
- `with_applicants_count()` annotation (CB `querysets/trial.py:612-617`).
- `with_goodness_score_optimized()` user_profile parameter changes (CB `querysets/trial.py:619-720`) — EXACT scoring is independent.
- CB `8bfa8e5f`, `56bb4ab4`, `50e02d85`, `b344c89e` — all about CB's `PatientInfo.save()` / `update_fields` Django-model semantics. EXACT's `PatientInfo` is stateless (no `save()`); the bug doesn't exist by construction. Intent (always recompute derived fields) guaranteed via `normalize.py`.
- CB `7d3e06fa` (`matchingScore` mismatch) — already in EXACT (`with_goodness_score_optimized` signature already includes `geo_point` / `recruitment_status`).
- CB `e37d4f93` (Refractory status matching) — already in EXACT (`matcher.py:425` uses `if not patient_info_attr_value`).

---

## Verification commands

```bash
# CB MCL surface
grep -rni 'mcl\|mantle' /Users/lm/work/biblum/cancerbot/trials/ --include='*.py' | wc -l

# EXACT MCL surface (should be 0 today)
grep -rni 'mcl\|mantle' /Users/lm/work/cancerbot/exact/trials/ --include='*.py' | wc -l

# Diff configs side-by-side
diff /Users/lm/work/biblum/cancerbot/trials/services/patient_info/configs.py \
     /Users/lm/work/cancerbot/exact/trials/services/patient_info/configs.py

# Diff matcher
diff /Users/lm/work/biblum/cancerbot/trials/services/user_to_trial_attr_matcher.py \
     /Users/lm/work/cancerbot/exact/trials/services/user_to_trial_attr_matcher.py
```
