"""CB clinical source contract, with EXACT's stateless DTO and real trial model."""
import datetime as dt

import pytest

from exact_matching.matcher import UserToTrialAttrMatcher
from exact_matching.patient_info.patient_info_attributes import PatientInfoAttributes
from exact_matching.querysets.trial import TrialQuerySet
from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('days,expected', [(10, 'not_matched'), (60, 'matched'), (-1, 'unknown'), (None, 'unknown')])
def test_promop_sct_source_survives_real_adapter(days, expected):
    from trials.services.patient_info.promop_adapter import build_patient_info_from_promop_row

    date = dt.date.today() - dt.timedelta(days=days) if days is not None else None
    patient = build_patient_info_from_promop_row({
        'disease': 'multiple myeloma', 'sct_date': date.isoformat() if date else None,
        'stem_cell_transplant_date': '2000-01-01',
    })
    assert patient.stem_cell_transplant_date == date
    trial = Trial.objects.create(disease='multiple myeloma', day_since_stem_cell_transplant_required=30)
    assert UserToTrialAttrMatcher(trial, patient).attr_match_status('day_since_stem_cell_transplant') == expected
    pool, _ = TrialQuerySet(model=Trial).filter(pk=trial.pk).filter_by_patient_info(patient)
    assert pool.exists() is (expected != 'not_matched')


@pytest.mark.parametrize('required', [None, False, True])
def test_imwg_potential_question_requires_a_trial_restriction(required):
    from exact_matching.user_to_trial_attrs_mapper import UserToTrialAttrsMapper

    trial = Trial.objects.create(disease='multiple myeloma', measurable_disease_imwg_required=required)
    questions = UserToTrialAttrsMapper().potential_attrs_for_trial(
        trial=trial, counts={'measurable_disease_imwg': 2},
    )
    assert bool(questions) is (required is True)


@pytest.mark.parametrize('serum,urine,kappa,lambda_,expected', [
    (None, None, None, None, None),
    (0, 0, None, None, None),
    (0, 0, 0, 1, False),
    (0, 0, 1, 0, None),
    (0, 0, 99.9, 1, False),
    (0, 0, 100, 1, True),
    (0, 0, 1, 100, True),
    (0, 0, 100, 100, False),
    (0.499, 199.9, 1, 1, False),
    (0.5, None, None, None, True),
    (None, 200, None, None, True),
])
def test_imwg_upstream_derivation_and_normalizer_agree(serum, urine, kappa, lambda_, expected):
    from trials.services.patient_info.normalize import normalize_patient_info

    patient = PatientInfo(
        disease='multiple myeloma', monoclonal_protein_serum=serum,
        monoclonal_protein_urine=urine, kappa_flc=kappa, lambda_flc=lambda_,
    )
    attrs = PatientInfoAttributes(patient)
    assert attrs.measurable_disease_imwg is expected
    normalize_patient_info(patient)
    assert patient.measurable_disease_imwg is expected
    trial = Trial.objects.create(disease='multiple myeloma', measurable_disease_imwg_required=True)
    verdict = 'unknown' if expected is None else ('matched' if expected else 'not_matched')
    assert UserToTrialAttrMatcher(trial, patient).attr_match_status('measurable_disease_imwg') == verdict
    pool, _ = TrialQuerySet(model=Trial).filter(pk=trial.pk).filter_by_patient_info(patient)
    assert pool.exists() is (expected is not False)


@pytest.mark.parametrize('attribute,fields,restriction', [
    ('day_since_stem_cell_transplant', {'day_since_stem_cell_transplant_required': 30}, True),
    ('prothrombin_time_uln', {'prothrombin_time_uln_max': 1.5}, True),
    ('activated_partial_thromboplastin_time_uln', {'activated_partial_thromboplastin_time_uln_max': 1.5}, True),
    ('able_to_swallow_tablets_capsules', {'able_to_swallow_tablets_capsules_required': True}, True),
    ('able_to_swallow_tablets_capsules', {'able_to_swallow_tablets_capsules_required': False}, False),
    ('able_to_swallow_tablets_capsules', {'able_to_swallow_tablets_capsules_required': None}, False),
])
def test_new_potential_count_sql_executes_on_trial_columns(attribute, fields, restriction):
    from django.db.models.expressions import RawSQL
    from exact_matching.user_to_trial_attrs_mapper import UserToTrialAttrsMapper

    patient = PatientInfo(disease='multiple myeloma')
    trial = Trial.objects.create(disease='multiple myeloma', **fields)
    expressions = UserToTrialAttrsMapper().potential_attrs_to_check(patient_info=patient)
    expression = expressions[attribute]
    count = Trial.objects.filter(pk=trial.pk).annotate(
        contract_count=RawSQL(expression, []),
    ).values_list('contract_count', flat=True).get()
    assert count == (1 if restriction else None)


@pytest.mark.parametrize('days,expected,eligible', [
    (None, 'unknown', True), (-1, 'unknown', True), (0, 'not_matched', False),
    (29, 'not_matched', False), (30, 'matched', True), (60, 'matched', True),
])
def test_sct_date_is_computed_without_patient_persistence(days, expected, eligible):
    date = dt.date.today() - dt.timedelta(days=days) if days is not None else None
    patient = PatientInfo(disease='multiple myeloma', stem_cell_transplant_date=date)
    trial = Trial.objects.create(disease='multiple myeloma', day_since_stem_cell_transplant_required=30)
    attrs = PatientInfoAttributes(patient)
    assert attrs.get_value('day_since_stem_cell_transplant') == (days if days is not None and days >= 0 else None)
    assert UserToTrialAttrMatcher(trial, patient).attr_match_status('day_since_stem_cell_transplant') == expected
    pool, _ = TrialQuerySet(model=Trial).filter(pk=trial.pk).filter_by_patient_info(patient)
    assert pool.exists() is eligible


@pytest.mark.parametrize('attribute', ['prothrombin_time_uln', 'activated_partial_thromboplastin_time_uln'])
@pytest.mark.parametrize('value,expected,eligible', [(None, 'unknown', True), (1.0, 'matched', True), (1.5, 'matched', True), (1.6, 'not_matched', False)])
def test_clotting_bound_pair_is_a_ceiling(attribute, value, expected, eligible):
    patient = PatientInfo(**{attribute: value})
    trial = Trial.objects.create(disease='multiple myeloma', **{f'{attribute}_min': 1.5, f'{attribute}_max': 1.5})
    assert UserToTrialAttrMatcher(trial, patient).attr_match_status(attribute) == expected
    pool, _ = TrialQuerySet(model=Trial).filter(pk=trial.pk).filter_by_patient_info(patient)
    assert pool.exists() is eligible


@pytest.mark.parametrize('value', [None, False, True])
@pytest.mark.parametrize('required', [None, False, True])
def test_swallowing_tri_state(value, required):
    patient = PatientInfo(able_to_swallow_tablets_capsules=value)
    trial = Trial.objects.create(disease='multiple myeloma', able_to_swallow_tablets_capsules_required=required)
    expected = ('unknown' if value is None else 'not_matched') if required is True and value is not True else 'matched'
    assert UserToTrialAttrMatcher(trial, patient).attr_match_status('able_to_swallow_tablets_capsules') == expected
    pool, _ = TrialQuerySet(model=Trial).filter(pk=trial.pk).filter_by_patient_info(patient)
    assert pool.exists() is (expected != 'not_matched')
