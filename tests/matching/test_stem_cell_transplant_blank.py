"""An empty SCT history is unanswered, not "no transplant" (#5435).

CB's `PatientInfoAttributes.is_attr_blank` gained this rule in #5435: `[]` and
`''` mean the patient was not asked, while `['None']` is an explicit "no SCT"
answer. The packaged copy only special-cased the other JSON/list attributes, so
the SQL orchestrator applied the SCT filter with an empty value and the matcher
read it as answered. This pins the shared primitive before the queryset drain.
"""
import pytest

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes


def _attributes(**fields):
    return PatientInfoAttributes(PatientInfo(disease='multiple myeloma', **fields))


@pytest.mark.parametrize('value, blank', [
    ([], True),
    ('', True),
    (None, True),
    # An explicit no-SCT answer is an answer.
    (['None'], False),
    (['Autologous'], False),
])
def test_stem_cell_transplant_history_blankness(value, blank):
    assert _attributes(stem_cell_transplant_history=value).is_attr_blank(
        'stem_cell_transplant_history') is blank


@pytest.mark.parametrize('attribute', [
    'genetic_mutations', 'supportive_therapies', 'later_therapies',
])
@pytest.mark.parametrize('value', [[], ''])
def test_the_other_list_valued_attributes_keep_the_same_empty_rule(attribute, value):
    assert _attributes(**{attribute: value}).is_attr_blank(attribute) is True


def test_explicit_no_sct_strings_are_answers():
    # CB stores an explicit "no SCT" as the string 'None' in the legacy
    # normalization; the canonical transplant code is answered too.
    for value in ('None', ['completedASCT']):
        assert _attributes(stem_cell_transplant_history=value).is_attr_blank(
            'stem_cell_transplant_history') is False


@pytest.mark.django_db
@pytest.mark.parametrize('disease', ['multiple myeloma', 'follicular lymphoma'])
@pytest.mark.parametrize('history, required, excluded, expected', [
    # Not asked: an SCT requirement is unknown, and an exclusion cannot be
    # confirmed from the absence of an answer.
    ([], True, None, 'unknown'),
    ([], None, ['Autologous'], 'unknown'),
    ([], None, None, 'matched'),
    # Explicit "no SCT".
    (['None'], True, None, 'not_matched'),
    (['None'], None, ['Autologous'], 'matched'),
    # A transplant the trial names.
    (['Autologous'], True, None, 'matched'),
    (['Autologous'], None, ['Autologous'], 'not_matched'),
])
def test_sct_verdicts_across_required_excluded_and_unconstrained(
        disease, history, required, excluded, expected):
    from exact_matching.matcher import UserToTrialAttrMatcher
    from trials.models import Trial
    from tests.factories import TrialFactory

    patient = PatientInfo(disease=disease, stem_cell_transplant_history=history)
    trial = TrialFactory(
        disease=disease,
        stem_cell_transplant_history_required=required,
        stem_cell_transplant_history_excluded=excluded or [],
    )

    assert UserToTrialAttrMatcher(trial, patient).attr_match_status(
        'stem_cell_transplant_history') == expected

    kept = Trial.objects.filter(pk=trial.pk).eligible_for_stem_cell_transplant_history(
        patient.stem_cell_transplant_history).exists()
    # The SQL filter keeps an unknown/unconstrained trial and drops a definite
    # contradiction; it never turns an empty history into a negative answer.
    assert kept is (expected != 'not_matched')
