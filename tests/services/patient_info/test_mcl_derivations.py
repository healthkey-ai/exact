"""
Unit tests for MCL risk-score derivations (#41).

Covers MIPI / MIPI-C bucket boundaries, bulky-disease threshold rules, and
the wiring through normalize_patient_info() that overwrites caller-supplied
values for MCL patients.
"""
import pytest

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes
from trials.services.patient_info.normalize import normalize_patient_info


def _mcl_patient(**kwargs):
    """Build an MCL PatientInfo with sensible defaults so derivations exercise
    only the field under test."""
    defaults = dict(
        disease='mantle cell lymphoma',
        patient_age=50,
        ecog_performance_status=0,
        white_blood_cell_count=5000,
        white_blood_cell_count_units='CELLS/UL',
        lactate_dehydrogenase_level=200,
        ki67_proliferation_index=15,
    )
    defaults.update(kwargs)
    return PatientInfo(**defaults)


class TestMipiRiskBuckets:
    """Hoster 2008 MIPI cutoffs: low < 5.7, intermediate 5.7-<6.2, high >= 6.2
    (high cutoff is 6.2, not 6.5 — CB migration 0370, SME-confirmed #4478)."""

    def test_low_for_healthy_inputs(self):
        # WBC as cells/µL (Hoster 2008). score ≈ 4.41 — below 5.7
        pi = _mcl_patient(patient_age=35, ecog_performance_status=0,
                          white_blood_cell_count=5000, white_blood_cell_count_units='CELLS/UL',
                          lactate_dehydrogenase_level=150)
        assert PatientInfoAttributes(pi).mipi_risk == 'low'

    def test_intermediate_for_moderate_disease(self):
        # score ≈ 5.91 — in [5.7, 6.2)
        pi = _mcl_patient(patient_age=65, ecog_performance_status=0,
                          white_blood_cell_count=7000, white_blood_cell_count_units='CELLS/UL',
                          lactate_dehydrogenase_level=250)
        assert PatientInfoAttributes(pi).mipi_risk == 'intermediate'

    def test_high_for_aggressive_disease(self):
        # score well above 6.2
        pi = _mcl_patient(patient_age=75, ecog_performance_status=3,
                          white_blood_cell_count=100000, white_blood_cell_count_units='CELLS/UL',
                          lactate_dehydrogenase_level=3000)
        assert PatientInfoAttributes(pi).mipi_risk == 'high'

    def test_wbc_uses_per_microliter_magnitude_4421(self):
        # Regression guard for CB #4421: WBC must be read as cells/µL. A median
        # 7000/µL patient is 'intermediate'; the old ×10⁹/L reading (log10(7))
        # scored ~2.82 lower and wrongly returned 'low'.
        pi = _mcl_patient(patient_age=65, ecog_performance_status=0,
                          white_blood_cell_count=7000, white_blood_cell_count_units='CELLS/UL',
                          lactate_dehydrogenase_level=250)
        assert PatientInfoAttributes(pi).mipi_risk == 'intermediate'

    def test_high_cutoff_is_6_2_not_6_5(self):
        # Regression for the CB catch-up (#4478): the high band starts at 6.2, not
        # 6.5. age 73 + wbc 10000/uL (ldh at ULN) -> score ~= 6.34, which is in the
        # [6.2, 6.5) band that flips from 'intermediate' (old) to 'high' (now).
        pi = _mcl_patient(patient_age=73, ecog_performance_status=0,
                          white_blood_cell_count=10000, white_blood_cell_count_units='CELLS/UL',
                          lactate_dehydrogenase_level=250)
        assert PatientInfoAttributes(pi).mipi_risk == 'high'

    def test_ecog_zero_does_not_add_penalty(self):
        # ECOG 0 and 1 both contribute 0 (predicate is ecog >= 2)
        low_ecog = _mcl_patient(ecog_performance_status=0)
        med_ecog = _mcl_patient(ecog_performance_status=1)
        attrs_low = PatientInfoAttributes(low_ecog)
        attrs_med = PatientInfoAttributes(med_ecog)
        assert attrs_low.mipi_risk == attrs_med.mipi_risk

    @pytest.mark.parametrize('missing', [
        {'patient_age': None},
        {'ecog_performance_status': None},
        {'white_blood_cell_count': None},
        {'lactate_dehydrogenase_level': None},
    ])
    def test_returns_none_when_any_input_missing(self, missing):
        pi = _mcl_patient(**missing)
        assert PatientInfoAttributes(pi).mipi_risk is None

    @pytest.mark.parametrize('bad', [
        {'white_blood_cell_count': 0},
        {'white_blood_cell_count': -1e9},
        {'lactate_dehydrogenase_level': 0},
        {'lactate_dehydrogenase_level': -50},
    ])
    def test_returns_none_for_non_positive_wbc_or_ldh(self, bad):
        # log10 of <=0 is undefined; algorithm must guard.
        pi = _mcl_patient(**bad)
        assert PatientInfoAttributes(pi).mipi_risk is None

    def test_unit_conversion_when_wbc_in_cells_per_ul(self):
        # 8000 CELLS/UL == 8e9 CELLS/L; should give the same MIPI as the
        # CELLS/L canonical input above.
        canonical = _mcl_patient(white_blood_cell_count=8e9,
                                 white_blood_cell_count_units='CELLS/L')
        ul_form = _mcl_patient(white_blood_cell_count=8000,
                               white_blood_cell_count_units='CELLS/UL')
        assert PatientInfoAttributes(canonical).mipi_risk == \
               PatientInfoAttributes(ul_form).mipi_risk


class TestMipiCRiskMatrix:
    """4-tier MIPI-C matrix from Hoster 2014: combines MIPI with Ki-67 < 30 vs >= 30."""

    def test_low_mipi_plus_low_ki67_is_low(self):
        pi = _mcl_patient(ki67_proliferation_index=15)  # low MIPI by default
        assert PatientInfoAttributes(pi).mipi_c_risk == 'low'

    def test_low_mipi_plus_high_ki67_is_low_intermediate(self):
        pi = _mcl_patient(ki67_proliferation_index=45)
        assert PatientInfoAttributes(pi).mipi_c_risk == 'low_intermediate'

    def test_intermediate_mipi_plus_low_ki67_is_low_intermediate(self):
        pi = _mcl_patient(patient_age=65, ecog_performance_status=0,
                          white_blood_cell_count=7000, white_blood_cell_count_units='CELLS/UL',
                          lactate_dehydrogenase_level=250, ki67_proliferation_index=20)
        assert PatientInfoAttributes(pi).mipi_c_risk == 'low_intermediate'

    def test_intermediate_mipi_plus_high_ki67_is_high_intermediate(self):
        pi = _mcl_patient(patient_age=65, ecog_performance_status=0,
                          white_blood_cell_count=7000, white_blood_cell_count_units='CELLS/UL',
                          lactate_dehydrogenase_level=250, ki67_proliferation_index=35)
        assert PatientInfoAttributes(pi).mipi_c_risk == 'high_intermediate'

    def test_high_mipi_plus_low_ki67_is_high_intermediate(self):
        pi = _mcl_patient(patient_age=90, ecog_performance_status=2,
                          white_blood_cell_count=100000, white_blood_cell_count_units='CELLS/UL', lactate_dehydrogenase_level=1500,
                          ki67_proliferation_index=20)
        assert PatientInfoAttributes(pi).mipi_c_risk == 'high_intermediate'

    def test_high_mipi_plus_high_ki67_is_high(self):
        pi = _mcl_patient(patient_age=90, ecog_performance_status=2,
                          white_blood_cell_count=100000, white_blood_cell_count_units='CELLS/UL', lactate_dehydrogenase_level=1500,
                          ki67_proliferation_index=60)
        assert PatientInfoAttributes(pi).mipi_c_risk == 'high'

    def test_ki67_boundary_at_30_falls_into_upper_bucket(self):
        # Predicate is `ki67 < 30`, so 30 itself goes to the upper bucket.
        pi = _mcl_patient(ki67_proliferation_index=29)
        assert PatientInfoAttributes(pi).mipi_c_risk == 'low'
        pi_30 = _mcl_patient(ki67_proliferation_index=30)
        assert PatientInfoAttributes(pi_30).mipi_c_risk == 'low_intermediate'

    @pytest.mark.parametrize('missing', [
        {'patient_age': None},
        {'ki67_proliferation_index': None},
    ])
    def test_returns_none_when_inputs_missing(self, missing):
        pi = _mcl_patient(**missing)
        assert PatientInfoAttributes(pi).mipi_c_risk is None


class TestBulkyDiseaseCriteria:
    """Lesion / node use >= thresholds; spleen uses > thresholds at 13/15/20
    plus a >= 20 variant — mirrors CB so trials matching either spleen
    predicate work."""

    def test_no_size_inputs_returns_none(self):
        pi = _mcl_patient()
        assert PatientInfoAttributes(pi).bulky_disease_criteria is None

    def test_lesion_5cm_alone(self):
        pi = _mcl_patient(largest_lesion_size=5)
        assert PatientInfoAttributes(pi).bulky_disease_criteria == 'bulky_lesion_5cm'

    def test_lesion_10cm_fires_all_three_lesion_thresholds(self):
        pi = _mcl_patient(largest_lesion_size=12)
        assert PatientInfoAttributes(pi).bulky_disease_criteria == \
            'bulky_lesion_5cm,bulky_lesion_7_5cm,bulky_lesion_10cm'

    def test_lesion_under_5cm_does_not_fire(self):
        pi = _mcl_patient(largest_lesion_size=4.9)
        assert PatientInfoAttributes(pi).bulky_disease_criteria is None

    def test_node_5cm_alone(self):
        pi = _mcl_patient(largest_lymph_node_size=5)
        assert PatientInfoAttributes(pi).bulky_disease_criteria == 'bulky_node_5cm'

    def test_node_10cm_fires_all_three_node_thresholds(self):
        pi = _mcl_patient(largest_lymph_node_size=10)
        assert PatientInfoAttributes(pi).bulky_disease_criteria == \
            'bulky_node_5cm,bulky_node_7_5cm,bulky_node_10cm'

    def test_spleen_uses_strict_gt_thresholds(self):
        # Spleen 13 itself does NOT fire bulky_spleen_13cm (predicate is > 13)
        pi_13 = _mcl_patient(spleen_size=13)
        assert PatientInfoAttributes(pi_13).bulky_disease_criteria is None
        pi_13_1 = _mcl_patient(spleen_size=13.1)
        assert PatientInfoAttributes(pi_13_1).bulky_disease_criteria == 'bulky_spleen_13cm'

    def test_spleen_20_exactly_fires_gte_only(self):
        pi = _mcl_patient(spleen_size=20)
        assert PatientInfoAttributes(pi).bulky_disease_criteria == \
            'bulky_spleen_13cm,bulky_spleen_15cm,bulky_spleen_20cm_gte'

    def test_spleen_above_20_fires_both_gt_and_gte(self):
        pi = _mcl_patient(spleen_size=21)
        assert PatientInfoAttributes(pi).bulky_disease_criteria == \
            'bulky_spleen_13cm,bulky_spleen_15cm,bulky_spleen_20cm_gt,bulky_spleen_20cm_gte'

    def test_multiple_anatomic_sites_combine(self):
        pi = _mcl_patient(largest_lesion_size=5, largest_lymph_node_size=5, spleen_size=14)
        assert PatientInfoAttributes(pi).bulky_disease_criteria == \
            'bulky_lesion_5cm,bulky_node_5cm,bulky_spleen_13cm'


class TestNormalizerWiring:
    """normalize_patient_info() must overwrite caller-supplied MCL fields
    for MCL patients and leave non-MCL patients untouched."""

    @pytest.mark.django_db
    def test_mcl_patient_caller_values_get_overwritten(self):
        pi = _mcl_patient(
            mipi_risk='high',  # caller-supplied; should be overwritten to 'low'
            mipi_c_risk='high',
            bulky_disease_criteria='caller_supplied_garbage',
            largest_lesion_size=12,
        )
        normalize_patient_info(pi)
        assert pi.mipi_risk == 'low'  # actual derivation for the default inputs
        assert pi.mipi_c_risk == 'low'
        assert pi.bulky_disease_criteria == \
            'bulky_lesion_5cm,bulky_lesion_7_5cm,bulky_lesion_10cm'

    @pytest.mark.django_db
    def test_non_mcl_patient_mcl_fields_left_alone(self):
        # An MM patient with caller-supplied MCL fields should NOT be touched
        # (the field exists for forward-compat but doesn't apply clinically).
        pi = PatientInfo(disease='multiple myeloma', mipi_risk='caller_value')
        normalize_patient_info(pi)
        assert pi.mipi_risk == 'caller_value'

    @pytest.mark.django_db
    @pytest.mark.parametrize('disease', [
        'mantle cell lymphoma',
        'Mantle Cell Lymphoma',
        'MANTLE CELL LYMPHOMA',
    ])
    def test_disease_gate_is_case_insensitive(self, disease):
        # str(pi.disease).lower() == 'mantle cell lymphoma' must accept any
        # casing the API caller sends.
        pi = _mcl_patient(disease=disease, largest_lesion_size=6)
        normalize_patient_info(pi)
        assert pi.bulky_disease_criteria == 'bulky_lesion_5cm'


def _high_risk_codes(pi):
    """Set of derived high-risk MCL criterion codes (comma-string -> set)."""
    derived = PatientInfoAttributes(pi).high_risk_mcl_criteria
    return set(derived.split(',')) if derived else set()


class TestHighRiskMclCriteriaDerivation:
    """Ported from CB: patient high-risk MCL criteria derivation (#4399-#4437)."""

    def test_no_inputs_returns_none(self):
        # Defaults give mipi 'low' / ki67 15 / no markers or sizes -> nothing.
        assert PatientInfoAttributes(_mcl_patient()).high_risk_mcl_criteria is None

    def test_molecular_marker_codes(self):
        pi = _mcl_patient(molecular_markers='tp53Mutation,bcl2Amplification')
        codes = _high_risk_codes(pi)
        assert 'tp53_mutation' in codes
        assert 'bcl2_amplification' in codes

    def test_notch_combined_emits_single_code(self):
        # Ambiguous combined option must NOT emit gene-specific codes (#4406).
        codes = _high_risk_codes(_mcl_patient(molecular_markers='notch1or2Mutations'))
        assert 'notch1_or_2' in codes
        assert 'notch1_mutation' not in codes
        assert 'notch2_mutation' not in codes

    def test_complex_karyotype_strict_also_satisfies_plain(self):
        codes = _high_risk_codes(_mcl_patient(cytogenic_markers='complexKaryotypeExcludingT1114'))
        assert 'complex_karyotype' in codes
        assert 'complex_karyotype_strict' in codes

    def test_complex_karyotype_plain_only(self):
        codes = _high_risk_codes(_mcl_patient(cytogenic_markers='complexKaryotype'))
        assert 'complex_karyotype' in codes
        assert 'complex_karyotype_strict' not in codes

    def test_p53_ihc_threshold(self):
        assert 'p53_ihc_gte_50' in _high_risk_codes(_mcl_patient(p53_ihc=50))
        assert 'p53_ihc_gte_50' not in _high_risk_codes(_mcl_patient(p53_ihc=49))

    def test_ki67_tiers(self):
        codes = _high_risk_codes(_mcl_patient(ki67_proliferation_index=55))
        assert codes >= {'ki67_gt_30', 'ki67_gte_30', 'ki67_gt_50', 'ki67_gte_50'}

    def test_high_mipi_code(self):
        # Aggressive inputs -> mipi high.
        pi = _mcl_patient(patient_age=90, ecog_performance_status=2,
                          white_blood_cell_count=100000, white_blood_cell_count_units='CELLS/UL', lactate_dehydrogenase_level=1500)
        assert 'high_mipi' in _high_risk_codes(pi)

    def test_mipi_c_high(self):
        # mipi high + ki67 >= 30 -> mipi_c high.
        pi = _mcl_patient(patient_age=90, ecog_performance_status=2,
                          white_blood_cell_count=100000, white_blood_cell_count_units='CELLS/UL', lactate_dehydrogenase_level=1500,
                          ki67_proliferation_index=40)
        assert 'mipi_c_high' in _high_risk_codes(pi)

    def test_size_and_lymphocytosis_codes(self):
        pi = _mcl_patient(largest_lesion_size=8, largest_lymph_node_size=5,
                          spleen_size=20, absolute_lymphocyte_count=60000)
        codes = _high_risk_codes(pi)
        assert {'lesion_gte_5cm', 'lesion_gte_7_5cm'} <= codes
        assert 'lesion_gt_10cm' not in codes
        assert 'node_gte_5cm' in codes
        assert {'spleen_gte_13cm', 'spleen_gte_15cm', 'spleen_gte_20cm'} <= codes
        assert 'lymphocytosis_gte_50k' in codes


class TestHighRiskMclUnknownCodes:
    """unknown-vs-none distinction (#4399/#4416)."""

    def test_unknown_when_source_blank(self):
        # molecular_markers unanswered -> tp53_mutation cannot be ruled absent.
        attrs = PatientInfoAttributes(_mcl_patient())
        assert 'tp53_mutation' in attrs.high_risk_mcl_criteria_unknown_codes(['tp53_mutation'])

    def test_known_when_source_answered(self):
        attrs = PatientInfoAttributes(_mcl_patient(molecular_markers='ccnd1Alteration'))
        assert attrs.high_risk_mcl_criteria_unknown_codes(['tp53_mutation']) == set()

    def test_notch_specific_unknown_while_combined_selected(self):
        # Combined NOTCH option leaves gene-specific codes undeterminable.
        attrs = PatientInfoAttributes(_mcl_patient(molecular_markers='notch1or2Mutations'))
        unknown = attrs.high_risk_mcl_criteria_unknown_codes(['notch1_mutation'])
        assert 'notch1_mutation' in unknown

    def test_all_unknown_codes_covers_vocabulary(self):
        # With no inputs, every code with a backing source is unknown.
        attrs = PatientInfoAttributes(_mcl_patient())
        all_unknown = attrs.high_risk_mcl_criteria_all_unknown_codes()
        assert 'tp53_mutation' in all_unknown
        assert 'del17p' in all_unknown