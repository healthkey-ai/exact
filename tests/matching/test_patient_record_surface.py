"""The package-owned PROMOP `PatientRecord` surface (#5523).

These pin the behaviour CB's `trials/matching/promop_surface.py` used to own: aliases,
unit rescales, vocabulary translation, derived-property routing, the therapy-unknown
filter and the pre-existing-condition cache seeding. The host-supplied parts (schema and
geo point) are pinned as explicit seams so a later move cannot quietly reintroduce a
`trials.*` import.

No database: the surface is a duck-typed wrapper over any object, which is exactly how
the matcher uses it.
"""
from types import SimpleNamespace

import pytest

from exact_matching.patient_record import PatientRecordSurface


class Schema:
    """Stand-in for the host `PatientInfo` class the matcher reads schema off."""

    DERIVED_FIELDS_IN_SAVE = ('mipi_risk',)


class BoundSurface(PatientRecordSurface):
    _meta = 'host-meta'
    DERIVED_FIELDS_IN_SAVE = Schema.DERIVED_FIELDS_IN_SAVE


class Record:
    """A PROMOP row: every column exists, unset ones are NULL (`None`).

    `SimpleNamespace` raises on a missing column, but the real record returns None,
    and the field maps read the `*_units` companions whether or not the value is set.
    """

    def __init__(self, **fields):
        self.__dict__.update(fields)

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        return None


def _record(**fields):
    return Record(**fields)


def test_the_package_ships_no_schema():
    # The matcher reads `patient.__class__._meta`; leaving it unset would otherwise
    # look like a working surface until the first blankness check.
    assert PatientRecordSurface._meta is None
    assert PatientRecordSurface.DERIVED_FIELDS_IN_SAVE == ()


def test_a_host_subclass_supplies_the_schema():
    assert BoundSurface._meta == 'host-meta'
    assert BoundSurface.DERIVED_FIELDS_IN_SAVE == ('mipi_risk',)


def test_geo_point_is_passed_through_not_derived():
    point = object()

    assert PatientRecordSurface(_record(), geo_point=point).geo_point is point
    assert PatientRecordSurface(_record()).geo_point is None


def test_cbc_counts_scale_from_the_thousand_per_ul_variant():
    surface = PatientRecordSurface(_record(wbc_count_thousand_per_ul=7.5, anc_thousand_per_ul=4.2))

    assert surface.white_blood_cell_count == 7500
    assert surface.absolute_neutrophile_count == 4200
    assert surface.white_blood_cell_count_units == 'CELLS/UL'
    assert surface.absolute_neutrophile_count_units == 'CELLS/UL'


def test_a_direct_cb_parity_value_wins_over_the_canonical_fallback():
    surface = PatientRecordSurface(_record(
        hemoglobin_level=11.0, hemoglobin_g_dl=99.0, hemoglobin_level_units='g/dL',
        serum_calcium_level=9.1, serum_calcium_mg_dl=99.0, serum_calcium_level_units='mg/dL',
    ))

    assert surface.hemoglobin_level == 11.0
    assert surface.hemoglobin_level_units == 'g/dL'
    assert surface.serum_calcium_level == 9.1
    assert surface.serum_calcium_level_units == 'mg/dL'


def test_the_canonical_column_is_the_fallback_and_keeps_its_unit():
    surface = PatientRecordSurface(_record(hemoglobin_g_dl=10.6, serum_creatinine_mg_dl=1.1))

    assert surface.hemoglobin_level == 10.6
    assert surface.serum_creatinine_level == 1.1
    assert surface.serum_creatinine_level_units == 'mg/dL'


def test_cytogenetic_markers_are_translated_to_cb_codes():
    surface = PatientRecordSurface(_record(cytogenetic_markers='t(4;14), del17p, del13q'))

    assert surface.cytogenic_markers == 't414,del17p13'


def test_an_unrecognised_cytogenetic_value_reads_as_no_markers():
    assert PatientRecordSurface(_record(cytogenetic_markers='del13q')).cytogenic_markers == ''


def test_the_older_column_name_still_reads():
    assert PatientRecordSurface(_record(cytogenic_markers='hyperdiploidy')).cytogenic_markers == 'hyperdiploidy'


def test_a_type_incompatible_field_reads_as_unknown():
    # PROMOP stores text; CB's matcher iterates structured dicts and would crash.
    surface = PatientRecordSurface(_record(supportive_therapies='bisphosphonates'))

    assert surface.supportive_therapies is None


def test_an_unknown_therapy_marker_is_not_a_regimen():
    assert PatientRecordSurface(_record(first_line_therapy='Unknown')).first_line_therapy is None
    assert PatientRecordSurface(_record(first_line_therapy='VRd')).first_line_therapy == 'VRd'


def test_later_therapies_drop_the_unknown_composition_row_but_keep_the_line():
    rows = [
        {'therapy': 'Unknown', 'line': 2},
        {'therapy': 'VRd', 'line': 3},
    ]

    assert PatientRecordSurface(_record(later_therapies=rows)).later_therapies == [
        {'therapy': None, 'line': 2},
        {'therapy': 'VRd', 'line': 3},
    ]


def test_pre_existing_condition_codes_seed_the_matcher_memo():
    surface = PatientRecordSurface(_record(preexisting_conditions=['diabetes']))

    assert surface._pre_existing_condition_codes_cache == ['diabetes']


def test_recorded_conditions_mask_a_contradictory_no_conditions_flag():
    surface = PatientRecordSurface(_record(
        preexisting_conditions=['diabetes'], no_pre_existing_conditions=True,
    ))

    assert surface.no_pre_existing_conditions is None


def test_no_conditions_stands_when_no_category_was_recorded():
    surface = PatientRecordSurface(_record(
        preexisting_conditions=[], no_pre_existing_conditions=True,
    ))

    assert surface.no_pre_existing_conditions is True


def test_a_derived_attribute_runs_the_package_engine():
    from exact_matching.patient_info.patient_info_attributes import PatientInfoAttributes

    surface = PatientRecordSurface(_record(
        patient_age=70, ecog_performance_status=1, lactate_dehydrogenase_level=200,
        white_blood_cell_count=7.0, absolute_neutrophile_count=4.0, platelet_count=200000,
    ))

    expected = PatientInfoAttributes(surface).get_value('mipi_risk')
    assert surface.mipi_risk == expected


def test_an_injected_attributes_engine_is_used_instead_of_the_default():
    class Engine:
        def __init__(self, patient):
            self.patient = patient

        @property
        def mipi_risk(self):
            return 'injected'

    surface = PatientRecordSurface(_record(), attributes_factory=Engine)

    assert surface.mipi_risk == 'injected'


@pytest.mark.parametrize('crab, slim, expected', [
    (True, None, 'active'),
    (None, True, 'active'),
    (False, False, 'smoldering'),
    (False, None, None),
])
def test_progression_is_derived_from_crab_and_slim_when_the_record_has_none(crab, slim, expected):
    surface = PatientRecordSurface(_record(meets_crab=crab, meets_slim=slim, progression=None))

    assert surface.progression == expected


def test_a_populated_label_wins_over_the_crab_derivation():
    # The record value is authoritative; a non-equivalent label must not silently
    # fall back to the derivation just because CRAB is set.
    surface = PatientRecordSurface(_record(progression='Relapsed', meets_crab=True))

    assert surface.progression == 'Relapsed'


def test_a_populated_progression_wins_over_the_derivation():
    surface = PatientRecordSurface(_record(meets_crab=True, progression='smoldering'))

    assert surface.progression == 'smoldering'


def test_patient_age_is_unknown_when_derived_from_the_unknown_dob_sentinel():
    record = _record(patient_age=64, person=SimpleNamespace(year_of_birth=1900))

    assert PatientRecordSurface(record).patient_age is None


def test_patient_age_is_kept_when_the_date_of_birth_is_real():
    record = _record(patient_age=64, person=SimpleNamespace(year_of_birth=1962))

    assert PatientRecordSurface(record).patient_age == 64


def test_an_attribute_the_record_does_not_carry_reads_as_unknown():
    assert PatientRecordSurface(_record()).meets_lugano is None


def test_hepatic_adequacy_is_unknown_not_a_recursion_when_inputs_are_missing():
    # The surface routes the null back into the engine, whose "could not work it out"
    # branch asks whether the caller named it. Reading that through the surface
    # re-enters the derivation and recursed until Python's limit.
    surface = PatientRecordSurface(_record(patient_age=70))

    assert surface.hepatic_adequacy_status is None


def test_explicitly_unknown_reads_provenance_instead_of_the_derivation():
    from exact_matching.patient_info.patient_info_attributes import explicitly_unknown

    class Surface:
        _provided_fields = frozenset({'hepatic_adequacy_status'})
        asked_for_value = False

        def _provenance_value(self, name):
            return None

        def __getattr__(self, name):
            type(self).asked_for_value = True
            raise AssertionError(f're-entered the derivation for {name}')

    surface = Surface()

    assert explicitly_unknown(surface, 'hepatic_adequacy_status') is True
    assert surface.asked_for_value is False


def test_private_probes_still_raise_so_the_matcher_memo_stays_honest():
    # A Django model raises AttributeError for an unknown column, and the surface must
    # pass that through: the matcher probes `hasattr(patient, '_cache')` before memoizing.
    surface = PatientRecordSurface(SimpleNamespace())

    with pytest.raises(AttributeError):
        surface._cache


@pytest.mark.parametrize('stored, expected', [
    ('Not Refractory', 'notRefractory'),
    ('notRefractory', 'notRefractory'),
    ('Primary Refractory', 'primaryRefractory'),
    ('primaryRefractory', 'primaryRefractory'),
    ('Secondary Refractory', 'secondaryRefractory'),
    ('Multi-Refractory', 'multiRefractory'),
    ('multiRefractory', 'multiRefractory'),
    ('Not Refractory (progression halted)', 'notRefractory'),
    ('Refractory', None),
    ('Unknown', None),
    ('unknown', None),
    ('', None),
    (None, None),
    ('Some future label', None),
])
def test_refractory_status_labels_become_codes_never_a_refractory_assertion(stored, expected):
    # PROMOP stores the label; the matcher treats any unrecognised non-empty value
    # as refractory, so the adapter must map or null it (#5552).
    surface = PatientRecordSurface(_record(treatment_refractory_status=stored))

    assert surface.treatment_refractory_status == expected


@pytest.mark.parametrize('stored, expected', [
    ('Active', 'active'),
    ('active', 'active'),
    ('Smoldering', 'smoldering'),
    ('smoldering', 'smoldering'),
    ('  ACTIVE  ', 'active'),
    ('SmolDering', 'smoldering'),
    ('', ''),
    # Non-strings (inline JSON) pass through; the domain guard rejects them.
    (0, 0),
    (False, False),
    (None, None),
    # Different concepts, not spelling variants: leave them; the exact domain
    # membership then reports them as unanswered.
    ('Stable Disease', 'Stable Disease'),
    ('Relapsed', 'Relapsed'),
])
def test_progression_labels_map_to_codes_and_other_states_are_left_alone(stored, expected):
    surface = PatientRecordSurface(_record(progression=stored))

    assert surface.progression == expected


@pytest.mark.parametrize('field, stored, expected', [
    # PROMOP's LOINC answer labels.
    ('estrogen_receptor_status', 'Positive', 'er_plus'),
    ('estrogen_receptor_status', 'Negative', 'er_minus'),
    ('estrogen_receptor_status', 'POSITIVE', 'er_plus'),
    ('estrogen_receptor_status', 'Equivocal', None),
    ('estrogen_receptor_status', 'Some future label', None),
    ('estrogen_receptor_status', '', None),
    ('estrogen_receptor_status', None, None),
    ('progesterone_receptor_status', 'Positive', 'pr_plus'),
    ('progesterone_receptor_status', 'Negative', 'pr_minus'),
    ('progesterone_receptor_status', 'Equivocal', None),
    # A CB-authored record already carries the code, including expression levels.
    ('estrogen_receptor_status', 'er_plus_with_hi_exp', 'er_plus_with_hi_exp'),
    ('progesterone_receptor_status', 'pr_plus_with_low_exp', 'pr_plus_with_low_exp'),
    ('her2_status', 'Positive', 'her2_plus'),
    ('her2_status', 'Negative', 'her2_minus'),
    ('her2_status', 'HER2 low', 'her2_low'),
    ('her2_status', 'her2_low', 'her2_low'),
    # Equivocal is IHC 2+ without amplification, not "HER2 low" (#5553).
    ('her2_status', 'Equivocal', None),
    ('hr_status', 'HR+', 'hr_plus'),
    ('hr_status', 'HR-', 'hr_minus'),
    ('hr_status', 'hr_plus_with_hi_exp', 'hr_plus_with_hi_exp'),
    ('hr_status', 'Positive', 'hr_plus'),
    ('hr_status', 'Negative', 'hr_minus'),
])
def test_receptor_labels_become_codes_and_equivocal_stays_unknown(field, stored, expected):
    surface = PatientRecordSurface(_record(**{field: stored}))

    assert getattr(surface, field) == expected


@pytest.mark.parametrize('field, stored, expected', [
    ('tumor_stage', 'T2: Invasive Tumor > 2 – 5 cm', 't2'),
    ('tumor_stage', 'T1mi: Invasive Tumor ≤ 0.1 cm', 't1mi'),
    ('tumor_stage', 'Tx: Primary Tumor, cannot be assessed', 'tx'),
    ('tumor_stage', 't2', 't2'),
    ('tumor_stage', 'T5: not an option', None),
    ('tumor_stage', '', None),
    ('tumor_stage', None, None),
    ('nodes_stage', 'N1: 1–3 axillary lymph nodes or small internal mammary nodes', 'n1'),
    ('nodes_stage', 'N3c: Supraclavicular nodes', 'n3c'),
    ('nodes_stage', 'n0', 'n0'),
    ('nodes_stage', 'N4: not an option', None),
    # The token belongs to a different axis; it must not match by accident.
    ('nodes_stage', 'T2: Invasive Tumor', None),
    ('distant_metastasis_stage', 'M0: No distant metastasis', 'm0'),
    ('distant_metastasis_stage', 'M0(i+): No metastasis on scans, …', 'm0(i_plus)'),
    ('distant_metastasis_stage', 'm0(i_plus)', 'm0(i_plus)'),
    ('distant_metastasis_stage', 'M1: Distant metastasis present', 'm1'),
    ('distant_metastasis_stage', 'M2: not an option', None),
])
def test_tnm_titles_and_codes_become_the_loader_code(field, stored, expected):
    surface = PatientRecordSurface(_record(**{field: stored}))

    assert getattr(surface, field) == expected


@pytest.mark.parametrize('stored, expected', [
    ('1', '10'),
    ('2', '20'),
    ('3', '30'),
    ('3A', '31'),
    ('3B', '32'),
    (2, '20'),
    ('Grade 2', '20'),
    ('10', '10'),
    ('31', '31'),
    ('4', None),
    ('Unknown', None),
    ('', None),
    (None, None),
])
def test_fl_tumor_grade_uses_exacts_numeric_codes(stored, expected):
    surface = PatientRecordSurface(_record(tumor_grade=stored))

    assert surface.tumor_grade == expected


@pytest.mark.parametrize('stored, expected', [
    ('Infiltrating ductal carcinoma (IDC)', 'infiltrating_ductal_carcinoma'),
    ('infiltrating ductal carcinoma (idc)', 'infiltrating_ductal_carcinoma'),
    ('Ductal carcinoma in situ (DCIS)', 'dcis'),
    ('Mixed ductal and lobular carcinoma', 'mixed_ductal_and_lobular_carcinoma'),
    ('Paget disease of the nipple', 'paget_disease_of_the_nipple'),
    ('infiltrating_ductal_carcinoma', 'infiltrating_ductal_carcinoma'),
    # A non-BC histology has no BC trial code; it stays unknown.
    ('Follicular lymphoma, grade 2', None),
    ('', None),
    (None, None),
])
def test_histology_descriptions_become_exacts_codes(stored, expected):
    surface = PatientRecordSurface(_record(histologic_type=stored))

    assert surface.histologic_type == expected


@pytest.mark.parametrize('stored, expected', [
    ('Pre-menopausal', 'pre_menopausal'),
    ('pre_menopausal', 'pre_menopausal'),
    ('premenopausal', 'pre_menopausal'),
    ('pre menopausal', 'pre_menopausal'),
    ('Post-menopausal', 'post_menopausal'),
    ('postmenopausal', 'post_menopausal'),
    ('POST MENOPAUSAL', 'post_menopausal'),
    # EXACT has no code for this state, and neither label is an honest answer.
    ('Peri-menopausal', None),
    ('Unknown', None),
    ('', None),
    (None, None),
])
def test_menopausal_labels_map_only_to_the_two_known_states(stored, expected):
    surface = PatientRecordSurface(_record(menopausal_status=stored))

    assert surface.menopausal_status == expected
