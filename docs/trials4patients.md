# trials4patients.sh

Runs EXACT trial matching for a set of patients directly against the patient
and trial databases — no web server required. Results are written to a JSON
file and optionally to an evaluator-compatible CSV.

---

## Prerequisites

1. EXACT local DB migrated: `python manage.py migrate`
2. `TRIALS_DATABASE_URL` — remote trials database
3. `PATIENT_DATABASE_URL` — patient database

---

## Usage

```bash
bash scripts/trials4patients.sh
```

All configuration is via environment variables. The script loads `.env` from
the project root automatically, so the recommended approach is to copy
`scripts/.env.example` to `.env`, fill in the values, and just run:

```bash
bash scripts/trials4patients.sh
```

---

## Environment variables

| Var | Default | Description |
|---|---|---|
| `TRIALS_DATABASE_URL` | required | Remote trials PostgreSQL database |
| `PATIENT_DATABASE_URL` | required | Patient PostgreSQL database |
| `PERSON_IDS` | all | Comma-separated person IDs to process |
| `PATIENT_LIMIT` | all | Max number of patients to process |
| `SEARCH_LIMIT` | `20` | Top N trials returned per patient |
| `RESULTS_CSV` | — | If set, also writes results in evaluator CSV format to this path |

---

## Output

Always writes a full JSON results file to `/tmp/exact_local_test_results.json`.

If `RESULTS_CSV` is set, also writes an evaluator-compatible CSV:

```
CTOMOP Patient ID,Trial,Eligible/Potential,Suitability Score
20291,NCT03452774,potential,81
20291,NCT07038785,eligible,79
```

This CSV can be passed directly to the evaluator — see [evaluator.md](evaluator.md).

### Output structure

For each patient, the JSON output contains a summary object with `eligible_trials` and `potential_trials` lists. Each trial entry contains:

| Field | Type | Description |
|-------|------|-------------|
| `studyId` | string | NCT number or trial identifier |
| `briefTitle` | string | Short trial title |
| `officialTitle` | string | Full official title |
| `matchingType` | string | `eligible`, `potential`, or `not_eligible` |
| `matchScore` | int | 0–100; % of eligibility attributes met (0 if any attribute is not matched) |
| `goodnessScore` | int | 0–100 composite score weighted by benefit, patient burden, risk, and distance |
| `recruitmentStatus` | string | e.g. `RECRUITING` |
| `phase` | list | Trial phases, e.g. `["Phase 2"]` |
| `studyType` | string | e.g. `Interventional` |
| `sponsor` | string | Sponsoring organization |
| `link` | string | URL to trial details |
| `disease` | string | Disease/condition under study |
| `register` | string | Source registry, e.g. `ClinicalTrials.gov` |

**`matchingType`** is derived from per-attribute eligibility checks: `eligible` means all required attributes match; `potential` means some attributes are unknown (patient could still qualify); `not_eligible` means at least one required attribute is unmet (excluded from results by default).

**`goodnessScore`** is a weighted composite of four components (each defaulting to weight 25): trial benefit, patient burden (inversed), trial risk (inversed), and distance from patient to nearest site (inversed).

---

## Examples

Typical `.env` for running against specific patients and saving evaluator CSV:

```bash
TRIALS_DATABASE_URL=postgresql://user:pass@host:5432/trials
PATIENT_DATABASE_URL=postgresql://user:pass@host:5432/patients

PERSON_IDS=20291,20292,20293
SEARCH_LIMIT=5
RESULTS_CSV=results.csv
```

Then just:

```bash
bash scripts/trials4patients.sh
```

To set `PERSON_IDS` from an ground truth CSV before running:

```bash
PERSON_IDS=$(tail -n +2 scripts/evaluator/ground_truth.csv | cut -d',' -f1 | sort -u | tr '\n' ',' | sed 's/,$//') \
bash scripts/trials4patients.sh
```
