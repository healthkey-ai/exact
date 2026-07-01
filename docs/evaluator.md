# EXACT — Evaluator

The evaluator works entirely with CSV files — no database connection required.
It takes two CSVs in the same format (ground truth + EXACT results) and
produces per-patient and aggregate metrics.

To get the results CSV from EXACT, use `trials4patients.sh` with `RESULTS_CSV`
set — it runs the matching and writes the output in evaluator-compatible format.
See [Typical end-to-end workflow](#typical-end-to-end-workflow) below.

---

## CSV format

Both the ground-truth and the EXACT results CSV use the same format:

```
CTOMOP Patient ID,Trial,Eligible/Potential,Suitability Score
20291,NCT03452774,potential,81
20291,NCT07038785,eligible,79
```

| Column | Description |
|---|---|
| `CTOMOP Patient ID` | Integer person ID |
| `Trial` | NCT number or internal trial code |
| `Eligible/Potential` | `eligible` or `potential` |
| `Suitability Score` | Integer goodness score (0–100) |

Rows for the same patient must appear consecutively. The order within a patient
determines the trial's rank in the results (first row = rank 1).

---

## Running the evaluator

No database connection required. The script loads `.env` from the project root
if present, but no env vars are needed for CSV-only evaluation.

```bash
bash scripts/evaluator/evaluate.sh scripts/evaluator/ground_truth.csv results.csv
```

With JSON output for deeper analysis:

```bash
bash scripts/evaluator/evaluate.sh \
  scripts/evaluator/ground_truth.csv \
  results.csv \
  --output /tmp/comparison.json
```

`TOP_N` is inferred automatically as the maximum number of result rows for any
single patient in the results CSV. `PENALTY_RANK = TOP_N + 1` is used for
trials that are expected but not found.

---

## Typical end-to-end workflow

**Step 1 — Extract person IDs from the ground truth CSV**

```bash
PERSON_IDS=$(tail -n +2 scripts/evaluator/ground_truth.csv | cut -d',' -f1 | sort -u | tr '\n' ',' | sed 's/,$//')
```

**Step 2 — Run EXACT matching and save results as CSV**

Use `RESULTS_CSV` to make `trials4patients.sh` write an evaluator-compatible
CSV alongside its normal JSON output. See [trials4patients.md](trials4patients.md)
for all available options.

```bash
PERSON_IDS="$PERSON_IDS" \
SEARCH_LIMIT=5 \
RESULTS_CSV=results.csv \
bash scripts/trials4patients.sh
```

**Step 3 — Evaluate results against ground truth**

```bash
bash scripts/evaluator/evaluate.sh scripts/evaluator/ground_truth.csv results.csv
```

**Step 4 — Save full breakdown for further analysis**

```bash
bash scripts/evaluator/evaluate.sh \
  scripts/evaluator/ground_truth.csv results.csv \
  --output /tmp/comparison.json
```

---

## Metrics

All metrics are computed per patient and then micro-averaged across patients.

| Metric | Description |
|---|---|
| `recall` | Fraction of expected trials that appear in EXACT results |
| `precision` | Fraction of EXACT results that are in the expected set |
| `f1` | Harmonic mean of precision and recall |
| `type_match_rate` | Among found trials, fraction where `eligible/potential` matches exactly |
| `score_match_rate` | Among found trials, fraction where the goodness score matches exactly |
| `score_mae` | Mean absolute error of goodness scores (found trials only) |
| `score_bias` | Mean signed error of goodness scores — positive means EXACT scores higher |
| `mrr` | Mean Reciprocal Rank — uses `penalty_rank` for missing trials |
| `avg_rank` | Average rank of expected trials — uses `penalty_rank` for missing |

### Penalty rank

When an expected trial is not found in EXACT's results, its rank is set to
`penalty_rank = top_n + 1`. This affects MRR and avg_rank. A lower `top_n`
makes the penalty harsher — a trial ranked just outside the window is treated
the same as a trial that EXACT doesn't return at all.

### Interpreting low recall

Low recall can have two distinct causes:

1. **Ranking issue** — the expected trial exists in the trials DB but ranks
   below position `top_n`. Increase `SEARCH_LIMIT` to check.
2. **Coverage gap** — the expected trial doesn't exist in the trials DB at all.
   EXACT cannot return what it doesn't have. Verify with:
   ```bash
   python manage.py shell -c "
   from trials.models import Trial
   print(Trial.objects.filter(code='NCT03452774').exists())
   "
   ```

---

## Files

| Path | Description |
|---|---|
| `scripts/evaluator/ground_truth.csv` | Ground-truth trial assignments |
| `scripts/evaluator/evaluate.sh` | CSV evaluation wrapper |
| `scripts/trials4patients.sh` | Runs EXACT matching, produces results CSV — see [trials4patients.md](trials4patients.md) |
| `trials/management/commands/evaluate_ground_truth.py` | CSV evaluation command |
| `tests/management/test_evaluate_ground_truth.py` | Unit tests — CSV evaluator |
