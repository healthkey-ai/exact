"""Integration tests for `POST /normalize-ctomop-row/`.

The view is a thin authenticated wrapper around
`normalize_ctomop_row` from `trials.services.patient_info.ctomop_adapter`.
The adapter itself has exhaustive coverage in
`tests/management/test_normalize_ctomop_row.py`; the tests here exist
to lock the HTTP surface — auth, payload shape, response shape — so
the federation harness (added in #117) can rely on it.
"""
from unittest.mock import MagicMock, patch

import pytest

from trials.api.trials_views import NormalizeCtomopRowView


def _post(body):
    """Drive `NormalizeCtomopRowView.post` directly with a mock request."""
    view = NormalizeCtomopRowView()
    mock_request = MagicMock()
    mock_request.data = body
    return view.post(mock_request)


# Mock the DB-backed code lookup so the test doesn't need fixtures —
# the adapter's own unit tests already prove the lookup wiring.
_MOCK_LOOKUP = {
    'Her2Status': {
        'her2-': 'her2_minus',
        'her2+': 'her2_plus',
        'positive': 'her2_plus',
        'negative': 'her2_minus',
        'equivocal': 'her2_low',
    },
    'EstrogenReceptorStatus': {
        'positive': 'er_plus_with_hi_exp',
        'negative': 'er_minus',
        'borderline': 'er_plus_with_low_exp',
    },
    'ProgesteroneReceptorStatus': {},
    'HrStatus': {},
    'HrdStatus': {},
    'HistologicType': {},
    'Ethnicity': {},
    'Marker': {},
    'PlannedTherapy': {},
    'ConcomitantMedication': {},
    '_therapy': {},
}


@patch(
    'trials.services.patient_info.ctomop_adapter._build_code_lookup',
    return_value=_MOCK_LOOKUP,
)
class TestNormalizeCtomopRowView:
    def test_returns_normalized_payload(self, _mock_lookup):
        """Headline case: receptor display string is resolved to a code."""
        response = _post({
            'her2_status': 'Equivocal',
            'estrogen_receptor_status': 'Positive',
            'disease': 'Multiple Myeloma',
        })
        assert response.status_code == 200
        # `equivocal` → `her2_low`; `Positive` → `er_plus_with_hi_exp`.
        assert response.data['her2_status'] == 'her2_low'
        assert response.data['estrogen_receptor_status'] == 'er_plus_with_hi_exp'
        # Non-normalized fields pass through untouched.
        assert response.data['disease'] == 'Multiple Myeloma'

    def test_stage_letter_stripped(self, _mock_lookup):
        """`stage` is canonical CT-stage stripping — 'IIA' → 'II'."""
        response = _post({'stage': 'IIIB'})
        assert response.data['stage'] == 'III'

    def test_tumor_grade_int_to_code(self, _mock_lookup):
        """CTOMOP grade 1/2/3 → EXACT '10'/'20'/'30'."""
        response = _post({'tumor_grade': 2})
        assert response.data['tumor_grade'] == '20'

    def test_metastasis_status_inverted_to_metastatic_status(self, _mock_lookup):
        response = _post({'metastasis_status': 'Positive'})
        assert response.data['metastatic_status'] is True

    def test_prior_therapy_from_lines_count(self, _mock_lookup):
        response = _post({'therapy_lines_count': 2})
        assert response.data['prior_therapy'] == 'Two lines'

    def test_empty_body_returns_empty_normalized_dict(self, _mock_lookup):
        """An empty payload normalizes to an empty payload — no side effects."""
        response = _post({})
        assert response.status_code == 200
        assert response.data == {}

    def test_non_dict_body_returns_400(self, _mock_lookup):
        for bad in ([1, 2, 3], "string body", 42, None):
            response = _post(bad)
            assert response.status_code == 400
            assert 'Body must be a JSON object' in response.data['detail']

    def test_caller_dict_is_not_mutated(self, _mock_lookup):
        """The view copies the payload before normalizing — important so
        callers can reuse the dict after the request."""
        original = {'her2_status': 'Equivocal', 'stage': 'IIIB'}
        snapshot = dict(original)
        _post(original)
        assert original == snapshot
