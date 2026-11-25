"""Under OMOP therapy matching a PROMOP line is offered to the matcher by its regimen concept_id.

The matcher resolves a patient's regimens with `resolve_regimens`: legacy by CB `Therapy.code`,
OMOP by `Therapy.omop_concept_id`. PROMOP's `PatientRecord` names a line by its regimen name
("RVD"), which is neither, so before this change a PROMOP patient's regimens never resolved under
OMOP. Legacy mode must stay byte-identical.
"""
import pytest
from django.test import override_settings

from exact_matching.omop.therapy_graph import resolve_regimens
from exact_matching.patient_info.patient_info_attributes import PatientInfoAttributes
from exact_matching.patient_record import PatientRecordSurface
from tests.factories import TherapyFactory
from trials.services.patient_info.patient_info import PatientInfo

# Concept ids no seeded catalogue row carries, so the factory rows below are the only candidates.
RVD = 999000001
DARA_RD = 999000002
KD = 999000004


class Record:
    """A PROMOP row: unset columns are NULL, as on the real record."""

    def __init__(self, **fields):
        self.__dict__.update(fields)

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)
        return None


class HostSurface(PatientRecordSurface):
    _meta = PatientInfo._meta
    DERIVED_FIELDS_IN_SAVE = getattr(PatientInfo, 'DERIVED_FIELDS_IN_SAVE', ())


def _record():
    return Record(
        first_line_therapy='RVD', first_line_therapy_id=RVD,
        second_line_therapy='Some unresolved regimen', second_line_therapy_id=None,
        later_therapy='Dara-Rd',
        later_therapies=[
            {'lineNumber': 3, 'therapy': 'Dara-Rd', 'concept_id': DARA_RD},
            {'lineNumber': 4, 'therapy': 'Unknown', 'concept_id': None},
            {'lineNumber': 5, 'therapy': 'Free text line', 'concept_id': None},
            {'lineNumber': 6, 'therapy': 'Kd', 'concept_id': KD},   # reaches the matcher only as a row
        ],
        later_therapy_ids=[DARA_RD, KD],
    )


@override_settings(EXACT_OMOP_THERAPY=False)
def test_legacy_mode_still_sees_the_names():
    surface = PatientRecordSurface(_record())

    assert surface.first_line_therapy == 'RVD'
    assert surface.second_line_therapy == 'Some unresolved regimen'
    assert surface.later_therapy == 'Dara-Rd'
    assert surface.later_therapies == [
        {'lineNumber': 3, 'therapy': 'Dara-Rd', 'concept_id': DARA_RD},
        {'lineNumber': 4, 'therapy': None, 'concept_id': None},
        {'lineNumber': 5, 'therapy': 'Free text line', 'concept_id': None},
        {'lineNumber': 6, 'therapy': 'Kd', 'concept_id': KD},
    ]


@override_settings(EXACT_OMOP_THERAPY=True)
def test_omop_mode_offers_resolved_lines_by_concept_id_and_keeps_the_rest():
    surface = PatientRecordSurface(_record())

    assert surface.first_line_therapy == str(RVD)
    assert surface.second_line_therapy == 'Some unresolved regimen'   # no concept: still a line
    assert surface.later_therapy == str(DARA_RD)                     # its row's concept
    assert surface.later_therapies == [
        {'lineNumber': 3, 'therapy': str(DARA_RD), 'concept_id': DARA_RD},
        {'lineNumber': 4, 'therapy': None, 'concept_id': None},       # unknown stays no evidence
        {'lineNumber': 5, 'therapy': 'Free text line', 'concept_id': None},
        {'lineNumber': 6, 'therapy': str(KD), 'concept_id': KD},
    ]


@override_settings(EXACT_OMOP_THERAPY=True)
def test_an_unknown_line_is_no_evidence_even_with_a_concept():
    record = Record(first_line_therapy='Unknown', first_line_therapy_id=RVD)

    assert PatientRecordSurface(record).first_line_therapy is None


@override_settings(EXACT_OMOP_THERAPY=True)
def test_later_therapy_without_a_matching_row_keeps_its_name():
    record = Record(later_therapy='Dara-Rd', later_therapies=[{'therapy': 'Other', 'concept_id': DARA_RD}])

    assert PatientRecordSurface(record).later_therapy == 'Dara-Rd'


@pytest.mark.django_db
@pytest.mark.parametrize('omop, expected_codes', [
    (True, {'test_rvd', 'test_dara_rd', 'test_kd'}),
    (False, set()),      # legacy: PROMOP names are not CB codes, unchanged by this change
])
def test_the_matcher_resolves_a_promop_patients_regimens(omop, expected_codes):
    TherapyFactory(code='test_rvd', omop_concept_id=RVD)
    TherapyFactory(code='test_dara_rd', omop_concept_id=DARA_RD)
    TherapyFactory(code='test_kd', omop_concept_id=KD)
    TherapyFactory(code='test_unrelated', omop_concept_id=999000003)

    with override_settings(EXACT_OMOP_THERAPY=omop):
        therapies = PatientInfoAttributes(HostSurface(_record())).get_user_therapies()
        resolved = set(resolve_regimens(therapies).values_list('code', flat=True))

    assert resolved == expected_codes
