"""
Tests for _normalize_ctomop_row() and related resolvers in search_trials_for_patients.

The function normalises raw CTOMOP patient_info rows into the value format
EXACT's matching engine expects.  Tests are split by concern:

* Pure-logic transforms (TNM, stage, grade, outcome, etc.) — no DB required.
* DB-backed alias tests (receptor statuses, ethnicity) — use mocked code-lookup
  so they run without a live 'trials' DB alias.
"""
from datetime import date
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

    def test_stage_i_unchanged(self):
        assert _normalize_ctomop_row(_row(stage='I'))['stage'] == 'I'


# ---------------------------------------------------------------------------
# Tumor grade — int (1-3) → EXACT code ('10', '20', '30'). Any other int
# (4+) collapses to None (#69): WHO FL grading is 1/2/3A/3B only, Grade 4
# is clinically invalid, and orphan codes leave the UI rendering blank.
# ---------------------------------------------------------------------------

class TestTumorGrade:
    @pytest.mark.parametrize('grade, expected', [(1, '10'), (2, '20'), (3, '30')])
    def test_int_to_code(self, grade, expected):
        assert _normalize_ctomop_row(_row(tumor_grade=grade))['tumor_grade'] == expected

    def test_grade_4_normalizes_to_none(self):
        """Regression for #69 / paired with #56: WHO FL grading is 1/2/3A/3B
        only — Grade 4 is clinically invalid. The previous mapping produced
        '40', which had no matching dropdown label after PR #66 removed it
        from value_options.tumor_grades, leaving the UI rendering blank."""
        assert _normalize_ctomop_row(_row(tumor_grade=4))['tumor_grade'] is None

    def test_unknown_int_grade_normalizes_to_none(self):
        # Grade 5 / 6 / etc. — anything outside the WHO 1-3 range — collapses
        # to None rather than producing an orphan code.
        assert _normalize_ctomop_row(_row(tumor_grade=5))['tumor_grade'] is None
        assert _normalize_ctomop_row(_row(tumor_grade=99))['tumor_grade'] is None

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

    def test_not_a_matching_attr(self):
        # #4121 catch-up: metastatic_status is still a normalized PatientInfo field
        # (above), but it was removed as a trial-matching attr (redundant with
        # stage / distant_metastasis_stage) — it must not be in the mapping.
        from trials.services.patient_info.configs import USER_TO_TRIAL_ATTRS_MAPPING
        assert 'metastatic_status' not in USER_TO_TRIAL_ATTRS_MAPPING


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


# ---------------------------------------------------------------------------
# Lab value fallbacks — CTOMOP renamed columns
# ---------------------------------------------------------------------------

class TestLabValueFallbacks:
    """The four labs that live in two columns each.

    CTOMOP's writable-fields descriptor splits them: for ANC and LDH the
    EXACT-named column is a read-only `alias` whose `canonical` is the
    CTOMOP-named one, and for haemoglobin and ALC both are `direct` and
    `writable`. That split is what these tests are about — the first two
    follow the canonical, the second two keep whatever is already there.
    """

    def test_hemoglobin_fallback(self):
        result = _normalize_ctomop_row(_row(hemoglobin_g_dl=12.5))
        assert result['hemoglobin_level'] == 12.5

    def test_hemoglobin_existing_value_not_overridden(self):
        # Both columns are writable here and EXACT's own editor writes this
        # one, so a value in it is the reader's and must not be replaced.
        result = _normalize_ctomop_row(_row(hemoglobin_level=11.0, hemoglobin_g_dl=12.5))
        assert result['hemoglobin_level'] == 11.0

    def test_anc_fallback_scaled_by_1000(self):
        result = _normalize_ctomop_row(_row(anc_thousand_per_ul=2.5))
        assert result['absolute_neutrophile_count'] == pytest.approx(2500.0)

    def test_anc_follows_the_canonical_column_even_when_the_mirror_is_set(self):
        # CTOMOP calls `absolute_neutrophile_count` a read-only mirror of
        # `anc_thousand_per_ul`, so a value in it is either older than the
        # canonical or copied from it — never fresher. Taking it kept the
        # patient's previous count after every write to the real one (#557).
        result = _normalize_ctomop_row(_row(absolute_neutrophile_count=1800, anc_thousand_per_ul=2.5))
        assert result['absolute_neutrophile_count'] == pytest.approx(2500.0)

    def test_anc_is_not_read_in_the_units_of_the_other_column(self):
        # What CTOMOP's mirroring actually writes: a COPY, so the mirror
        # holds 2.5 where its own unit is cells/µL. Read as it stands, this
        # patient has 2.5 neutrophils per microlitre and fails every ANC
        # floor a trial can name, by three orders of magnitude.
        result = _normalize_ctomop_row(_row(absolute_neutrophile_count=2.5, anc_thousand_per_ul=2.5))
        assert result['absolute_neutrophile_count'] == pytest.approx(2500.0)

    def test_anc_keeps_a_mirror_that_is_all_there_is(self):
        # A patient whose value predates the split has only the mirror, and
        # it is in its own unit already.
        result = _normalize_ctomop_row(_row(absolute_neutrophile_count=1800))
        assert result['absolute_neutrophile_count'] == 1800

    def test_alc_fallback_scaled_by_1000(self):
        result = _normalize_ctomop_row(_row(alc_thousand_per_ul=1.5))
        assert result['absolute_lymphocyte_count'] == pytest.approx(1500.0)

    def test_alc_existing_not_overridden(self):
        # ALC is the pair CTOMOP has NOT declared: both columns writable,
        # nothing mirroring, so the value already there stands.
        result = _normalize_ctomop_row(_row(absolute_lymphocyte_count=1200, alc_thousand_per_ul=1.5))
        assert result['absolute_lymphocyte_count'] == 1200

    def test_ldh_fallback(self):
        result = _normalize_ctomop_row(_row(ldh_u_l=300))
        assert result['lactate_dehydrogenase_level'] == 300

    def test_ldh_follows_the_canonical_column(self):
        # Declared a mirror like ANC, and in the same unit — so this is
        # about staleness rather than scale, but it is the same rule.
        result = _normalize_ctomop_row(_row(lactate_dehydrogenase_level=250, ldh_u_l=300))
        assert result['lactate_dehydrogenase_level'] == 300

    def test_ldh_keeps_a_mirror_that_is_all_there_is(self):
        result = _normalize_ctomop_row(_row(lactate_dehydrogenase_level=250))
        assert result['lactate_dehydrogenase_level'] == 250

    # ── The values as they actually arrive ────────────────────────────
    #
    # CTOMOP serialises a DecimalField as a STRING. Measured on the live
    # payload the host posts to `/normalize-ctomop-row/`:
    #
    #     "anc_thousand_per_ul": "2.5", "absolute_neutrophile_count": "2.50"
    #
    # which matters because `'2.5' * 1000` is not 2500.

    def test_a_string_canonical_is_a_number_and_not_repeated_text(self):
        # `'2.5' * 1000` is three thousand characters of "2.52.52.5…". The
        # in-memory builder then drops it as unparseable, so the patient
        # reached the matcher with NO neutrophil count and every trial that
        # names one read as "unknown" for them — silently.
        result = _normalize_ctomop_row(_row(anc_thousand_per_ul='2.5'))
        assert result['absolute_neutrophile_count'] == pytest.approx(2500)
        assert not isinstance(result['absolute_neutrophile_count'], str)

    def test_a_string_mirror_is_recognised_as_present(self):
        # The fill-if-empty pairs read the EXACT-named column to decide
        # whether to fill it. `'11.0'` is a value, not an absence.
        result = _normalize_ctomop_row(_row(hemoglobin_level='11.0', hemoglobin_g_dl='12.5'))
        assert result['hemoglobin_level'] == '11.0'

    def test_a_blank_canonical_does_not_erase_the_mirror(self):
        # A blank arrives through JSON where a decimal is optional. Read as
        # present it would overwrite a usable value with `'' * 1000`, which
        # is the empty string.
        result = _normalize_ctomop_row(
            _row(absolute_neutrophile_count=1800, anc_thousand_per_ul='')
        )
        assert result['absolute_neutrophile_count'] == 1800

    def test_a_blank_mirror_is_filled_from_the_canonical(self):
        result = _normalize_ctomop_row(_row(hemoglobin_level='', hemoglobin_g_dl='12.5'))
        assert result['hemoglobin_level'] == pytest.approx(12.5)

    def test_running_twice_does_not_scale_twice(self):
        # The module's own contract: "Idempotent." A second pass must not
        # read its own output as a new canonical value.
        once = _normalize_ctomop_row(_row(anc_thousand_per_ul='2.5'))
        twice = _normalize_ctomop_row(dict(once))
        assert twice['absolute_neutrophile_count'] == pytest.approx(2500)

    def test_a_canonical_that_is_not_a_finite_number_leaves_the_mirror_alone(self):
        # `Decimal('NaN')` and `Decimal('Infinity')` CONSTRUCT, so a parse
        # that only catches exceptions lets them through. A NaN then reaches
        # DRF's renderer, which refuses to emit it and answers 500 where the
        # row used to normalise fine; through the non-HTTP path it is quieter
        # and worse, because every threshold comparison against NaN is False
        # and the patient fails criteria nobody can see them failing.
        for spelling in ('NaN', 'nan', 'Infinity', 'inf', '-Infinity'):
            result = _normalize_ctomop_row(
                _row(absolute_neutrophile_count=1800, anc_thousand_per_ul=spelling)
            )
            assert result['absolute_neutrophile_count'] == 1800, spelling

    def test_a_signalling_nan_does_not_raise_out_of_the_function(self):
        # `Decimal('sNaN')` constructs too, so it survives the parse — and
        # then raises on the first arithmetic, which is outside it.
        result = _normalize_ctomop_row(
            _row(absolute_neutrophile_count=1800, anc_thousand_per_ul='sNaN')
        )
        assert result['absolute_neutrophile_count'] == 1800

    def test_a_float_nan_is_ignored_too(self):
        # Not only the string spelling: Python's own `json.loads` accepts a
        # bare `NaN` literal, so a body that names one arrives here as a
        # float rather than as text.
        for junk in (float('nan'), float('inf'), float('-inf')):
            result = _normalize_ctomop_row(
                _row(absolute_neutrophile_count=1800, anc_thousand_per_ul=junk)
            )
            assert result['absolute_neutrophile_count'] == 1800, junk

    def test_an_integer_too_big_for_a_float_does_not_raise(self):
        # JSON carries an integer of any length, and `math.isfinite` converts
        # to float before answering — so asking it about a 309-digit integer
        # raises `OverflowError` rather than returning True.
        huge = 10 ** 309
        result = _normalize_ctomop_row(_row(anc_thousand_per_ul=huge))
        assert result['absolute_neutrophile_count'] == huge * 1000

    def test_a_canonical_that_is_not_a_number_at_all_is_ignored(self):
        # A list is the string bug in another type: `[1] * 1000`. A date
        # raises instead. Neither is a lab value.
        for junk in ([1], {'a': 1}, date(2026, 1, 1), True, False):
            result = _normalize_ctomop_row(
                _row(absolute_neutrophile_count=1800, anc_thousand_per_ul=junk)
            )
            assert result['absolute_neutrophile_count'] == 1800, junk

    def test_zero_in_the_exact_column_is_a_reading_and_not_an_absence(self):
        # `not row.get(...)` counted 0 as empty, so a lymphocyte count of
        # zero — which a patient can have — was replaced by the other column.
        result = _normalize_ctomop_row(
            _row(absolute_lymphocyte_count=0, alc_thousand_per_ul=1.5)
        )
        assert result['absolute_lymphocyte_count'] == 0

    def test_an_unparseable_exact_column_gives_way_to_the_other_one(self):
        # `'unknown'` or a censored `'<0.5'` is not a number, and the builder
        # drops it later anyway — leaving the patient with nothing where the
        # CTOMOP column had a usable value.
        result = _normalize_ctomop_row(
            _row(hemoglobin_level='unknown', hemoglobin_g_dl='12.5')
        )
        assert result['hemoglobin_level'] == pytest.approx(12.5)

    def test_running_twice_does_not_scale_twice_for_the_filled_pairs(self):
        # The second pass reads a Decimal out of the column the first pass
        # wrote, which must read as "already has a value".
        once = _normalize_ctomop_row(_row(alc_thousand_per_ul='1.5'))
        twice = _normalize_ctomop_row(dict(once))
        assert twice['absolute_lymphocyte_count'] == pytest.approx(1500)

    def test_string_canonicals_for_the_other_two_pairs(self):
        alc = _normalize_ctomop_row(_row(alc_thousand_per_ul='1.5'))
        assert alc['absolute_lymphocyte_count'] == pytest.approx(1500)
        ldh = _normalize_ctomop_row(_row(ldh_u_l='300'))
        assert ldh['lactate_dehydrogenase_level'] == pytest.approx(300)

    def test_a_canonical_that_names_nothing_leaves_the_mirror_alone(self):
        result = _normalize_ctomop_row(
            _row(absolute_neutrophile_count=1800, anc_thousand_per_ul='not a number')
        )
        assert result['absolute_neutrophile_count'] == 1800


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

    def test_omop_concept_name_female(self):
        # HTTP path sends the OMOP concept name 'Female' (concept_id 8532).
        assert _normalize_ctomop_row(_row(gender='Female'))['gender'] == 'F'

    def test_omop_concept_name_male(self):
        assert _normalize_ctomop_row(_row(gender='Male'))['gender'] == 'M'

    def test_omop_concept_name_case_insensitive(self):
        assert _normalize_ctomop_row(_row(gender='FEMALE'))['gender'] == 'F'
        assert _normalize_ctomop_row(_row(gender=' male '))['gender'] == 'M'

    def test_coded_gender_is_idempotent(self):
        assert _normalize_ctomop_row(_row(gender='F'))['gender'] == 'F'
        assert _normalize_ctomop_row(_row(gender='M'))['gender'] == 'M'

    def test_full_word_gender_wins_over_source_value(self):
        # A populated full-word gender is translated unconditionally; the
        # empty-gender source-value fallback never overrides it.
        result = _normalize_ctomop_row(_row(gender='Female', gender_source_value='M'))
        assert result['gender'] == 'F'

    def test_unknown_gender_mapped_to_blank(self):
        # Non-binary OMOP concepts have no EXACT code; blank (None) so the
        # matcher treats gender as unknown instead of excluding the patient.
        assert _normalize_ctomop_row(_row(gender='Unknown'))['gender'] is None
        assert _normalize_ctomop_row(_row(gender='Ambiguous'))['gender'] is None
        assert _normalize_ctomop_row(_row(gender='Other'))['gender'] is None

    def test_unmapped_concept_id_mapped_to_blank(self):
        # An unrecognized concept id in `gender` is also blanked, not left as int.
        assert _normalize_ctomop_row(_row(gender=99999))['gender'] is None

    def test_empty_string_gender_falls_back_to_concept_id(self):
        # An empty-string gender is blanked, then recovered from the concept id.
        result = _normalize_ctomop_row(_row(gender='', gender_concept_id=8532))
        assert result['gender'] == 'F'

    def test_unknown_gender_falls_back_to_source_value(self):
        # Blanking an unrecognized name lets the empty-gender fallback recover
        # a value from gender_source_value when one is present.
        result = _normalize_ctomop_row(_row(gender='Unknown', gender_source_value='M'))
        assert result['gender'] == 'M'

    def test_bare_concept_id_in_gender_field(self):
        # The endpoint may put the OMOP concept id directly in `gender`.
        assert _normalize_ctomop_row(_row(gender=8507))['gender'] == 'M'
        assert _normalize_ctomop_row(_row(gender=8532))['gender'] == 'F'


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
    'trials.services.patient_info.ctomop_adapter._build_code_lookup',
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
