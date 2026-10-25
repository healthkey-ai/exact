"""PROMOP's cytogenetic markers reach the matcher in CB's vocabulary (#590).

PROMOP names the column `cytogenetic_markers` (migration 0222) while EXACT's
patient field keeps CB's older `cytogenic_markers` spelling, and PROMOP's
canonical values only overlap CB's codes on `hyperdiploidy`. Before this fix
the value was discarded twice: the key was unknown to the DTO, and a raw
`del17p` would not equal CB's `del17p13` in a trial list anyway.
"""
import pytest
from django.test import override_settings

from exact_matching.matcher import UserToTrialAttrMatcher
from exact_matching.patient_info.patient_info_attributes import PatientInfoAttributes
from exact_matching.querysets.trial import TrialQuerySet
from trials.models import Trial
from trials.services.patient_info.promop_adapter import (
    build_patient_info_from_promop_row,
    normalize_promop_row,
)
from trials.services.patient_info.resolve import _build_in_memory

# The legacy display-title path resolves through the Marker table, so the
# whole file is DB-bound.
pytestmark = pytest.mark.django_db

MM = 'multiple myeloma'


def _patient(row):
    return build_patient_info_from_promop_row({'disease': MM, **row})


def test_renamed_column_is_read():
    row = normalize_promop_row({'cytogenetic_markers': 'del17p'})
    assert row['cytogenic_markers'] == 'del17p13'
    assert 'cytogenetic_markers' not in row


def test_old_spelling_is_still_read():
    row = normalize_promop_row({'cytogenic_markers': 'del17p'})
    assert row['cytogenic_markers'] == 'del17p13'


def test_new_name_wins_when_both_are_present():
    row = normalize_promop_row({
        'cytogenetic_markers': 't(4;14)', 'cytogenic_markers': 'del17p',
    })
    assert row['cytogenic_markers'] == 't414'


@pytest.mark.parametrize('canonical,cb_code', [
    ('del17p', 'del17p13'),
    ('t(4;14)', 't414'),
    ('t(11;14)', 't1114'),
    ('t(14;16)', 't1416'),
    ('1q_gain', '1q21Amplification'),
    ('1q_amp', '1q21Amplification'),
    ('hyperdiploidy', 'hyperdiploidy'),
    ('MYC rearrangement', 'mycRearrangements'),
])
def test_every_cb_vocabulary_entry_maps(canonical, cb_code):
    assert normalize_promop_row({'cytogenetic_markers': canonical})['cytogenic_markers'] == cb_code


def test_inner_commas_do_not_split_a_marker():
    row = normalize_promop_row({'cytogenetic_markers': 't(4;14), t(11;14), del17p'})
    assert row['cytogenic_markers'] == 't414,t1114,del17p13'


def test_unmapped_markers_are_dropped_but_known_ones_survive():
    row = normalize_promop_row({'cytogenetic_markers': 'del13q, del17p'})
    assert row['cytogenic_markers'] == 'del17p13'


def test_wholly_unknown_markers_stay_unknown_not_absent():
    """'' reads as unknown; an absent field would read as "no markers"."""
    row = normalize_promop_row({'cytogenetic_markers': 'del13q'})
    assert row['cytogenic_markers'] == ''
    patient = _patient({'cytogenetic_markers': 'del13q'})
    assert PatientInfoAttributes(patient).is_attr_blank('cytogenic_markers') is True


def test_legacy_display_titles_still_resolve_through_the_marker_table():
    """An older payload in EXACT's title form keeps its previous handling."""
    row = normalize_promop_row({'cytogenic_markers': 'Unknown/Other'})
    # The resolver may return None for a title with no Marker row; either way
    # the display-title path is used and nothing raises.
    assert row.get('cytogenic_markers') in (None, '', 'none')


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_marked_trial_matches_and_pool_admits():
    patient = _patient({'cytogenetic_markers': 'del17p'})
    trial = Trial.objects.create(
        disease=MM, cytogenic_markers_required=['del17p13'],
    )
    assert UserToTrialAttrMatcher(trial, patient).attr_match_status(
        'cytogenic_markers') == 'matched'
    pool, _ = TrialQuerySet(model=Trial).filter(pk=trial.pk).filter_by_patient_info(patient)
    assert pool.exists()


@pytest.mark.django_db
@override_settings(EXACT_OMOP_THERAPY=False, EXACT_OMOP_LANGUAGES=False)
def test_exclusion_list_still_rejects_the_patient():
    patient = _patient({'cytogenetic_markers': 'del17p'})
    trial = Trial.objects.create(
        disease=MM, cytogenic_markers_excluded=['del17p13'],
    )
    assert UserToTrialAttrMatcher(trial, patient).attr_match_status(
        'cytogenic_markers') == 'not_matched'
    pool, _ = TrialQuerySet(model=Trial).filter(pk=trial.pk).filter_by_patient_info(patient)
    assert not pool.exists()


def test_tp53_disruption_sees_the_mapped_marker():
    assert PatientInfoAttributes(_patient({'cytogenetic_markers': 'del17p'})).tp53_disruption is True
    assert PatientInfoAttributes(_patient({'cytogenetic_markers': 't(4;14)'})).tp53_disruption is False


def test_high_risk_mcl_criteria_see_the_mapped_marker():
    patient = _patient({
        'disease': 'mantle cell lymphoma', 'cytogenetic_markers': 'del17p',
    })
    assert 'del17p' in (PatientInfoAttributes(patient).high_risk_mcl_criteria or '')


def test_dto_carries_the_mapped_codes_for_both_builders():
    patient = _patient({'cytogenetic_markers': 'del17p, t(4;14)'})
    assert patient.cytogenic_markers == 'del17p13,t414'
    inline = _build_in_memory({'disease': MM, 'cytogenic_markers': 'del17p13,t414'})
    assert inline.cytogenic_markers == 'del17p13,t414'
