"""
Tests for _normalize_ctomop_row() and related resolvers in search_trials_for_patients.

The function normalises raw CTOMOP patient_info rows into the value format
EXACT's matching engine expects.  Tests are split by concern:

* Pure-logic transforms (TNM, stage, grade, outcome, etc.) — no DB required.
* DB-backed alias tests (receptor statuses, ethnicity) — use mocked code-lookup
  so they run without a live 'trials' DB alias.
"""
from unittest.mock import patch

import pytest

from trials.management.commands.search_trials_for_patients import (
    _normalize_ctomop_row,
    _resolve_code,
    _resolve_therapy_code,
    _resolve_code_csv,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(**kwargs):
    """Build a minimal CTOMOP row dict."""
    return dict(kwargs)


# ---------------------------------------------------------------------------
# TNM staging — "T1: Invasive Tumor ≤ 2 cm" → "t1"
# ---------------------------------------------------------------------------

class TestTnmStaging:
    def test_tumor_stage_extracts_code_before_colon(self):
        result = _normalize_ctomop_row(_row(tumor_stage='T1: Invasive Tumor ≤ 2 cm'))
        assert result['tumor_stage'] == 't1'

    def test_nodes_stage_extraction(self):
        result = _normalize_ctomop_row(_row(nodes_stage='N0: No regional lymph node metastasis'))
        assert result['nodes_stage'] == 'n0'

    def test_distant_metastasis_i_plus_converted(self):
        result = _normalize_ctomop_row(
            _row(distant_metastasis_stage='M0(i+): Isolated tumor cells in bone marrow')
        )
        assert result['distant_metastasis_stage'] == 'm0(i_plus)'

    def test_code_without_colon_passes_through(self):
        result = _normalize_ctomop_row(_row(tumor_stage='t2'))
        assert result['tumor_stage'] == 't2'

    def test_non_string_passes_through(self):
        result = _normalize_ctomop_row(_row(tumor_stage=None))
        assert result['tumor_stage'] is None


# ---------------------------------------------------------------------------
# Stage — strip trailing sub-stage letter (IIIB → III)
# ---------------------------------------------------------------------------

class TestStageNormalization:
    def test_strips_trailing_a(self):
        assert _normalize_ctomop_row(_row(stage='IIIA'))['stage'] == 'III'

    def test_strips_trailing_b(self):
        assert _normalize_ctomop_row(_row(stage='IIIB'))['stage'] == 'III'

    def test_strips_trailing_c(self):
        assert _normalize_ctomop_row(_row(stage='IVC'))['stage'] == 'IV'

    def test_roman_numeral_without_suffix_unchanged(self):
        assert _normalize_ctomop_row(_row(stage='IV'))['stage'] == 'IV'

    def test_non_string_stage_does_not_crash(self):
        # Defends re.sub against unexpected int/None coming from upstream
        assert _normalize_ctomop_row(_row(stage=4))['stage'] == 4
        assert _normalize_ctomop_row(_row(stage=None))['stage'] is None

    def test_stage_i_unchanged(self):
        assert _normalize_ctomop_row(_row(stage='I'))['stage'] == 'I'


# ---------------------------------------------------------------------------
# Tumor grade — int (1-4) → EXACT code ('10', '20', '30', '40')
# ---------------------------------------------------------------------------

class TestTumorGrade:
    @pytest.mark.parametrize('grade, expected', [(1, '10'), (2, '20'), (3, '30'), (4, '40')])
    def test_int_to_code(self, grade, expected):
        assert _normalize_ctomop_row(_row(tumor_grade=grade))['tumor_grade'] == expected

    def test_string_code_passes_through(self):
        # Already-normalised values must not be double-converted
        assert _normalize_ctomop_row(_row(tumor_grade='20'))['tumor_grade'] == '20'

    def test_none_passes_through(self):
        assert _normalize_ctomop_row(_row(tumor_grade=None))['tumor_grade'] is None


# ---------------------------------------------------------------------------
# Biopsy grade — int → str
# ---------------------------------------------------------------------------

class TestBiopsyGrade:
    @pytest.mark.parametrize('grade', [1, 2, 3])
    def test_int_to_string(self, grade):
        assert _normalize_ctomop_row(_row(biopsy_grade=grade))['biopsy_grade'] == str(grade)

    def test_string_passthrough(self):
        assert _normalize_ctomop_row(_row(biopsy_grade='2'))['biopsy_grade'] == '2'


# ---------------------------------------------------------------------------
# Outcome mapping — full-text labels → abbreviated IDs
# ---------------------------------------------------------------------------

class TestOutcomeMapping:
    @pytest.mark.parametrize('label, code', [
        ('Complete Response',                        'CR'),
        ('Complete Response (CR)',                   'CR'),
        ('Stringent Complete Response (sCR)',        'sCR'),
        ('Very Good Partial Response (VGPR)',        'VGPR'),
        ('Partial Response',                         'PR'),
        ('Partial Response (PR)',                    'PR'),
        ('Minimal Residual Disease (MRD) Negativity','MRD'),
        ('Stable Disease (SD)',                      'SD'),
        ('Progressive Disease',                      'PD'),
        ('Progressive Disease (PD)',                 'PD'),
    ])
    def test_known_labels_map_to_code(self, label, code):
        assert _normalize_ctomop_row(_row(first_line_outcome=label))['first_line_outcome'] == code

    def test_unknown_maps_to_none(self):
        assert _normalize_ctomop_row(_row(first_line_outcome='Unknown'))['first_line_outcome'] is None

    def test_unrecognised_label_passes_through(self):
        assert _normalize_ctomop_row(
            _row(first_line_outcome='Unexpected value')
        )['first_line_outcome'] == 'Unexpected value'

    def test_applies_to_second_line_outcome(self):
        assert _normalize_ctomop_row(_row(second_line_outcome='Partial Response'))['second_line_outcome'] == 'PR'

    def test_applies_to_later_outcome(self):
        assert _normalize_ctomop_row(_row(later_outcome='Progressive Disease (PD)'))['later_outcome'] == 'PD'


# ---------------------------------------------------------------------------
# Treatment refractory status mapping
# ---------------------------------------------------------------------------

class TestRefractoryStatusMapping:
    @pytest.mark.parametrize('ctomop_val, exact_val', [
        ('Responsive', 'notRefractory'),
        ('Stable',     'notRefractory'),
        ('Refractory', 'primaryRefractory'),
    ])
    def test_maps_ctomop_labels(self, ctomop_val, exact_val):
        result = _normalize_ctomop_row(_row(treatment_refractory_status=ctomop_val))
        assert result['treatment_refractory_status'] == exact_val

    def test_unknown_maps_to_none(self):
        result = _normalize_ctomop_row(_row(treatment_refractory_status='Unknown'))
        assert result['treatment_refractory_status'] is None

    def test_already_normalised_passes_through(self):
        result = _normalize_ctomop_row(_row(treatment_refractory_status='notRefractory'))
        assert result['treatment_refractory_status'] == 'notRefractory'


# ---------------------------------------------------------------------------
# Prior therapy from therapy_lines_count
# ---------------------------------------------------------------------------

class TestPriorTherapyFromLines:
    @pytest.mark.parametrize('lines, expected', [
        (0, 'None'),
        (1, 'One line'),
        (2, 'Two lines'),
        (3, 'More than two lines of therapy'),
        (5, 'More than two lines of therapy'),
    ])
    def test_maps_line_count(self, lines, expected):
        assert _normalize_ctomop_row(_row(therapy_lines_count=lines))['prior_therapy'] == expected


# ---------------------------------------------------------------------------
# Metastatic status
# ---------------------------------------------------------------------------

class TestMetastaticStatus:
    def test_positive_sets_true(self):
        assert _normalize_ctomop_row(_row(metastasis_status='Positive'))['metastatic_status'] is True

    def test_negative_sets_false(self):
        assert _normalize_ctomop_row(_row(metastasis_status='Negative'))['metastatic_status'] is False

    def test_unknown_does_not_add_key(self):
        result = _normalize_ctomop_row(_row(metastasis_status='Unknown'))
        assert 'metastatic_status' not in result


# ---------------------------------------------------------------------------
# Staging modality — "c → Clinical" → "c"
# ---------------------------------------------------------------------------

class TestStagingModality:
    def test_strips_arrow_notation(self):
        assert _normalize_ctomop_row(_row(staging_modalities='c → Clinical'))['staging_modalities'] == 'c'

    def test_no_arrow_passes_through(self):
        assert _normalize_ctomop_row(_row(staging_modalities='p'))['staging_modalities'] == 'p'


# ---------------------------------------------------------------------------
# Genetic mutations — rename 'mutation' → 'variant', lowercase gene, etc.
# ---------------------------------------------------------------------------

class TestGeneticMutationsNormalization:
    def test_renames_mutation_key_to_variant(self):
        row = _row(genetic_mutations=[{'gene': 'BRCA1', 'mutation': 'C61G>T'}])
        result = _normalize_ctomop_row(row)['genetic_mutations']
        assert 'mutation' not in result[0]
        assert result[0]['variant'] == 'c61g_t'

    def test_gt_symbol_replaced_in_variant(self):
        row = _row(genetic_mutations=[{'gene': 'TP53', 'mutation': 'C>T'}])
        assert _normalize_ctomop_row(row)['genetic_mutations'][0]['variant'] == 'c_t'

    def test_space_replaced_in_variant(self):
        row = _row(genetic_mutations=[{'gene': 'BRCA1', 'mutation': 'del exon 3'}])
        assert _normalize_ctomop_row(row)['genetic_mutations'][0]['variant'] == 'del_exon_3'

    def test_existing_variant_key_also_normalised(self):
        row = _row(genetic_mutations=[{'gene': 'BRCA2', 'variant': 'C>T transition'}])
        assert _normalize_ctomop_row(row)['genetic_mutations'][0]['variant'] == 'c_t_transition'

    def test_gene_lowercased(self):
        row = _row(genetic_mutations=[{'gene': 'TP53'}])
        assert _normalize_ctomop_row(row)['genetic_mutations'][0]['gene'] == 'tp53'

    def test_interpretation_lowercased_and_underscored(self):
        row = _row(genetic_mutations=[{'gene': 'brca1', 'interpretation': 'Pathogenic Variant'}])
        assert _normalize_ctomop_row(row)['genetic_mutations'][0]['interpretation'] == 'pathogenic_variant'

    def test_somatic_origin_kept(self):
        row = _row(genetic_mutations=[{'gene': 'tp53', 'origin': 'Somatic'}])
        assert _normalize_ctomop_row(row)['genetic_mutations'][0]['origin'] == 'somatic'

    def test_germline_origin_kept(self):
        row = _row(genetic_mutations=[{'gene': 'brca2', 'origin': 'Germline'}])
        assert _normalize_ctomop_row(row)['genetic_mutations'][0]['origin'] == 'germline'

    def test_unknown_origin_set_to_none(self):
        row = _row(genetic_mutations=[{'gene': 'brca1', 'origin': 'Unknown'}])
        assert _normalize_ctomop_row(row)['genetic_mutations'][0]['origin'] is None

    def test_empty_list_unchanged(self):
        assert _normalize_ctomop_row(_row(genetic_mutations=[]))['genetic_mutations'] == []

    def test_non_dict_items_passed_through(self):
        row = _row(genetic_mutations=['some_string'])
        assert _normalize_ctomop_row(row)['genetic_mutations'] == ['some_string']

    def test_non_string_gene_does_not_crash(self):
        # Defends .lower() against numeric/None values that slipped through CTOMOP
        row = _row(genetic_mutations=[{'gene': 1234, 'interpretation': None, 'origin': 42}])
        result = _normalize_ctomop_row(row)['genetic_mutations'][0]
        assert result['gene'] == 1234
        assert result['interpretation'] is None
        assert result['origin'] == 42


# ---------------------------------------------------------------------------
# Lab value fallbacks — CTOMOP renamed columns
# ---------------------------------------------------------------------------

class TestLabValueFallbacks:
    def test_hemoglobin_fallback(self):
        result = _normalize_ctomop_row(_row(hemoglobin_g_dl=12.5))
        assert result['hemoglobin_level'] == 12.5

    def test_hemoglobin_existing_value_not_overridden(self):
        result = _normalize_ctomop_row(_row(hemoglobin_level=11.0, hemoglobin_g_dl=12.5))
        assert result['hemoglobin_level'] == 11.0

    def test_anc_fallback_scaled_by_1000(self):
        result = _normalize_ctomop_row(_row(anc_thousand_per_ul=2.5))
        assert result['absolute_neutrophile_count'] == pytest.approx(2500.0)

    def test_anc_existing_not_overridden(self):
        result = _normalize_ctomop_row(_row(absolute_neutrophile_count=1800, anc_thousand_per_ul=2.5))
        assert result['absolute_neutrophile_count'] == 1800

    def test_alc_fallback_scaled_by_1000(self):
        result = _normalize_ctomop_row(_row(alc_thousand_per_ul=1.5))
        assert result['absolute_lymphocyte_count'] == pytest.approx(1500.0)

    def test_ldh_fallback(self):
        result = _normalize_ctomop_row(_row(ldh_u_l=300))
        assert result['lactate_dehydrogenase_level'] == 300

    def test_ldh_existing_not_overridden(self):
        result = _normalize_ctomop_row(_row(lactate_dehydrogenase_level=250, ldh_u_l=300))
        assert result['lactate_dehydrogenase_level'] == 250


# ---------------------------------------------------------------------------
# Gender from gender_source_value / gender_concept_id
# ---------------------------------------------------------------------------

class TestGenderNormalization:
    def test_m_source_value(self):
        assert _normalize_ctomop_row(_row(gender_source_value='M'))['gender'] == 'M'

    def test_f_source_value(self):
        assert _normalize_ctomop_row(_row(gender_source_value='F'))['gender'] == 'F'

    def test_male_prefix(self):
        assert _normalize_ctomop_row(_row(gender_source_value='male'))['gender'] == 'M'

    def test_female_prefix(self):
        assert _normalize_ctomop_row(_row(gender_source_value='female'))['gender'] == 'F'

    def test_concept_id_8507_male(self):
        assert _normalize_ctomop_row(_row(gender_concept_id=8507))['gender'] == 'M'

    def test_concept_id_8532_female(self):
        assert _normalize_ctomop_row(_row(gender_concept_id=8532))['gender'] == 'F'

    def test_existing_gender_not_overridden(self):
        result = _normalize_ctomop_row(_row(gender='F', gender_source_value='M'))
        assert result['gender'] == 'F'

    def test_non_string_gender_source_value_does_not_crash(self):
        # Defends .lower() against int gender_source_value (concept-id fallback used)
        result = _normalize_ctomop_row(_row(gender_source_value=8507, gender_concept_id=8507))
        assert result['gender'] == 'M'


# ---------------------------------------------------------------------------
# Receptor-status alias resolution — requires _build_code_lookup()
# Mocked so tests run without a live 'trials' DB alias.
# ---------------------------------------------------------------------------

_MOCK_LOOKUP = {
    'Her2Status': {
        'her2-':   'her2_minus',
        'her2+':   'her2_plus',
        'her2 low':'her2_low',
        # CTOMOP aliases injected by _build_code_lookup:
        'negative':  'her2_minus',
        'positive':  'her2_plus',
        'equivocal': 'her2_low',
    },
    'EstrogenReceptorStatus': {
        'er+/hi exp': 'er_plus_with_hi_exp',
        'er-':        'er_minus',
        # CTOMOP aliases:
        'positive':   'er_plus_with_hi_exp',
        'negative':   'er_minus',
        'borderline': 'er_plus_with_low_exp',
    },
    'ProgesteroneReceptorStatus': {
        'pr+/hi exp': 'pr_plus_with_hi_exp',
        'pr-':        'pr_minus',
        'positive':   'pr_plus_with_hi_exp',
        'negative':   'pr_minus',
        'borderline': 'pr_plus_with_low_exp',
    },
    'HrStatus': {
        'hr+':    'hr_plus',
        'hr-':    'hr_minus',
        'positive': 'hr_plus',
        'negative': 'hr_minus',
    },
    'HrdStatus': {
        'hrd positive': 'hrd_positive',
        'hrd negative': 'hrd_negative',
        'positive': 'hrd_positive',
        'negative': 'hrd_negative',
    },
    'HistologicType': {
        'invasive ductal carcinoma': 'idc',
        'invasive lobular carcinoma': 'ilc',
    },
    'Ethnicity': {
        'caucasian/white':           'caucasian_or_european',
        'white':                     'caucasian_or_european',
        'black/african-american':    'african_or_black',
        'black or african american': 'african_or_black',
        'hispanic or latino':        'other',
        'hispanic/latino':           'other',
    },
    # Other models — empty for these unit tests
    'Marker': {},
    'PlannedTherapy': {},
    'ConcomitantMedication': {},
    '_therapy': {},
}


@patch(
    'trials.management.commands.search_trials_for_patients._build_code_lookup',
    return_value=_MOCK_LOOKUP,
)
class TestReceptorStatusAliases:
    """
    Verify that CTOMOP display strings map to the correct EXACT codes via
    the alias injection in _build_code_lookup().
    """

    def test_her2_equivocal_maps_to_low(self, _mock):
        assert _normalize_ctomop_row(_row(her2_status='Equivocal'))['her2_status'] == 'her2_low'

    def test_her2_positive_maps_to_plus(self, _mock):
        assert _normalize_ctomop_row(_row(her2_status='Positive'))['her2_status'] == 'her2_plus'

    def test_her2_negative_maps_to_minus(self, _mock):
        assert _normalize_ctomop_row(_row(her2_status='Negative'))['her2_status'] == 'her2_minus'

    def test_er_positive_maps_to_hi_exp(self, _mock):
        result = _normalize_ctomop_row(_row(estrogen_receptor_status='Positive'))
        assert result['estrogen_receptor_status'] == 'er_plus_with_hi_exp'

    def test_er_borderline_maps_to_low_exp(self, _mock):
        result = _normalize_ctomop_row(_row(estrogen_receptor_status='Borderline'))
        assert result['estrogen_receptor_status'] == 'er_plus_with_low_exp'

    def test_er_negative(self, _mock):
        result = _normalize_ctomop_row(_row(estrogen_receptor_status='Negative'))
        assert result['estrogen_receptor_status'] == 'er_minus'

    def test_pr_positive_maps_to_hi_exp(self, _mock):
        result = _normalize_ctomop_row(_row(progesterone_receptor_status='Positive'))
        assert result['progesterone_receptor_status'] == 'pr_plus_with_hi_exp'

    def test_pr_negative(self, _mock):
        result = _normalize_ctomop_row(_row(progesterone_receptor_status='Negative'))
        assert result['progesterone_receptor_status'] == 'pr_minus'

    def test_hr_positive(self, _mock):
        assert _normalize_ctomop_row(_row(hr_status='Positive'))['hr_status'] == 'hr_plus'

    def test_hr_negative(self, _mock):
        assert _normalize_ctomop_row(_row(hr_status='Negative'))['hr_status'] == 'hr_minus'

    def test_unknown_receptor_value_resolves_to_none(self, _mock):
        # Values that don't appear in the lookup table resolve to None
        assert _normalize_ctomop_row(_row(her2_status='Indeterminate'))['her2_status'] is None

    def test_histologic_type_resolved(self, _mock):
        result = _normalize_ctomop_row(_row(histologic_type='Invasive Ductal Carcinoma'))
        assert result['histologic_type'] == 'idc'

    def test_ethnicity_hispanic_maps_to_other(self, _mock):
        result = _normalize_ctomop_row(_row(ethnicity='Hispanic or Latino'))
        assert result['ethnicity'] == 'other'

    def test_ethnicity_white_maps_to_caucasian(self, _mock):
        result = _normalize_ctomop_row(_row(ethnicity='White'))
        assert result['ethnicity'] == 'caucasian_or_european'
