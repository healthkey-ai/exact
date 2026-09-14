# EXACT — Clinical Trial Matching Engine

EXACT (EXtracting Attributes from Clinical Trials) is a stateless search and
matching engine for clinical trials. It connects to an external database that
holds the trial catalog and reference data — EXACT does not own or manage that
data.

Patient profiles are passed inline with each API request. They can also be retrieved from an external database that has a PatientInfo table in the form implemented by [CTOMOP](https://github.com/healthkey-ai/ctomop), which exposes a flat projection of a patient record from underlying OMOP tables.

> **[API_SURFACE.md](API_SURFACE.md)** — full REST API reference (endpoints, patient context schema, query params, response shapes).

## Documentation

| Doc | Description |
|-----|-------------|
| [docs/overview.md](docs/overview.md) | Architecture, key components, data flow |
| [docs/trials4patients.md](docs/trials4patients.md) | Running trial search for patients |
| [docs/evaluator.md](docs/evaluator.md) | Evaluating EXACT results against ground truth |
| [docs/setup.md](docs/setup.md) | Local development setup |
| [API_SURFACE.md](API_SURFACE.md) | REST API reference |

## Running trial search for patients

To match patients from an external patient database against the trial catalog
without a running web server:

```bash
export TRIALS_DATABASE_URL=postgresql://...
export PATIENT_DATABASE_URL=postgresql://...

bash scripts/trials4patients.sh
```

See [docs/trials4patients.md](docs/trials4patients.md) for all options and output structure.

## Evaluating results

To score EXACT results against a ground-truth CSV:

```bash
RESULTS_CSV=results.csv bash scripts/trials4patients.sh
bash scripts/evaluator/evaluate.sh scripts/evaluator/ground_truth.csv results.csv
```

See [docs/evaluator.md](docs/evaluator.md) for metrics, full workflow, and output options.

## Quick start (connecting to an existing trials database)

```bash
pip install -r requirements.txt

# Local DB for auth/tokens only
python manage.py migrate

# Point to the external trials database
export TRIALS_DATABASE_URL=postgresql://readonly:secret@trials-db.example.com:5432/trials

python manage.py runserver
```

## Quick start (standalone / local development)

When no external trials database is configured, EXACT falls back to a single
local database for everything — useful for development and testing:

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_reference_data
python manage.py runserver
```

Once the server is running, search for trials with curl:

```bash
curl -s -H "Authorization: Token <your-token>" \
  'http://localhost:8000/trials/?type=eligible' \
  -H 'Content-Type: application/json' \
  -d '{"patientInfo": {"disease": "multiple myeloma", "patientAge": 55, "gender": "M"}}' | python -m json.tool
```

See [docs/setup.md](docs/setup.md) for full setup instructions, environment
variables, secrets management, and test configuration.
