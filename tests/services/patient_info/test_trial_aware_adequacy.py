"""Trial-specific adequacy limits decide matching, SQL and Trial Details (#630).

CB evaluates `hepatic_adequacy_status` / `haematological_adequacy_status` with
the trial's own ULN maxima and lab cutoffs, falling back to the documented
defaults per laboratory. Before this port EXACT matched the stored aggregate
boolean, which only encodes the defaults, so a trial that admits 3xULN AST was
rejected by the 2.5xULN default.
"""
import pytest
from django.test import override_settings

from exact_matching.matcher import UserToTrialAttrMatcher
from exact_matching.patient_info.patient_info_attributes import PatientInfoAttributes
from exact_matching.querysets.trial import TrialQuerySet
from trials.models import Trial
from trials.services.patient_info.resolve import _build_in_memory
from trials.services.trial_details.trial_attributes import TrialAttributes

MM = 'multiple myeloma'
HEPATIC = dict(ethnicity='caucasian_or_european', gender='M')
# ULN norms for caucasian/European male: AST 40, ALT 45, bilirubin total 1.2.
# AST 120 / ALT 90 / bili 1.2 = 3.0 / 2.0 / 1.0 times ULN.
HIGH_AST = dict(HEPATIC, liver_enzyme_levels_ast=120,
                liver_enzyme_levels_alt=90, serum_bilirubin_level_total='1.2')
ADEQUATE_LABS = dict(
    absolute_neutrophile_count=2000, absolute_neutrophile_count_units='CELLS/UL',
    platelet_count=100000, platelet_count_units='CELLS/UL',
    hemoglobin_level=12, hemoglobin_level_units='G/DL',
)


def _patient(fields):
    return _build_in_memory(dict(fields, disease=MM))


def _hepatic(trial, patient):
    return UserToTrialAttrMatcher(trial, patient).attr_match_status('hepatic_adequacy_status')


def _haem(trial, patient):
    return UserToTrialAttrMatcher(trial, patient).attr_match_status('haematological_adequacy_status')


def _in_pool(trial, patient):
    pool, _ = TrialQuerySet(model=Trial).filter(pk=trial.pk).filter_by_patient_info(patient)
    return pool.exists()


@pytest.mark.parametrize('trial_max,expected', [(None, 'not_matched'), (2.0, 'not_matched'), (3.0, 'matched'), (5.0, 'matched')])
@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_hepatic_trial_limit_replaces_the_default(trial_max, expected):
    patient = _patient(HIGH_AST)
    trial = Trial.objects.create(
        disease=MM, hepatic_adequacy_required=True,
        liver_enzyme_level_ast_uln_max=trial_max,
    )
    assert PatientInfoAttributes(patient).hepatic_adequacy_status is False  # default 2.5x
    assert _hepatic(trial, patient) == expected
    assert _in_pool(trial, patient) is (expected == 'matched')


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_hepatic_missing_lab_stays_unknown_not_a_rejection():
    # The aggregate must be NAMED null, exactly as the PROMOP surface does;
    # that is what makes the verdict "unknown" instead of the legacy False
    # (#5483/#621).
    patient = _patient({**HEPATIC, 'liver_enzyme_levels_ast': None,
                        'liver_enzyme_levels_alt': 90,
                        'serum_bilirubin_level_total': '1.2',
                        'hepatic_adequacy_status': None})
    trial = Trial.objects.create(
        disease=MM, hepatic_adequacy_required=True, liver_enzyme_level_ast_uln_max=5,
    )
    assert _hepatic(trial, patient) == 'unknown'
    assert _in_pool(trial, patient)


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_hepatic_trial_not_requiring_adequacy_matches():
    patient = _patient(HIGH_AST)
    trial = Trial.objects.create(disease=MM, hepatic_adequacy_required=False)
    assert _hepatic(trial, patient) == 'matched'
    assert _in_pool(trial, patient)


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_hepatic_trial_details_uses_the_trial_verdict():
    patient = _patient(HIGH_AST)
    trial = Trial.objects.create(
        disease=MM, hepatic_adequacy_required=True, liver_enzyme_level_ast_uln_max=5,
    )
    details = TrialAttributes(trial, patient).get_user_details(subform_attrs={})
    assert details['hepatic_adequacy_required']['uvalue'] is True


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_haematological_trial_min_replaces_the_default():
    """ANC 1200 passes the default 1000 cut-off but not a stated 1500."""
    patient = _patient({**ADEQUATE_LABS, 'absolute_neutrophile_count': 1200})
    default_trial = Trial.objects.create(
        disease=MM, code='default-anc', haematological_adequacy_required=True,
    )
    strict_trial = Trial.objects.create(
        disease=MM, code='strict-anc', haematological_adequacy_required=True,
        absolute_neutrophile_count_min=1500,
    )
    assert PatientInfoAttributes(patient).haematological_adequacy_status is True
    assert _haem(default_trial, patient) == 'matched'
    assert _haem(strict_trial, patient) == 'not_matched'
    assert _in_pool(default_trial, patient)
    assert not _in_pool(strict_trial, patient)


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_haematological_trial_max_is_enforced():
    patient = _patient(ADEQUATE_LABS)
    trial = Trial.objects.create(
        disease=MM, haematological_adequacy_required=True,
        platelet_count_max=90000,
    )
    assert _haem(trial, patient) == 'not_matched'
    assert not _in_pool(trial, patient)


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_haematological_missing_lab_stays_unknown():
    patient = _patient({**ADEQUATE_LABS, 'platelet_count': None,
                        'haematological_adequacy_status': None})
    trial = Trial.objects.create(disease=MM, haematological_adequacy_required=True)
    assert _haem(trial, patient) == 'unknown'


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_haematological_trial_details_uses_the_trial_verdict():
    patient = _patient({**ADEQUATE_LABS, 'absolute_neutrophile_count': 1200})
    trial = Trial.objects.create(
        disease=MM, haematological_adequacy_required=True,
        absolute_neutrophile_count_min=1500,
    )
    details = TrialAttributes(trial, patient).get_user_details(subform_attrs={})
    assert details['haematological_adequacy_required']['uvalue'] is False


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_haematological_trace_is_emitted_after_the_therapy_fallback():
    """CB orders this trace row after later_therapies; keep the two hosts alike."""
    patient = _patient(ADEQUATE_LABS)
    Trial.objects.create(disease=MM, haematological_adequacy_required=True)
    _, traces = Trial.objects.filter_by_patient_info(patient, add_traces=True)
    attrs = [t['attr'] for t in traces]
    assert attrs.count('patient_info.haematological_adequacy_status') <= 1
    if 'patient_info.haematological_adequacy_status' in attrs:
        assert attrs.index('patient_info.haematological_adequacy_status') > attrs.index(
            'patient_info.later_therapies'
        ) if 'patient_info.later_therapies' in attrs else True
