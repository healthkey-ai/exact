"""Under OMOP therapy the matcher reads a PROMOP patient's lines by their regimen concept_ids (cb#5554).

The matcher resolves a patient's regimens with `resolve_regimens`: legacy by CB `Therapy.code`,
OMOP by `Therapy.omop_concept_id`. PROMOP's `PatientRecord` names a line by its regimen name
("RVD"), which is neither, so before this change a PROMOP patient's regimens never resolved under
OMOP. The concept ids reach the matcher through `get_user_therapies` only: the surface's line
attributes stay names, because CB's patient views read them. Legacy mode is unchanged.
"""
import pytest
from django.test import override_settings

from exact_matching.omop.therapy_graph import resolve_regimens
from exact_matching.patient_info.patient_info_attributes import PatientInfoAttributes
from exact_matching.patient_record import PatientRecordSurface
from tests.factories import TherapyFactory, TrialFactory
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher

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


LATER_ROWS = [
    {'lineNumber': 3, 'therapy': 'Dara-Rd', 'concept_id': DARA_RD},
    {'lineNumber': 4, 'therapy': 'Unknown', 'concept_id': None},
    {'lineNumber': 5, 'therapy': 'Free text line', 'concept_id': None},
    {'lineNumber': 6, 'therapy': 'Kd', 'concept_id': KD},   # reaches the matcher only as a row
]


def _record(**overrides):
    fields = dict(
        prior_therapy='More than two lines of therapy',
        first_line_therapy='RVD', first_line_therapy_id=RVD,
        second_line_therapy='Some unresolved regimen', second_line_therapy_id=None,
        later_therapy='Dara-Rd',
        later_therapies=[dict(row) for row in LATER_ROWS],
        later_therapy_ids=[DARA_RD, KD],
    )
    fields.update(overrides)
    return Record(**fields)


@pytest.mark.parametrize('omop', [False, True])
def test_the_line_attributes_stay_names_in_both_modes(omop):
    with override_settings(EXACT_OMOP_THERAPY=omop):
        surface = PatientRecordSurface(_record())

        assert surface.first_line_therapy == 'RVD'
        assert surface.second_line_therapy == 'Some unresolved regimen'
        assert surface.later_therapy == 'Dara-Rd'
        assert [row['therapy'] for row in surface.later_therapies] == [
            'Dara-Rd', None, 'Free text line', 'Kd',
        ]


def test_identities_offer_resolved_lines_by_concept_id_and_keep_the_rest():
    identities = PatientRecordSurface(_record()).omop_regimen_identities()

    assert sorted(identities) == sorted([
        str(RVD), 'Some unresolved regimen', str(DARA_RD), 'Free text line', str(KD),
    ])


def test_an_unknown_line_is_no_evidence_even_with_a_concept():
    record = Record(first_line_therapy='Unknown', first_line_therapy_id=RVD)

    assert PatientRecordSurface(record).omop_regimen_identities() == []


@pytest.mark.parametrize('later, expected', [
    pytest.param(
        dict(later_therapies=[{'therapy': 'Dara-Rd'}, {'therapy': 'Kd'}], later_therapy_ids=[DARA_RD, KD]),
        [str(DARA_RD), str(KD)], id='rows-without-concept-key-align-with-ids'),
    pytest.param(
        dict(later_therapies=[{'therapy': 'Dara-Rd'}, {'therapy': 'Kd'}], later_therapy_ids=[DARA_RD]),
        ['Dara-Rd', 'Kd'], id='counts-disagree-so-nothing-aligns'),
    pytest.param(
        dict(later_therapies=None, later_therapy='Dara-Rd', later_therapy_ids=[DARA_RD, KD]),
        [str(DARA_RD), str(KD)], id='oldest-ids-only'),
    pytest.param(
        dict(later_therapies=None, later_therapy='Dara-Rd', later_therapy_ids=None),
        ['Dara-Rd'], id='flat-name-only'),
    pytest.param(
        dict(later_therapies=[{'therapy': 'Dara-Rd', 'concept_id': DARA_RD}, {'therapy': 'Kd'}],
             later_therapy_ids=[1, 2]),
        [str(DARA_RD), 'Kd'], id='per-row-key-wins-over-ids'),
])
def test_identities_read_every_later_therapies_shape_promop_stored(later, expected):
    record = Record(**later)

    assert PatientRecordSurface(record).omop_regimen_identities() == expected


@override_settings(EXACT_OMOP_THERAPY=False)
def test_legacy_get_user_therapies_still_reads_the_names():
    therapies = PatientInfoAttributes(HostSurface(_record())).get_user_therapies()

    assert sorted(therapies) == sorted(['RVD', 'Some unresolved regimen', 'Dara-Rd', 'Free text line', 'Kd'])


@override_settings(EXACT_OMOP_THERAPY=True)
def test_omop_get_user_therapies_reads_the_identities():
    therapies = PatientInfoAttributes(HostSurface(_record())).get_user_therapies()

    assert sorted(therapies) == sorted([
        str(RVD), 'Some unresolved regimen', str(DARA_RD), 'Free text line', str(KD),
    ])


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


@pytest.mark.django_db
@pytest.mark.parametrize('omop, excluded', [(True, True), (False, False)])
def test_a_trial_excluding_a_prior_regimen_excludes_the_promop_patient(omop, excluded):
    TherapyFactory(code='test_kd', omop_concept_id=KD)
    trial = TrialFactory(therapies_excluded=['test_kd'], omop_therapies_excluded=[str(KD)])

    with override_settings(EXACT_OMOP_THERAPY=omop):
        matcher = UserToTrialAttrMatcher(trial, HostSurface(_record()))
        verdict = matcher._match_therapy_related_things(matcher.patient_info_attr.get_user_therapies(), False)

    assert (verdict == 'not_matched') is excluded
