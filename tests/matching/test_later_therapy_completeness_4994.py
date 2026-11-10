"""#4994 (EXACT #402): the 'More than two lines of therapy' completeness check read the raw
`later_therapies` column, so a date-only row (`[{'therapy': None, 'date': ...}]` — what the v1 UI
stores after the first of its two PATCHes) counted as a documented later line and returned
`not_matched` where the line is still incomplete and should be `unknown`. The fix checks
`therapy_codes(...)` (a named therapy), not just a non-empty list.

Ported from CancerBot `trials/tests/services/test_matcher_later_therapy_completeness_4994.py`
(cancerbot-org/cancerbot#5155), adapted to EXACT's in-memory `PatientInfo`, plus the `None` case:
EXACT's PROMOP adapter sends `later_therapies = None`.
"""
import pytest

from exact_matching.patient_info.therapy_rows import therapy_codes
from tests.factories import TrialFactory
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher

pytestmark = pytest.mark.django_db


def _status(later_therapies):
    patient = PatientInfo(
        disease='multiple myeloma',
        prior_therapy='More than two lines of therapy',
        first_line_therapy='FL-A',
        second_line_therapy='SL-B',
        later_therapy='LL-C',
        later_therapies=later_therapies,
    )
    return UserToTrialAttrMatcher(TrialFactory(), patient).therapy_related_things_mismatch_status()


class TestLaterTherapyCompleteness:
    def test_date_only_later_row_is_unknown_not_matched(self):
        # A later line whose row carries only a date names no therapy -> the line is incomplete.
        assert _status([{'therapy': None, 'date': '2024-01-01'}]) == 'unknown'

    def test_named_later_therapy_stays_not_matched(self):
        # Regression guard: a real later therapy is still a documented later line.
        assert _status([{'therapy': 'LL-C', 'date': '2024-01-01'}]) == 'not_matched'

    def test_empty_later_therapies_is_unknown(self):
        assert _status([]) == 'unknown'

    def test_none_later_therapies_is_unknown_not_a_crash(self):
        # EXACT-only: the PROMOP adapter sends `later_therapies = None`.
        assert _status(None) == 'unknown'


class TestTherapyCodes:
    @pytest.mark.parametrize('value, expected', [
        (None, []),
        ([], []),
        ([{'therapy': None, 'date': '2024-01-01'}], []),
        ([{'therapy': 'LL-C', 'date': None}], ['LL-C']),
        (['LL-C'], ['LL-C']),          # a bare code, the legacy row shape
        ('LL-C', ['LL-C']),            # the whole column as a bare string
        ([{'therapy': ['x']}], []),    # not a code -> dropped, not a TypeError
    ])
    def test_shapes(self, value, expected):
        assert therapy_codes(value) == expected
