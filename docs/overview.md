# EXACT — Architecture Overview

EXACT (EXtracting Attributes from Clinical Trials) is a stateless search and
matching engine for clinical trials. It reads trial data from an external
database — it does not own or manage the trial catalog. Patient data is always
passed inline per request; nothing is persisted.

---

## What it does

1. **Reads the trial catalog** — connects to an external database containing
   `Trial` records with 150+ structured eligibility-criteria fields.
2. **Matches patients to trials** — scores and ranks trials against a patient's
   profile using a disease-aware attribute matching algorithm.
3. **Exposes a REST API** — callers send a `patientInfo` JSON object with each
   request; the engine builds an in-memory profile, runs matching, and returns
   ranked trial results. No patient state is stored.

---

## High-level components

```
┌──────────────────────────────────────────────────────┐
│                   REST API (DRF)                     │
│  /trials/   /trials-graph/                           │
│  /form-settings/  /countries/  /locations/           │
└────────────────────┬─────────────────────────────────┘
                     │
        ┌────────────▼────────────┐
        │   resolve_patient_info  │  stateless only:
        │   (trials/services/     │  builds PatientInfo in memory
        │    patient_info/        │  from JSON body
        │    resolve.py)          │
        └────────────┬────────────┘
                     │
     ┌───────────────▼──────────────────┐
     │        TrialQuerySet             │
     │  filtered_trials()               │  SQL-level filters
     │  with_goodness_score_optimized() │  ORM annotations
     │  with_distance_optimized()       │
     └───────────────┬──────────────────┘
                     │
     ┌───────────────▼──────────────────┐
     │   UserToTrialAttrMatcher         │
     │   per-trial scoring & status     │  Python-level scoring
     │   (eligible / potential /        │
     │    not_eligible)                 │
     └───────────────┬──────────────────┘
                     │
     ┌───────────────▼──────────────────┐
     │  TrialTemplates / TrialAttributes│  Presentation layer
     │  attribute labeling & grouping   │
     └──────────────────────────────────┘
```

---

## Key models

| Model | Purpose |
|---|---|
| `Trial` | One clinical trial, ~150 eligibility-criteria fields |
| `PatientInfo` | In-memory patient profile (unmanaged — never persisted) |
| `Location` / `LocationTrial` | Geographic trial sites (PostGIS) |
| `Therapy` / `TherapyComponent` / `TherapyComponentCategory` | Therapy taxonomy |
| `CytogenicMarker` / `MolecularMarker` | Biomarker taxonomy |
| `ConcomitantMedication` | Medication taxonomy |
| `PreExistingConditionCategory` | Comorbidity categories |
| `TrialType` / `TrialTypeDiseaseConnection` | Trial-type taxonomy by disease |

---

## Key services

### `resolve_patient_info` (`trials/services/patient_info/resolve.py`)

Builds an unsaved `PatientInfo` instance from the `"patientInfo": {...}` key
in the request body. Converts camelCase to snake_case, filters to known fields,
attaches synthetic M2M attributes, and calls `normalize_patient_info`. Returns
`None` if no `patientInfo` payload is present.

### `study_preferences_from_query_params` (`trials/services/study_preferences.py`)

Builds a `StudyPreferences` dataclass from query parameters (distance,
distanceUnits, recruitmentStatus, searchTitle, sponsor, register, trialType,
validatedOnly, etc.). Replaces the former `StudyInfo` DB model — no persistence,
no lookup.

### `normalize_patient_info` (`trials/services/patient_info/normalize.py`)

Pure function (no DB write). Computes all derived fields from raw inputs:

- Clears downstream therapy fields when `prior_therapy` is reduced
- Computes treatment refractory status from therapy outcomes
- Sets `geo_point` from country/postal code or lat/lon
- Computes FLIPI score (follicular lymphoma)
- Derives TNBC and HR status from receptor statuses (breast cancer)
- Derives metastatic status and IMWG measurable-disease criteria (myeloma)
- Sets `last_treatment` date from the most recent therapy line

### `UserToTrialAttrMatcher` (`trials/services/user_to_trial_attr_matcher.py`)

Per-trial scoring engine. Given one `(trial, patient_info)` pair:

- Returns a **match status**: `eligible`, `potential`, or `not_eligible`
- Returns a **match score** (0–100): percentage of checked attributes that match
- Returns per-attribute statuses: `matched`, `unknown`, or `not_matched`
- Is disease-aware — skips attributes that don't apply to the patient's disease

### `TrialQuerySet` (`trials/querysets/trial.py`)

The SQL-level filtering layer. Key methods:

- `filtered_trials(search_options, study_info, patient_info)` — applies all
  active filters (disease, therapy, markers, distance, etc.) and annotates the
  queryset with `match_score`. `study_info` is a `StudyPreferences` dataclass
  built from query params; `search_options` carries additional search-level flags.
- `with_goodness_score_optimized(geo_point=None, recruitment_status=None, ...)` —
  annotates with a weighted composite score (benefit, patient burden, risk,
  distance). When `geo_point` is provided it computes distance via an inline
  subquery; `with_distance_optimized` does not need to be called first.
- `with_distance_optimized(geo_point)` — annotates with distance to nearest
  recruiting site. Only needed when distance is used independently of the
  goodness score (e.g. for sorting by distance alone).
- `with_potential_attrs_count(patient_info)` — annotates each trial with the
  count of patient attributes that are blank but required.

### `TrialMatchExplainer` (`trials/services/trial_match_explainer.py`)

Produces a per-criterion eligibility explanation for a trial+patient pair.
Used by `TrialSerializer` when `?explain=true` is passed to the search API,
and by the `explain_trial_match` management command.

Returns a list of `{attr, status, patientValue, trialRequirement}` dicts,
sorted `not_matched → unknown → matched`. Only criteria relevant to the
trial's disease are included.

### `TrialTemplates` / `TrialAttributes` (`trials/services/trial_details/`)

Presentation layer. Formats trial details for API responses:

- Groups attributes into display buckets (`general`, `trialEligibilityAttributes`)
- Attaches patient values alongside trial values for each attribute
- Computes per-attribute matching types for display

### Reference data seeders (`trials/services/loaders/`)

Internal modules called by the `seed_reference_data` management command. Each
`load_*.py` file populates one taxonomy table (therapies, markers, medications,
etc.) using hardcoded data. Despite the `load_` prefix, these are **seeders**
— there is no corresponding dump/export. The name predates the public `seed_*`
command convention.

### `ValueOptions` (`trials/services/value_options.py`)

Single source of truth for all enumeration dropdowns (therapies by disease,
markers, outcomes, staging options, ethnicity, etc.). Consumed by the API's
`/form-settings/` endpoint and by `TrialAttributes`.

---

## Patient-info lifecycle (stateless)

```
GET /trials/
  │
  ├── request.data["patient_info"] → resolve_patient_info()
  │         │
  │         ├── camelCase → snake_case
  │         ├── filter to known PatientInfo fields
  │         ├── PatientInfo(**fields)  ← unsaved, in-memory only
  │         └── normalize_patient_info(pi)
  │
  ├── request.query_params → study_preferences_from_query_params()
  │         └── StudyPreferences dataclass (distance, filters, etc.)
  │
  └── filtered_trials(study_info=study_prefs, patient_info=pi)
            └── results returned — nothing written to DB
```

---

## Database architecture

EXACT uses a **split-database** model:

| Database | Alias | What it holds | Managed by EXACT? |
|---|---|---|---|
| Local | `default` | Auth users, tokens, sessions | Yes — EXACT runs migrations |
| External | `trials` | Trial catalog, reference data (therapies, markers, etc.) | **No** — schema is owned externally |

`TrialsDatabaseRouter` (`exact/db_router.py`) routes all `trials` app model
reads/writes to the external database and blocks migrations from running on it.

When `TRIALS_DATABASE_URL` is not set, the router is inactive and everything
falls back to `default` (standalone mode — everything in one database).

### Key indexes (on the external trials database)

- `GistIndex` on `Location.geo_point` for fast distance queries.
- `GinIndex` on trial JSON array fields (therapies, markers, stages) for
  containment lookups.

### PatientInfo

`PatientInfo` is a plain Python class (not a Django model) — it exists for
in-memory use only. No table is created or maintained. Patient data is built
from the request body on every API call.

---

## Authentication

All API endpoints require authentication. The service supports:

- **Token authentication** (`Authorization: Token <token>`) — primary method
  for service-to-service calls.
- **Session authentication** — for browser-based clients / Django admin.

Tokens are managed via the standard DRF `rest_framework.authtoken` app.

---

## Diseases supported

| Code | Disease |
|------|---------|
| MM | Multiple Myeloma |
| FL | Follicular Lymphoma |
| BC | Breast Cancer |
| CLL | Chronic Lymphocytic Leukemia |

The matching engine is disease-aware: criteria that don't apply to a patient's
disease are skipped entirely rather than marked as "not matched".
