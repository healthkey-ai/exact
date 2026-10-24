"""PatientRecord -> real DTO/matcher regression coverage for #627."""
from decimal import Decimal
import json

import pytest
from django.db.models.expressions import RawSQL
from django.test import override_settings
from rest_framework.renderers import JSONRenderer

from exact_matching.matcher import UserToTrialAttrMatcher
from exact_matching.patient_info.patient_info_attributes import PatientInfoAttributes
from exact_matching.querysets.trial import TrialQuerySet
from exact_matching.user_to_trial_attrs_mapper import UserToTrialAttrsMapper
from trials.models import Trial
from trials.services.patient_info.promop_adapter import (
    build_patient_info_from_promop_row,
    normalize_promop_row,
)
from trials.services.trial_details.trial_attributes import TrialAttributes
from trials.services.patient_info.resolve import _build_in_memory
from trials.api.patient_info_serializers import GraphPatientInfoSerializer, PatientInfoSerializer


def _federated_builder(row):
    # Exercise the actual authenticated view and JSON serialization separately
    # below; this helper reproduces its wire payload -> strict inline boundary.
    normalized = normalize_promop_row(dict(row))
    return _build_in_memory(json.loads(JSONRenderer().render(normalized)), strict=True)


BUILDERS = (build_patient_info_from_promop_row, _federated_builder)


DISEASES = (
    'multiple myeloma', 'follicular lymphoma', 'breast cancer',
    'chronic lymphocytic leukemia', 'mantle cell lymphoma',
)

FALLBACKS = (
    ('platelet_count', 'platelet_count_thousand_per_ul', 'CELLS/UL', 1000),
    ('hemoglobin_level', 'hemoglobin_g_dl', 'G/DL', 1),
    ('lactate_dehydrogenase_level', 'ldh_u_l', None, 1),
    ('liver_enzyme_levels_alt', 'alt_u_l', None, 1),
    ('liver_enzyme_levels_ast', 'ast_u_l', None, 1),
    ('liver_enzyme_levels_alp', 'alkaline_phosphatase_u_l', None, 1),
    ('creatinine_clearance_rate', 'creatinine_clearance_ml_min', None, 1),
    ('estimated_glomerular_filtration_rate', 'egfr_ml_min_173m2', None, 1),
    ('serum_calcium_level', 'serum_calcium_mg_dl', 'MG/DL', 1),
    ('serum_creatinine_level', 'serum_creatinine_mg_dl', 'MG/DL', 1),
    ('serum_bilirubin_level_total', 'bilirubin_total_mg_dl', 'MG/DL', 1),
    ('pd_l1_tumor_cels', 'pd_l1_tumor_cells', None, 1),
)


@pytest.mark.parametrize('target,source,unit,scale', FALLBACKS)
@pytest.mark.parametrize('value', [0, 12, '12', Decimal('12')])
def test_canonical_fallbacks_preserve_zero_and_pair_units(target, source, unit, scale, value):
    row = normalize_promop_row({source: value})
    assert row[target] == Decimal(str(value)) * scale
    if unit is not None:
        assert row[f'{target}_units'] == unit
    assert normalize_promop_row(dict(row)) == row


@pytest.mark.parametrize('target,source,unit,scale', FALLBACKS)
def test_reliable_direct_zero_wins_over_fallback(target, source, unit, scale):
    row = {target: 0, source: 12}
    if unit is not None:
        row[f'{target}_units'] = 'direct-unit'
    normalized = normalize_promop_row(row)
    assert normalized[target] == 0
    if unit is not None:
        assert normalized[f'{target}_units'] == 'direct-unit'


@pytest.mark.parametrize('target,source,unit,scale', FALLBACKS)
def test_null_direct_value_uses_canonical_unit_not_stale_unit(target, source, unit, scale):
    row = normalize_promop_row({target: None, f'{target}_units': 'stale-unit', source: 12})
    assert row[target] == 12 * scale
    if unit is not None:
        assert row[f'{target}_units'] == unit


@pytest.mark.parametrize('target,source,unit,scale', FALLBACKS)
@pytest.mark.parametrize('blank', ['', '  '])
def test_blank_direct_value_uses_canonical_fallback(target, source, unit, scale, blank):
    row = normalize_promop_row({target: blank, source: 12})
    assert row[target] == 12 * scale
    if unit is not None:
        assert row[f'{target}_units'] == unit


@pytest.mark.parametrize('disease', DISEASES)
@pytest.mark.parametrize('builder', BUILDERS)
@pytest.mark.parametrize('direct', ['', '  ', 0])
def test_blank_and_zero_direct_values_through_dto(disease, builder, direct):
    patient = builder({
        'disease': disease, 'hemoglobin_level': direct, 'hemoglobin_g_dl': 12,
        'lactate_dehydrogenase_level': direct, 'ldh_u_l': 300,
    })
    assert patient.hemoglobin_level == (0 if direct == 0 else 12)
    assert patient.lactate_dehydrogenase_level == (0 if direct == 0 else 300)


@pytest.mark.parametrize('bad', [None, '', 'not a number', 'NaN', 'Infinity', '1e309', '1e308', '1e1000000', True, []])
def test_bad_canonical_values_remain_unknown(bad):
    row = normalize_promop_row({'platelet_count_thousand_per_ul': bad})
    assert row.get('platelet_count') is None


def test_wbc_uses_canonical_source_over_unscaled_alias():
    row = normalize_promop_row({
        'wbc_count_thousand_per_ul': '7.5',
        'white_blood_cell_count': 7.5,
        'white_blood_cell_count_units': 'CELLS/L',
    })
    assert row['white_blood_cell_count'] == 7500
    assert row['white_blood_cell_count_units'] == 'CELLS/UL'
    assert normalize_promop_row(dict(row)) == row


def test_old_payload_without_canonical_columns_keeps_direct_labs():
    row = {'white_blood_cell_count': 7_500_000_000, 'white_blood_cell_count_units': 'CELLS/L'}
    assert normalize_promop_row(dict(row)) == row


@pytest.mark.parametrize('blank', ['', '  '])
def test_blank_canonical_wbc_does_not_delete_a_direct_reading(blank):
    row = normalize_promop_row({
        'white_blood_cell_count': 7_500_000_000,
        'white_blood_cell_count_units': 'CELLS/L',
        'wbc_count_thousand_per_ul': blank,
    })
    assert row['white_blood_cell_count'] == 7_500_000_000
    assert row['white_blood_cell_count_units'] == 'CELLS/L'


@pytest.mark.parametrize('malformed', ['NaN', 'abc', '1e309'])
def test_unusable_canonical_wbc_never_falls_back_to_the_untrusted_alias(malformed):
    """The alias may be the 1000x-low copy, so an unparseable canonical
    source must read unknown rather than publish a number that could be 10^3
    wrong."""
    row = normalize_promop_row({
        'white_blood_cell_count': 7.5,
        'white_blood_cell_count_units': 'CELLS/L',
        'wbc_count_thousand_per_ul': malformed,
    })
    assert row['white_blood_cell_count'] is None
    assert row['white_blood_cell_count_units'] == 'CELLS/UL'


@pytest.mark.parametrize('disease', DISEASES)
@pytest.mark.parametrize('builder', BUILDERS)
def test_blank_canonical_wbc_keeps_direct_value_through_dto(disease, builder):
    patient = builder({
        'disease': disease,
        'white_blood_cell_count': 7_500_000_000,
        'white_blood_cell_count_units': 'CELLS/L',
        'wbc_count_thousand_per_ul': '',
    })
    assert patient.white_blood_cell_count == 7_500_000_000
    assert PatientInfoAttributes(patient).get_value('white_blood_cell_count') == 7_500_000_000


@pytest.mark.parametrize('disease', DISEASES)
@pytest.mark.parametrize('builder', BUILDERS)
def test_real_builder_keeps_canonical_values_for_every_disease(disease, builder):
    patient = builder({
        'disease': disease, 'wbc_count_thousand_per_ul': 7.5,
        'platelet_count_thousand_per_ul': 150, 'alt_u_l': 20,
        'ast_u_l': 21, 'alkaline_phosphatase_u_l': 22,
        'creatinine_clearance_ml_min': 80, 'egfr_ml_min_173m2': 85,
        'serum_calcium_mg_dl': 9, 'bilirubin_total_mg_dl': 1,
        'hemoglobin_g_dl': 12, 'pd_l1_tumor_cells': 50,
    })
    attrs = PatientInfoAttributes(patient)
    # Physical value agrees with CB's 7500/µL; EXACT's configured comparison
    # basis remains cells/L rather than being silently changed by this port.
    assert attrs.get_value('white_blood_cell_count') == 7_500_000_000
    assert attrs.get_value('platelet_count') == 150000
    assert patient.liver_enzyme_levels_alt == 20
    assert patient.liver_enzyme_levels_ast == 21
    assert patient.liver_enzyme_levels_alp == 22
    assert patient.creatinine_clearance_rate == 80
    assert patient.estimated_glomerular_filtration_rate == 85
    assert patient.serum_calcium_level == 9
    assert patient.serum_bilirubin_level_total == 1
    assert patient.hemoglobin_level == 12
    assert patient.pd_l1_tumor_cels == 50


def test_creatinine_fallback_retains_mg_dl_unit():
    patient = build_patient_info_from_promop_row({'serum_creatinine_mg_dl': 1})
    assert patient.serum_creatinine_level == 1
    assert patient.serum_creatinine_level_units == 'MG/DL'


@pytest.mark.parametrize('disease', DISEASES)
@pytest.mark.parametrize('egfr', [0, 40, 85, '40'])
@pytest.mark.parametrize('source', ['egfr_ml_min_173m2', 'estimated_glomerular_filtration_rate'])
@pytest.mark.parametrize('builder', BUILDERS)
def test_source_egfr_survives_creatinine_derivation(disease, egfr, source, builder):
    patient = builder({
        'disease': disease, 'gender': 'M', 'patient_age': 20,
        source: egfr, 'serum_creatinine_mg_dl': 0.8,
    })
    assert patient.estimated_glomerular_filtration_rate == int(egfr)
    trial = Trial(estimated_glomerular_filtration_rate_min=60)
    # Existing numeric-zero blankness is tracked separately in #509. This
    # adapter fix must retain the actual zero rather than publish derived 129.93.
    expected = 'unknown' if int(egfr) == 0 else ('matched' if int(egfr) >= 60 else 'not_matched')
    assert UserToTrialAttrMatcher(trial, patient).attr_match_status('estimated_glomerular_filtration_rate') == expected


@pytest.mark.parametrize('bad', ['not a number', [], {}, True, 'NaN', 'Infinity'])
@pytest.mark.parametrize('builder', BUILDERS)
def test_invalid_source_egfr_does_not_block_valid_derivation(bad, builder):
    patient = builder({
        'gender': 'M', 'patient_age': 20,
        'estimated_glomerular_filtration_rate': bad,
        'serum_creatinine_mg_dl': 0.8,
    })
    assert patient.estimated_glomerular_filtration_rate > 60


@pytest.mark.parametrize('codes', [[], ['diabetes'], ['diabetes', 'cardiac_disease']])
@pytest.mark.parametrize('builder', BUILDERS)
def test_condition_codes_reach_matcher_without_database_access(codes, builder):
    patient = builder({'preexisting_conditions': codes})
    assert PatientInfoAttributes(patient).get_value('pre_existing_condition_categories') == codes
    assert patient._pre_existing_condition_codes_cache is not codes
    for serializer in (PatientInfoSerializer, GraphPatientInfoSerializer):
        assert serializer(patient).data['preExistingConditionCategories'] == codes


@pytest.mark.parametrize('builder', BUILDERS)
def test_malformed_source_item_does_not_erase_known_condition_codes(builder):
    patient = builder({'preexisting_conditions': ['diabetes', None, '', 123]})
    assert PatientInfoAttributes(patient).get_value('pre_existing_condition_categories') == ['diabetes']
    assert GraphPatientInfoSerializer(patient).data['preExistingConditionCategories'] == ['diabetes']


@pytest.mark.django_db
@pytest.mark.parametrize('builder', BUILDERS)
@pytest.mark.parametrize('source_codes', [[], ['source-category']])
def test_legacy_category_pk_input_keeps_precedence_over_source_codes(builder, source_codes):
    from tests.factories import PreExistingConditionCategoryFactory

    category = PreExistingConditionCategoryFactory(code='native-category')
    patient = builder({
        'pre_existing_condition_categories': [category.pk],
        'preexisting_conditions': source_codes,
    })
    assert PatientInfoAttributes(patient).get_value('pre_existing_condition_categories') == ['native-category']
    assert GraphPatientInfoSerializer(patient).data['preExistingConditionCategories'] == ['native-category']


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
@pytest.mark.parametrize('disease', DISEASES)
@pytest.mark.parametrize('builder', BUILDERS)
def test_canonical_platelets_agree_across_pool_verdict_counts_and_details(disease, builder):
    patient = builder({
        'disease': disease, 'platelet_count_thousand_per_ul': 150,
    })
    trial = Trial.objects.create(disease=disease, platelet_count_min=160000)
    assert UserToTrialAttrMatcher(trial, patient).attr_match_status('platelet_count') == 'not_matched'
    pool, _ = TrialQuerySet(model=Trial).filter(pk=trial.pk).filter_by_patient_info(patient)
    assert not pool.exists()
    details = TrialAttributes(trial, patient).get_user_details(subform_attrs={})
    assert details['platelet_count_min']['uvalue'] == 150000
    # Answered canonical platelets must no longer be counted as a missing
    # question. An otherwise identical old payload still gets that question.
    assert 'platelet_count' not in UserToTrialAttrsMapper().potential_attrs_to_check(patient_info=patient)
    blank_patient = build_patient_info_from_promop_row({'disease': disease})
    sql = UserToTrialAttrsMapper().potential_attrs_to_check(patient_info=blank_patient)['platelet_count']
    count = Trial.objects.filter(pk=trial.pk).annotate(value=RawSQL(sql, [])).values_list('value', flat=True).get()
    assert count == 1


@pytest.mark.django_db
@pytest.mark.parametrize('disease', DISEASES)
@pytest.mark.parametrize('builder', BUILDERS)
def test_real_condition_codes_enforce_exclusions_in_pool_and_verdict(disease, builder):
    patient = builder({
        'disease': disease, 'preexisting_conditions': ['diabetes'],
    })
    trial = Trial.objects.create(disease=disease, pre_existing_conditions_excluded=['diabetes'])
    assert UserToTrialAttrMatcher(trial, patient).attr_match_status('pre_existing_condition_categories') == 'not_matched'
    pool, _ = TrialQuerySet(model=Trial).filter(pk=trial.pk).filter_by_patient_info(patient)
    assert not pool.exists()


@pytest.mark.parametrize('disease', DISEASES)
@pytest.mark.parametrize('builder', BUILDERS)
def test_canonical_platelets_do_not_introduce_false_adequacy(disease, builder):
    patient = builder({
        'disease': disease, 'platelet_count_thousand_per_ul': 50,
        'absolute_neutrophile_count': 2000, 'hemoglobin_level': 12,
    })
    assert patient.platelet_count == 50000
    assert patient.haematological_adequacy_status is False
    assert UserToTrialAttrMatcher(
        Trial(haematological_adequacy_required=True), patient,
    ).attr_match_status('haematological_adequacy_status') == 'not_matched'


@pytest.mark.parametrize('disease', DISEASES)
@pytest.mark.parametrize('builder', BUILDERS)
def test_reading_that_overflows_on_unit_conversion_is_unknown(disease, builder):
    """`1e303` cells/uL is finite; EXACT's cells/L basis is not (#627 review).

    The source reading passes the adapter's finite check and the unit
    conversion then produces infinity, which strict JSON rendering cannot
    publish and which clears every threshold it is compared against.
    """
    from exact_matching.patient_info.patient_info_attributes import PatientInfoAttributes

    patient = builder({
        'disease': disease, 'wbc_count_thousand_per_ul': '1e300',
    })
    assert patient.white_blood_cell_count == 1e303
    assert PatientInfoAttributes(patient).get_value('white_blood_cell_count') is None

    trial = Trial(disease=disease, white_blood_cell_count_min=1000)
    assert UserToTrialAttrMatcher(
        trial, patient,
    ).attr_match_status('white_blood_cell_count') == 'unknown'


@pytest.mark.django_db
@pytest.mark.parametrize('disease', DISEASES)
@pytest.mark.parametrize('builder', BUILDERS)
def test_overflowing_reading_renders_trial_details_as_json(disease, builder):
    """Trial Details is where an infinity would become an error response."""
    from rest_framework.renderers import JSONRenderer
    from trials.services.trial_details.trial_templates import TrialTemplates

    patient = builder({
        'disease': disease, 'wbc_count_thousand_per_ul': '1e300',
    })
    trial = Trial.objects.create(disease=disease, white_blood_cell_count_min=1000)
    payload = TrialTemplates(trial, patient_info=patient).potential_attributes_first_view(None)
    JSONRenderer().render(payload)
    assert not any(
        attr.get('uvalue') is not None and not isinstance(attr.get('uvalue'), (int, float, str, list, dict, bool))
        for attr in payload['trialEligibilityAttributes']
    )


@pytest.mark.parametrize('disease', DISEASES)
@pytest.mark.parametrize('builder', BUILDERS)
def test_ordinary_wbc_conversion_stays_a_reading(disease, builder):
    patient = builder({
        'disease': disease, 'wbc_count_thousand_per_ul': 7.5,
    })
    assert PatientInfoAttributes(patient).get_value('white_blood_cell_count') == 7_500_000_000
