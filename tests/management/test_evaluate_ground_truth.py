"""
Tests for evaluate_ground_truth management command.

Covers:
- _load_results()      — results CSV → per-patient actual dicts with inferred ranks
- _compare_patient()   — same metrics as evaluate_ground_truth_live but with dynamic penalty_rank
- _aggregate()         — summary includes inferred top_n / penalty_rank
- TOP_N inference      — max rows per patient in results CSV
"""
import textwrap

import pytest

from trials.management.commands.evaluate_ground_truth import (
    _aggregate,
    _compare_patient,
    _load_results,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_csv(tmp_path, content, name='data.csv'):
    p = tmp_path / name
    p.write_text(textwrap.dedent(content))
    return str(p)


def _ground_truth_rows(*entries):
    """Build minimal ground truth row dicts from (person_id, code, match_type, score) tuples."""
    return [
        {'_line': i + 2, 'person_id': str(pid), 'code': code,
         'match_type': mt, 'score': str(score)}
        for i, (pid, code, mt, score) in enumerate(entries)
    ]


# ---------------------------------------------------------------------------
# _load_results
# ---------------------------------------------------------------------------

class TestLoadResults:
    def test_basic_parsing(self, tmp_path):
        path = _write_csv(tmp_path, """\
            CTOMOP Patient ID,Trial,Eligible/Potential,Suitability Score
            1001,NCT00000001,eligible,85
            1001,NCT00000002,potential,70
        """)
        r = _load_results(path)
        assert '1001' in r
        assert len(r['1001']) == 2
        assert r['1001'][0]['code'] == 'NCT00000001'
        assert r['1001'][0]['goodness_score'] == 85.0
        assert r['1001'][0]['match_type'] == 'eligible'

    def test_ranks_assigned_per_patient_in_order(self, tmp_path):
        path = _write_csv(tmp_path, """\
            CTOMOP Patient ID,Trial,Eligible/Potential,Suitability Score
            1001,NCT00000001,eligible,85
            1001,NCT00000002,potential,70
            1001,NCT00000003,potential,60
        """)
        r = _load_results(path)
        ranks = [row['rank'] for row in r['1001']]
        assert ranks == [1, 2, 3]

    def test_ranks_reset_per_patient(self, tmp_path):
        path = _write_csv(tmp_path, """\
            CTOMOP Patient ID,Trial,Eligible/Potential,Suitability Score
            1001,NCT00000001,eligible,85
            1001,NCT00000002,potential,70
            1002,NCT00000003,eligible,90
        """)
        r = _load_results(path)
        assert r['1001'][0]['rank'] == 1
        assert r['1001'][1]['rank'] == 2
        assert r['1002'][0]['rank'] == 1

    def test_code_and_study_id_both_set_to_trial_column(self, tmp_path):
        path = _write_csv(tmp_path, """\
            CTOMOP Patient ID,Trial,Eligible/Potential,Suitability Score
            1001,NCT03452774,potential,81
        """)
        r = _load_results(path)
        row = r['1001'][0]
        assert row['code'] == 'NCT03452774'
        assert row['study_id'] == 'NCT03452774'

    def test_multiple_patients(self, tmp_path):
        path = _write_csv(tmp_path, """\
            CTOMOP Patient ID,Trial,Eligible/Potential,Suitability Score
            1001,NCT00000001,eligible,85
            1002,NCT00000002,potential,70
            1003,NCT00000003,eligible,65
        """)
        r = _load_results(path)
        assert set(r.keys()) == {'1001', '1002', '1003'}


# ---------------------------------------------------------------------------
# TOP_N inference
# ---------------------------------------------------------------------------

class TestTopNInference:
    def test_top_n_is_max_rows_per_patient(self, tmp_path):
        path = _write_csv(tmp_path, """\
            CTOMOP Patient ID,Trial,Eligible/Potential,Suitability Score
            1001,NCT00000001,eligible,85
            1001,NCT00000002,potential,70
            1001,NCT00000003,potential,60
            1002,NCT00000001,eligible,80
            1002,NCT00000002,potential,65
        """)
        r = _load_results(path)
        top_n = max(len(v) for v in r.values())
        assert top_n == 3   # patient 1001 has 3 rows

    def test_single_result_per_patient(self, tmp_path):
        path = _write_csv(tmp_path, """\
            CTOMOP Patient ID,Trial,Eligible/Potential,Suitability Score
            1001,NCT00000001,eligible,85
            1002,NCT00000002,potential,70
        """)
        r = _load_results(path)
        top_n = max(len(v) for v in r.values())
        assert top_n == 1


# ---------------------------------------------------------------------------
# _compare_patient (dynamic penalty_rank)
# ---------------------------------------------------------------------------

class TestComparePatient:
    def test_all_found(self):
        expected = _ground_truth_rows((99, 'NCT001', 'eligible', 80), (99, 'NCT002', 'potential', 70))
        actual = [
            {'code': 'NCT001', 'study_id': 'NCT001', 'match_type': 'eligible',  'goodness_score': 80.0, 'rank': 1},
            {'code': 'NCT002', 'study_id': 'NCT002', 'match_type': 'potential', 'goodness_score': 70.0, 'rank': 2},
        ]
        r = _compare_patient('99', expected, actual, penalty_rank=6)
        assert r['tp'] == 2
        assert r['recall'] == 1.0
        assert r['precision'] == 1.0

    def test_none_found_uses_penalty_rank(self):
        expected = _ground_truth_rows((99, 'NCT001', 'eligible', 80))
        r = _compare_patient('99', expected, [], penalty_rank=6)
        assert r['tp'] == 0
        assert r['recall'] == 0.0
        assert r['trials'][0]['rank'] == 6

    def test_mrr_uses_penalty_rank(self):
        expected = _ground_truth_rows((99, 'NCT001', 'eligible', 80))
        r = _compare_patient('99', expected, [], penalty_rank=6)
        assert r['mrr'] == pytest.approx(1.0 / 6, abs=1e-4)

    def test_type_match_rate(self):
        expected = _ground_truth_rows(
            (99, 'NCT001', 'eligible', 80),
            (99, 'NCT002', 'eligible', 70),
        )
        actual = [
            {'code': 'NCT001', 'study_id': 'NCT001', 'match_type': 'eligible',  'goodness_score': 80.0, 'rank': 1},
            {'code': 'NCT002', 'study_id': 'NCT002', 'match_type': 'potential', 'goodness_score': 70.0, 'rank': 2},
        ]
        r = _compare_patient('99', expected, actual, penalty_rank=6)
        assert r['type_match_rate'] == pytest.approx(0.5)

    def test_score_match_rate_exact(self):
        expected = _ground_truth_rows((99, 'NCT001', 'eligible', 80))
        actual = [{'code': 'NCT001', 'study_id': 'NCT001', 'match_type': 'eligible', 'goodness_score': 80.0, 'rank': 1}]
        r = _compare_patient('99', expected, actual, penalty_rank=6)
        assert r['score_match_rate'] == 1.0

    def test_score_match_rate_off_by_one(self):
        expected = _ground_truth_rows((99, 'NCT001', 'eligible', 80))
        actual = [{'code': 'NCT001', 'study_id': 'NCT001', 'match_type': 'eligible', 'goodness_score': 81.0, 'rank': 1}]
        r = _compare_patient('99', expected, actual, penalty_rank=6)
        assert r['score_match_rate'] == 0.0

    def test_fp_count(self):
        # 3 actual results, 1 expected — 1 TP, 2 FP
        expected = _ground_truth_rows((99, 'NCT001', 'eligible', 80))
        actual = [
            {'code': 'NCT001', 'study_id': 'NCT001', 'match_type': 'eligible', 'goodness_score': 80.0, 'rank': 1},
            {'code': 'NCT002', 'study_id': 'NCT002', 'match_type': 'potential', 'goodness_score': 75.0, 'rank': 2},
            {'code': 'NCT003', 'study_id': 'NCT003', 'match_type': 'potential', 'goodness_score': 70.0, 'rank': 3},
        ]
        r = _compare_patient('99', expected, actual, penalty_rank=4)
        assert r['tp'] == 1
        assert r['fp'] == 2

    def test_score_mae_and_bias(self):
        # NCT001: actual 90 vs expected 80 (+10), NCT002: actual 65 vs expected 70 (-5)
        expected = _ground_truth_rows((99, 'NCT001', 'eligible', 80), (99, 'NCT002', 'potential', 70))
        actual = [
            {'code': 'NCT001', 'study_id': 'NCT001', 'match_type': 'eligible',  'goodness_score': 90.0, 'rank': 1},
            {'code': 'NCT002', 'study_id': 'NCT002', 'match_type': 'potential', 'goodness_score': 65.0, 'rank': 2},
        ]
        r = _compare_patient('99', expected, actual, penalty_rank=6)
        assert r['score_mae']  == pytest.approx(7.5)   # (10 + 5) / 2
        assert r['score_bias'] == pytest.approx(2.5)   # (10 - 5) / 2

    def test_score_mae_none_when_no_found(self):
        expected = _ground_truth_rows((99, 'NCT001', 'eligible', 80))
        r = _compare_patient('99', expected, [], penalty_rank=6)
        assert r['score_mae'] is None
        assert r['score_bias'] is None

    def test_partial_match_with_fp_and_score_diff(self):
        # 1 found with wrong score, 1 missing, 1 FP — mirrors patient 2002 in synthetic CSV
        expected = _ground_truth_rows(
            (99, 'NCT001', 'eligible', 90),
            (99, 'NCT002', 'potential', 80),
            (99, 'NCT003', 'potential', 70),
        )
        actual = [
            {'code': 'NCT001', 'study_id': 'NCT001', 'match_type': 'eligible',  'goodness_score': 90.0, 'rank': 1},
            {'code': 'NCT002', 'study_id': 'NCT002', 'match_type': 'potential', 'goodness_score': 72.0, 'rank': 2},
            {'code': 'NCT099', 'study_id': 'NCT099', 'match_type': 'potential', 'goodness_score': 68.0, 'rank': 3},
        ]
        r = _compare_patient('99', expected, actual, penalty_rank=4)
        assert r['tp'] == 2
        assert r['fp'] == 1
        assert r['fn'] == 1
        assert r['recall']          == pytest.approx(2 / 3, abs=1e-4)
        assert r['precision']       == pytest.approx(2 / 3, abs=1e-4)
        assert r['score_match_rate'] == pytest.approx(0.5)   # NCT001 exact, NCT002 off
        assert r['score_mae']        == pytest.approx(4.0)   # |90-90| + |72-80| / 2

    def test_patient_absent_from_results(self):
        # Patient has expected trials but results CSV had no rows for them
        expected = _ground_truth_rows(
            (99, 'NCT001', 'eligible', 85),
            (99, 'NCT002', 'potential', 75),
        )
        r = _compare_patient('99', expected, [], penalty_rank=6)
        assert r['tp'] == 0
        assert r['fn'] == 2
        assert r['fp'] == 0
        assert r['recall'] == 0.0
        assert r['precision'] == 0.0
        assert all(t['rank'] == 6 for t in r['trials'])
        assert r['mrr'] == pytest.approx(1.0 / 6, abs=1e-4)


# ---------------------------------------------------------------------------
# _aggregate includes top_n / penalty_rank in summary
# ---------------------------------------------------------------------------

class TestAggregate:
    def _patient(self, tp, fp, fn, found_trials=None):
        trials = found_trials or []
        return {
            'tp': tp, 'fp': fp, 'fn': fn,
            'expected_count': tp + fn, 'actual_count': tp + fp,
            'trials': trials,
        }

    def test_top_n_and_penalty_rank_in_summary(self):
        p = self._patient(1, 0, 0, [
            {'found': True, 'type_match': True, 'score_delta': 0.0, 'rank': 1}
        ])
        s = _aggregate([p], penalty_rank=6)
        assert s['top_n'] == 5
        assert s['penalty_rank'] == 6

    def test_micro_precision_recall(self):
        a = self._patient(2, 0, 1, [
            {'found': True,  'type_match': True, 'score_delta': 0.0,  'rank': 1},
            {'found': True,  'type_match': True, 'score_delta': 0.0,  'rank': 2},
            {'found': False, 'type_match': False, 'score_delta': None, 'rank': 6},
        ])
        b = self._patient(1, 1, 0, [
            {'found': True, 'type_match': True, 'score_delta': 2.0, 'rank': 1},
        ])
        s = _aggregate([a, b], penalty_rank=6)
        assert s['tp'] == 3
        assert s['fp'] == 1
        assert s['fn'] == 1
        assert s['precision'] == pytest.approx(3 / 4)
        assert s['recall'] == pytest.approx(3 / 4)

    def test_aggregate_type_and_score_match_rate(self):
        # 3 found: 2 type-match, 1 exact score
        p = self._patient(3, 0, 0, [
            {'found': True, 'type_match': True,  'score_delta': 0.0,  'rank': 1},
            {'found': True, 'type_match': True,  'score_delta': 5.0,  'rank': 2},
            {'found': True, 'type_match': False, 'score_delta': -3.0, 'rank': 3},
        ])
        s = _aggregate([p], penalty_rank=4)
        assert s['type_match_rate']  == pytest.approx(2 / 3, abs=1e-4)
        assert s['score_match_rate'] == pytest.approx(1 / 3, abs=1e-4)

    def test_aggregate_mae_and_bias(self):
        # score_deltas: +10, -4  → MAE=7, bias=+3
        p = self._patient(2, 0, 0, [
            {'found': True, 'type_match': True, 'score_delta': 10.0, 'rank': 1},
            {'found': True, 'type_match': True, 'score_delta': -4.0, 'rank': 2},
        ])
        s = _aggregate([p], penalty_rank=3)
        assert s['score_mae']  == pytest.approx(7.0)
        assert s['score_bias'] == pytest.approx(3.0)

    def test_aggregate_mrr_and_avg_rank(self):
        # found at rank 1 and rank 3; missing gets penalty_rank=4
        p = self._patient(2, 0, 1, [
            {'found': True,  'type_match': True, 'score_delta': 0.0,  'rank': 1},
            {'found': True,  'type_match': True, 'score_delta': 0.0,  'rank': 3},
            {'found': False, 'type_match': False, 'score_delta': None, 'rank': 4},
        ])
        s = _aggregate([p], penalty_rank=4)
        assert s['mrr']      == pytest.approx((1/1 + 1/3 + 1/4) / 3, abs=1e-4)
        assert s['avg_rank'] == pytest.approx((1 + 3 + 4) / 3, abs=1e-2)

    def test_aggregate_mae_none_when_no_found(self):
        p = self._patient(0, 0, 2, [
            {'found': False, 'type_match': False, 'score_delta': None, 'rank': 4},
            {'found': False, 'type_match': False, 'score_delta': None, 'rank': 4},
        ])
        s = _aggregate([p], penalty_rank=4)
        assert s['score_mae']        is None
        assert s['score_bias']       is None
        assert s['type_match_rate']  is None
        assert s['score_match_rate'] is None
