"""
Management command: evaluate_ground_truth

Compare two CSVs in ground truth format — no database, no live matching.

  --ground-truth   ground-truth CSV
  --results   EXACT output CSV (e.g. produced by trials4patients_results.csv)

Both files use the same format:
    CTOMOP Patient ID,Trial,Eligible/Potential,Suitability Score

TOP_N is inferred automatically as the maximum number of results rows for any
single patient in the results CSV.  PENALTY_RANK = TOP_N + 1.

Usage:
    python manage.py evaluate_ground_truth \\
      --ground-truth scripts/evaluator/ground_truth.csv \\
      --results trials4patients_results.csv \\
      --output  comparison.json
"""
import csv
import json
import os

from django.core.management.base import BaseCommand


def _read_csv(path):
    """Parse ground truth CSV. Returns list of row dicts with normalised keys."""
    rows = []
    with open(path, newline='') as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',\t')
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(f, dialect=dialect)
        for lineno, row in enumerate(reader, start=2):
            rows.append({
                '_line':      lineno,
                'person_id':  (row.get('CTOMOP Patient ID') or '').strip(),
                'code':       (row.get('Trial') or '').strip(),
                'match_type': (row.get('Eligible/Potential') or '').strip().lower(),
                'score':      (row.get('Suitability Score') or '').strip(),
            })
    return rows


# ---------------------------------------------------------------------------
# Results CSV → actual_results dicts
# ---------------------------------------------------------------------------

def _load_results(path):
    """
    Parse a results CSV (same format as the ground truth file).  Returns a dict:
        { person_id_str: [ {code, study_id, match_type, goodness_score, rank}, … ] }

    Rows are ranked in the order they appear per patient (first row = rank 1).
    Both 'code' and 'study_id' are set to the Trial column value so that
    _compare_patient can match on either.
    """
    rows = _read_csv(path)
    results: dict[str, list] = {}
    rank_counter: dict[str, int] = {}
    for r in rows:
        pid = r['person_id']
        rank_counter[pid] = rank_counter.get(pid, 0) + 1
        results.setdefault(pid, []).append({
            'code':          r['code'],
            'study_id':      r['code'],   # Trial column may be NCT or internal code
            'match_type':    r['match_type'],
            'goodness_score': float(r['score']),
            'rank':           rank_counter[pid],
        })
    return results


# ---------------------------------------------------------------------------
# Comparison (mirrors evaluate_ground_truth_live but penalty_rank is a parameter)
# ---------------------------------------------------------------------------

def _compare_patient(person_id, expected_rows, actual_results, penalty_rank):
    actual_by_code = {}
    for r in actual_results:
        actual_by_code[r['code']] = r
        if r.get('study_id') and r['study_id'] != r['code']:
            actual_by_code[r['study_id']] = r

    trials = []
    score_abs_errors = []
    score_signed_errors = []
    reciprocal_ranks = []

    for exp in expected_rows:
        code = exp['code']
        act = actual_by_code.get(code)
        found = act is not None
        exp_score = float(exp['score'])
        act_score = act['goodness_score'] if act else None
        act_rank  = act['rank'] if act else penalty_rank
        act_type  = act['match_type'] if act else None
        score_delta = (act_score - exp_score) if act_score is not None else None

        if score_delta is not None:
            score_abs_errors.append(abs(score_delta))
            score_signed_errors.append(score_delta)
        reciprocal_ranks.append(1.0 / act_rank)

        trials.append({
            'code':           code,
            'found':          found,
            'expected_type':  exp['match_type'],
            'actual_type':    act_type,
            'type_match':     act_type == exp['match_type'] if found else False,
            'expected_score': exp_score,
            'actual_score':   act_score,
            'score_delta':    score_delta,
            'rank':           act_rank,
        })

    tp = sum(1 for t in trials if t['found'])
    fn = len(trials) - tp
    fp = len(actual_results) - tp

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1        = (2 * precision * recall / (precision + recall)
                 if (precision + recall) > 0 else 0.0)

    type_matches  = sum(1 for t in trials if t['found'] and t['type_match'])
    score_matches = sum(1 for t in trials if t['found'] and t['score_delta'] == 0)
    type_total    = tp

    return {
        'person_id':        int(person_id),
        'expected_count':   len(expected_rows),
        'actual_count':     len(actual_results),
        'tp': tp, 'fp': fp, 'fn': fn,
        'precision':        round(precision, 4),
        'recall':           round(recall, 4),
        'f1':               round(f1, 4),
        'type_match_rate':  round(type_matches  / type_total, 4) if type_total else None,
        'score_match_rate': round(score_matches / type_total, 4) if type_total else None,
        'score_mae':        round(sum(score_abs_errors) / len(score_abs_errors), 2)
                            if score_abs_errors else None,
        'score_bias':       round(sum(score_signed_errors) / len(score_signed_errors), 2)
                            if score_signed_errors else None,
        'mrr':              round(sum(reciprocal_ranks) / len(reciprocal_ranks), 4)
                            if reciprocal_ranks else None,
        'avg_rank':         round(sum(t['rank'] for t in trials) / len(trials), 2)
                            if trials else None,
        'trials':           trials,
    }


def _aggregate(patient_results, penalty_rank):
    total_tp = sum(p['tp'] for p in patient_results)
    total_fp = sum(p['fp'] for p in patient_results)
    total_fn = sum(p['fn'] for p in patient_results)

    precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
    recall    = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
    f1        = (2 * precision * recall / (precision + recall)
                 if (precision + recall) > 0 else 0.0)

    all_trials = [t for p in patient_results for t in p['trials']]
    found      = [t for t in all_trials if t['found']]
    scored     = [t for t in found if t['score_delta'] is not None]

    type_match_rate  = (sum(1 for t in found if t['type_match']) / len(found)
                        if found else None)
    score_match_rate = (sum(1 for t in found if t['score_delta'] == 0) / len(found)
                        if found else None)
    score_mae  = (sum(abs(t['score_delta']) for t in scored) / len(scored)
                  if scored else None)
    score_bias = (sum(t['score_delta'] for t in scored) / len(scored)
                  if scored else None)

    all_rr  = [1.0 / t['rank'] for t in all_trials]
    all_rnk = [t['rank'] for t in all_trials]

    return {
        'patients':         len(patient_results),
        'expected_trials':  sum(p['expected_count'] for p in patient_results),
        'top_n':            penalty_rank - 1,
        'penalty_rank':     penalty_rank,
        'tp': total_tp, 'fp': total_fp, 'fn': total_fn,
        'precision':        round(precision, 4),
        'recall':           round(recall, 4),
        'f1':               round(f1, 4),
        'type_match_rate':  round(type_match_rate,  4) if type_match_rate  is not None else None,
        'score_match_rate': round(score_match_rate, 4) if score_match_rate is not None else None,
        'score_mae':        round(score_mae, 2)  if score_mae  is not None else None,
        'score_bias':       round(score_bias, 2) if score_bias is not None else None,
        'mrr':              round(sum(all_rr) / len(all_rr), 4)   if all_rr  else None,
        'avg_rank':         round(sum(all_rnk) / len(all_rnk), 2) if all_rnk else None,
    }


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = 'Compare two ground truth format CSVs (ground truth vs EXACT results).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--ground-truth',
            required=True,
            help='Ground-truth CSV.',
        )
        parser.add_argument(
            '--results',
            required=True,
            help='EXACT results CSV (same format; e.g. trials4patients_results.csv).',
        )
        parser.add_argument(
            '--output',
            default='',
            help='Write full JSON comparison to this file.',
        )

    def handle(self, *args, **options):
        # ── Read CSVs ─────────────────────────────────────────────────────
        self.stdout.write(f'Ground truth: {options["ground_truth"]}')
        self.stdout.write(f'Results : {options["results"]}')

        try:
            ground_truth_rows = _read_csv(options['ground_truth'])
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Failed to read ground_truth: {e}'))
            return

        try:
            results_by_patient = _load_results(options['results'])
        except Exception as e:
            self.stderr.write(self.style.ERROR(f'Failed to read results: {e}'))
            return

        # ── Infer TOP_N from results ───────────────────────────────────────
        top_n = max((len(v) for v in results_by_patient.values()), default=0)
        if top_n == 0:
            self.stderr.write(self.style.ERROR('Results CSV is empty.'))
            return
        penalty_rank = top_n + 1

        self.stdout.write(
            f'  ground_truth: {len(ground_truth_rows)} rows, '
            f'{len({r["person_id"] for r in ground_truth_rows})} patients\n'
            f'  results: {sum(len(v) for v in results_by_patient.values())} rows, '
            f'{len(results_by_patient)} patients  (top_n={top_n})'
        )

        # ── Group ground truth by patient ───────────────────────────────────────
        from collections import defaultdict
        expected_by_patient: dict = defaultdict(list)
        for r in ground_truth_rows:
            expected_by_patient[r['person_id']].append(r)

        # ── Compare ───────────────────────────────────────────────────────
        patient_results = []
        all_person_ids = sorted(
            expected_by_patient.keys(),
            key=lambda x: int(x),
        )

        for i, person_id in enumerate(all_person_ids, 1):
            actual = results_by_patient.get(person_id, [])
            result = _compare_patient(
                person_id,
                expected_by_patient[person_id],
                actual,
                penalty_rank,
            )
            patient_results.append(result)

            type_str  = (f'| type_match={result["type_match_rate"]:.0%} '
                         if result['type_match_rate']  is not None else '')
            score_str = (f'| score_match={result["score_match_rate"]:.0%} '
                         if result['score_match_rate'] is not None else '')
            self.stdout.write(
                f'  [{i}/{len(all_person_ids)}] person_id={person_id} '
                f'| recall={result["recall"]:.0%} '
                f'| precision={result["precision"]:.0%} '
                f'{type_str}{score_str}'
            )

        if not patient_results:
            self.stderr.write(self.style.ERROR('No results to aggregate.'))
            return

        # ── Aggregate ─────────────────────────────────────────────────────
        summary = _aggregate(patient_results, penalty_rank)

        self.stdout.write('\n' + '=' * 60)
        self.stdout.write('SUMMARY')
        self.stdout.write('=' * 60)
        self.stdout.write(f'  Patients          : {summary["patients"]}')
        self.stdout.write(f'  Expected trials   : {summary["expected_trials"]}')
        self.stdout.write(f'  Top N             : {summary["top_n"]}')
        self.stdout.write(f'  TP / FP / FN      : {summary["tp"]} / {summary["fp"]} / {summary["fn"]}')
        self.stdout.write(f'  Precision         : {summary["precision"]:.1%}')
        self.stdout.write(f'  Recall            : {summary["recall"]:.1%}')
        self.stdout.write(f'  F1                : {summary["f1"]:.1%}')
        if summary['type_match_rate'] is not None:
            self.stdout.write(f'  Type match rate   : {summary["type_match_rate"]:.1%}')
        if summary['score_match_rate'] is not None:
            self.stdout.write(f'  Score match rate  : {summary["score_match_rate"]:.1%}')
        if summary['score_mae'] is not None:
            self.stdout.write(f'  Score MAE         : {summary["score_mae"]:.2f}')
        if summary['score_bias'] is not None:
            self.stdout.write(f'  Score bias        : {summary["score_bias"]:+.2f}')
        if summary['mrr'] is not None:
            self.stdout.write(f'  MRR               : {summary["mrr"]:.4f}')
        if summary['avg_rank'] is not None:
            self.stdout.write(
                f'  Avg rank          : {summary["avg_rank"]:.2f}'
                f'  (penalty={penalty_rank} if not found)'
            )
        self.stdout.write('=' * 60)

        # ── Write output ──────────────────────────────────────────────────
        if options['output']:
            output = {'summary': summary, 'patients': patient_results}
            with open(options['output'], 'w') as f:
                json.dump(output, f, indent=2, default=str)
            self.stdout.write(self.style.SUCCESS(f'\nComparison written to: {options["output"]}'))
