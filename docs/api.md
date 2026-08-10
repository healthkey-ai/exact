# API Reference

Base path: `/` (no versioning prefix).

Interactive docs are available at runtime:
- Swagger UI: `/swagger/`
- ReDoc: `/redoc/`

All endpoints require an `Authorization: Token <token>` header unless noted
otherwise. To create a token for a user:

```bash
python manage.py drf_create_token <username>
```

JSON payloads and responses use **camelCase** keys (converted by
`djangorestframework-camel-case`).

---

## Patient context

Every trial-search endpoint accepts an optional patient profile supplied
**inline in the request body**. Patient data is never stored — it is built
in-memory, normalized, used for matching, and discarded at the end of the
request.

```json
{
  "patientInfo": {
    "disease": "multiple myeloma | follicular lymphoma | breast cancer | chronic lymphocytic leukemia",
    "patientAge": 55,
    "gender": "M | F | UN",
    "weight": 75,
    "weightUnits": "kg | lbs",
    "height": 175,
    "heightUnits": "cm | inch",
    "country": "US",
    "postalCode": "10001",
    "longitude": -74.006,
    "latitude": 40.7128,

    "priorTherapy": "None | One line | Two lines | More than two lines of therapy",
    "firstLineTherapy": "vrd",
    "firstLineDate": "2022-03-01",
    "firstLineOutcome": "CR | PR | SD | MRD | PD",
    "secondLineTherapy": "kd",
    "secondLineDate": "2023-01-15",
    "secondLineOutcome": "CR | PR | SD | MRD | PD",
    "laterTherapy": "isa-kd",
    "laterDate": "2024-06-01",
    "laterOutcome": "CR | PR | SD | MRD | PD",
    "laterTherapies": [],

    "stage": "I | II | III | IV",
    "karnofskyPerformanceScore": 80,
    "ecogPerformanceStatus": 1,

    "preExistingConditionCategories": ["cardiacIssues", "pulmonaryDisease"],

    "hemoglobinLevel": 10.5,
    "plateletCount": 150,
    "whiteBloodCellCount": 4.5,
    "serumCreatinineLevel": 1.0,
    "estimatedGlomerularFiltrationRate": 60,
    "liverEnzymeLevelsAlt": 30,
    "liverEnzymeLevelsAst": 25,
    "liverEnzymeLevelsAlp": 80,
    "albuminLevel": 3.5,
    "serumBilirubinLevelTotal": 0.8,
    "serumBilirubinLevelDirect": 0.2,
    "serumCalciumLevel": 9.5,
    "monoclonalProteinSerum": 1.5,
    "monoclonalProteinUrine": 200,
    "lactateDehydrogenaseLevel": 250,

    "geneticMutations": [
      {
        "gene": "tp53",
        "variant": "c.817C>T",
        "origin": "somatic",
        "interpretation": "pathogenic"
      }
    ],

    "stemCellTransplantHistory": "None | completedASCT | eligibleForASCT | ineligibleForASCT | completedAllogeneicSCT | preASCT | postASCT | neverReceivedSCT | sctIneligible | relapsedPostASCT | relapsedPostAllogeneicSCT | completedTandemSCT",
    "plasmaCellLeukemia": false,

    "estrogenReceptorStatus": "er_plus | er_plus_with_hi_exp | er_plus_with_low_exp | er_minus",
    "progesteroneReceptorStatus": "pr_plus | pr_plus_with_hi_exp | pr_plus_with_low_exp | pr_minus",
    "her2Status": "her2_plus | her2_minus",
    "menopausalStatus": "pre | post",
    "tumorStage": "string",
    "pdL1TumorCells": 50,

    "binetStage": "A | B | C",
    "treatmentRefractoryStatus": "notRefractory | primaryRefractory | secondaryRefractory | multiRefractory"
  }
}
```

Normalization runs automatically: BMI, `geo_point`, FLIPI score, TNBC/HR
status, treatment refractory status, and other derived fields are computed
from the supplied values.

---

## Study preferences

Search/filter preferences are passed as **query parameters** on every
trial-search request.

> **Terminology note**: the per-trial match result is called `matchingType` in
> JSON responses and `match status` in prose. The `type` query param filters by
> this value (`eligible`, `potential`, `not_eligible`).

| Param | Type | Description |
|---|---|---|
| `searchTitle` | string | Full-text search on trial title |
| `recruitmentStatus` | string | Filter by recruitment status (e.g. `RECRUITING`). Note: the query param is `recruitmentStatus` but the field returned in `/trials/` list responses is `recruitingStatus`. |
| `sponsor` | string | Filter by sponsor name |
| `register` | string | Filter by trial register (e.g. `clinicaltrials.gov`) |
| `trialType` | string | Filter by trial-type code |
| `trialPurpose` | string, repeatable | Filter by trial-purpose code (`treatment`, `screening`, …), case-insensitive. Several purposes are a union — a trial matching any of them comes back: repeat the param (`?trialPurpose=treatment&trialPurpose=supportive_care`) or pass one comma-separated value. Trials whose purpose was never extracted are not returned. |
| `validatedOnly` | boolean | Only return manually validated trials |
| `distance` | number | Maximum distance from patient location |
| `distanceUnits` | `km` \| `miles` | Units for `distance` (default `km`) |
| `country` | string | Filter by country code |
| `region` | string | Filter by region |
| `postalCode` | string | Override postal code for distance calculation |
| `studyId` | string | Filter by study ID (e.g. NCT number) |
| `lastUpdate` | date | Filter trials updated after this date |
| `firstEnrolment` | date | Filter trials with first enrolment after this date |

---

## Trials

### `GET /trials/`

List trials ordered by match score descending. For explicit sorting or the
`favorites` filter, use [`GET /trials/search/`](#get-trialssearch) instead.

**Patient context**: optional — include `"patientInfo": {...}` in the request body.

**Query params:**

| Param | Values | Description |
|---|---|---|
| `type` | `all`, `eligible`, `potential`, `not_eligible` | Filter by match status |
| `search` | string | Full-text search on title fields |
| `explain` | `true` | Include per-criterion match breakdown in each trial (see `matchReasons` below) |

**Response** (paginated, 200 per page by default):
```json
{
  "count": 142,
  "next": "http://…/trials/?page=2",
  "previous": null,
  "results": [
    {
      "trialId": 1,
      "studyId": "NCT03000000",
      "briefTitle": "A Study of Drug X in Myeloma",
      "phase": ["PHASE2"],
      "disease": "Multiple Myeloma",
      "recruitingStatus": "RECRUITING",
      "location": ["New York, NY, USA", "Boston, MA, USA"],
      "distance": 12.3,
      "distanceUnits": "miles",
      "matchScore": 85,
      "matchingType": "eligible",
      "attributesToFillIn": [
        {
          "trialAttributeName": "stem_cell_transplant_history_required",
          "userAttributeName": "stem_cell_transplant_history",
          "userAttributeTitle": "Stem Cell Transplant History",
          "count": 4
        }
      ],
      "matchReasons": null,
      "goodnessScore": 72,
      "patientBurdenScore": 15,
      "enrollmentCount": 120,
      "sponsor": "BioPharm Inc.",
      "link": "https://clinicaltrials.gov/ct2/show/NCT03000000"
    }
  ]
}
```

#### `matchReasons` — per-criterion explanation (`?explain=true`)

When `?explain=true` is passed and a patient context is provided, each trial
in the response includes a `matchReasons` array instead of `null`. Each entry
describes one eligibility criterion:

| Field | Type | Description |
|---|---|---|
| `attr` | string | Criterion key (matches `USER_TO_TRIAL_ATTRS_MAPPING`) |
| `status` | `"matched"` \| `"unknown"` \| `"not_matched"` | Whether the patient meets this criterion |
| `patientValue` | any \| `null` | The patient's value for this attribute (`null` if not provided) |
| `trialRequirement` | any \| `null` | The trial's requirement — scalar, `{"min": …, "max": …}`, or `null` for computed criteria |

Results are sorted: `not_matched` first (disqualifiers), then `unknown`
(missing patient data), then `matched`. Only criteria relevant to the trial's
disease are included.

```json
GET /trials/?explain=true

"matchReasons": [
  {"attr": "patient_age", "status": "not_matched", "patientValue": 70, "trialRequirement": {"min": 18, "max": 65}},
  {"attr": "ecog_performance_status", "status": "unknown", "patientValue": null, "trialRequirement": 2},
  {"attr": "gender", "status": "matched", "patientValue": "M", "trialRequirement": null}
]
```

Without `?explain=true`, or when no patient context is present, `matchReasons`
is `null`.

---

### `GET /trials/search/`

Extended search endpoint — use this instead of `GET /trials/` when you need
explicit sorting or access to the `favorites` filter type. Accepts the same
patient context and study-preference query params as `GET /trials/`, plus:

**Patient context**: optional — include `"patientInfo": {...}` in the request body.

**Query params:**

| Param | Values | Default | Description |
|---|---|---|---|
| `type` | `all`, `eligible`, `potential`, `not_eligible`, `favorites` | `all` | Match-status filter |
| `sort` | `goodnessScore`, `matchScore`, `patientBurdenScore`, `distance`, `status`, `phase`, `updated`, `enrollment` | `goodnessScore` | Sort order |
| `view` | template name | — | Override the attribute-detail template |
| `search` | string | — | Full-text search |

---

### `GET /trials/count/`

Returns the count of matched trials without fetching full records.

**Patient context**: optional — include `"patientInfo": {...}` in the request body.

**Response:**
```json
{ "count": 37 }
```

---

### `GET /trials/{id}/`

Retrieve full trial details including all eligibility attributes grouped for
display, with per-attribute patient match status.

**Patient context**: optional — include `"patientInfo": {...}` in the request body.

**Response:** Full trial object including `trialEligibilityAttributes` grouped
by category, each with the trial's value, the patient's current value, and the
match status (`matched`, `unknown`, or `not_matched`).

---

## Trial graph

### `GET /trials-graph/graph/`

Returns a compact graph-structured response optimised for visual dependency
views.

**Patient context**: optional — include `"patientInfo": {...}` in the request body.

**Query params:**

| Param | Default | Description |
|---|---|---|
| `n` | 50 | Maximum number of trial nodes to return |

**Response:**
```json
{
  "trialNodes": [
    {
      "trialId": 1,
      "briefTitle": "A Study of Drug X",
      "matchScore": 85,
      "attributes": {
        "matched": [
          { "label": "Age", "trialValue": "18–65", "patientValue": "52" }
        ],
        "notMatched": [
          { "label": "Gender", "trialValue": "Female only", "patientValue": "M" }
        ],
        "missing": [
          { "label": "Stem Cell Transplant History", "trialValue": "required", "patientValue": null }
        ]
      }
    }
  ]
}
```

---

## Form settings

### `GET /form-settings/`

Returns all dropdown option lists used by patient-intake forms.

**Query params:**

| Param | Description |
|---|---|
| `disease` | If provided, also returns `trialTypes` scoped to this disease |

**Response** (partial example):
```json
{
  "disease": [
    { "value": "multiple myeloma", "label": "Multiple Myeloma" },
    { "value": "follicular lymphoma", "label": "Follicular Lymphoma" },
    { "value": "breast cancer", "label": "Breast Cancer" },
    { "value": "chronic lymphocytic leukemia", "label": "Chronic Lymphocytic Leukemia" }
  ],
  "gender": [
    { "value": "", "label": "Unknown" },
    { "value": "M", "label": "Male" },
    { "value": "F", "label": "Female" }
  ],
  "stemCellTransplantHistory": [ ... ],
  "firstLineTherapy": [ ... ],
  "cytogenicMarkers": [ ... ],
  "molecularMarkers": [ ... ],
  "ethnicity": [ ... ],
  "therapyTypesAll": [ ... ],
  "therapyComponentsAll": [ ... ],
  "trialTypes": [ ... ]
}
```

---

## Lookup tables

### `GET /countries/`

Returns the list of preferred countries for the location picker.

### `GET /locations/`

Returns locations (trial sites). Optional filters:

| Param | Description |
|---|---|
| `country_id` | Filter by country FK |
| `state_id` | Filter by state FK |

---

## Pagination

All list endpoints return a standard paginated envelope:

```json
{
  "count": 200,
  "next": "http://…?page=2",
  "previous": null,
  "results": [ ... ]
}
```

Default page size is 20. Override with `?limit=<n>` up to a maximum of 200.
Requests with `limit` < 1, > 200, non-integer, or empty return
`400 Bad Request` with details under the `limit` key (#33).

---

## Error responses

| Status | When |
|---|---|
| `400 Bad Request` | Validation error in request body or query params |
| `401 Unauthorized` | Missing or invalid auth token |
| `404 Not Found` | Record not found |
| `500 Internal Server Error` | Unexpected server error |

Error body:
```json
{ "detail": "Human-readable error message" }
```

or for field-level validation errors:
```json
{ "fieldName": ["This field is required."] }
```
