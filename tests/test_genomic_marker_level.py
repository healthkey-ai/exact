"""Marker-level evaluation against the existing typed trial envelopes (#5674)."""
from types import SimpleNamespace

import pytest

from exact_matching.omop.genomic_markers import genomic_marker_state
from exact_matching.omop.marker_concepts import (
    COMPOSITE_MARKER_CODES,
    MARKER_CONCEPTS,
    UNRESOLVED_MARKER_CODES,
    marker_concept_ids,
)
from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db

TP53 = '35948926'
KRAS = '35948791'
ATM = '35960740'
ATR = '35963934'
CD38 = '35976963'
MYC = '19643476'
DEL17P = '19641187'
GAIN1Q21 = '2100007862'
HYPERDIPLOIDY = '4208087'
COMPLEX_KARYOTYPE = '36032861'
BCMA = 'bcmaExpression'


def _term(concept_id, kind='finding', status='present'):
    if kind == 'assertion':
        return {
            'kind': 'assertion', 'state': 'encoded_by_concept',
            'concept': {
                'domain_id': 'Observation', 'concept_id': concept_id,
                'concept_code': 'x', 'vocabulary_id': 'SNOMED',
                'concept_class_id': 'Morph Abnormality',
            },
        }
    return {
        'kind': 'finding',
        'status': {
            'value': {'kind': 'string', 'value': status},
            'question': {
                'unit': '', 'domain_id': 'Measurement', 'concept_id': '40772043',
                'omop_table': 'measurement', 'value_kind': 'string',
                'concept_code': '69548-6', 'source_value': 'genomics:status',
                'vocabulary_id': 'LOINC', 'concept_class_id': 'Lab Test',
            },
        },
        'feature': {
            'domain_id': 'Measurement', 'concept_id': concept_id,
            'concept_code': 'x', 'vocabulary_id': 'OMOP Genomic',
            'concept_class_id': 'Genetic Variation',
        },
        'association': 'same_finding',
    }


def _hk_term(concept_id):
    term = _term(concept_id)
    term['feature'].update({
        'vocabulary_id': 'HK-Genomics', 'concept_class_id': 'Lab Test',
    })
    return term


def _envelope(*terms, operator='any_of', role='required',
              unknown_is_negative=False, unresolved_codes=None):
    requirement = {
        'schema': 'cb-trial-criteria-1', 'role': role, 'operator': operator,
        'terms': list(terms), 'unknown_is_negative': unknown_is_negative,
    }
    if unresolved_codes:
        requirement['unresolved_codes'] = list(unresolved_codes)
    return requirement


def _trial(*, molecular=(), cytogenic=(), typed_molecular=None,
           typed_cytogenic=None, legacy_molecular=()):
    data = {
        'molecular_markers_required': list(legacy_molecular or molecular),
        'cytogenic_markers_required': list(cytogenic),
    }
    if typed_molecular is not None:
        data['omop_molecular_markers_required'] = typed_molecular
    if typed_cytogenic is not None:
        data['omop_cytogenic_markers_required'] = typed_cytogenic
    return SimpleNamespace(**data)


def _state(trial, field, markers, is_blank=False):
    return genomic_marker_state(trial, field, markers, is_blank)


class TestTheCodeToConceptTable:
    def test_every_resolved_code_has_a_concept(self):
        for code, concepts in MARKER_CONCEPTS.items():
            assert concepts, code
            assert all(str(cid).isdigit() for cid in concepts), code

    def test_the_observed_identities(self):
        assert MARKER_CONCEPTS['tp53Mutation'] == (TP53,)
        assert MARKER_CONCEPTS['mycRearrangements'] == (MYC,)
        assert MARKER_CONCEPTS['1q21'] == (GAIN1Q21,)
        assert MARKER_CONCEPTS['1q21Amplification'] == (GAIN1Q21,)
        assert MARKER_CONCEPTS['hyperdiploidy'] == (HYPERDIPLOIDY,)
        assert COMPOSITE_MARKER_CODES == {'atmOrAtrMutations', 'notch1or2Mutations'}
        assert MARKER_CONCEPTS['complexKaryotype'] == (COMPLEX_KARYOTYPE,)
        assert MARKER_CONCEPTS['bcmaExpression'] == ('2100007900',)
        assert marker_concept_ids(['1q21Amplification', 'tp53Mutation']) == {
            GAIN1Q21, TP53,
        }

    def test_bcma_has_a_local_concept_but_the_envelope_is_unresolved(self):
        assert BCMA in UNRESOLVED_MARKER_CODES
        assert MARKER_CONCEPTS[BCMA] == ('2100007900',)


class TestTheMarkerState:
    def test_no_requirement_is_a_match(self):
        assert _state(_trial(), 'molecular_markers', 'tp53Mutation') == 'matched'

    def test_a_blank_patient_is_unknown(self):
        trial = _trial(typed_molecular=[_envelope(_term(TP53))])
        assert _state(trial, 'molecular_markers', None) == 'unknown'
        assert _state(trial, 'molecular_markers', '') == 'unknown'

    def test_unknown_is_negative_is_honoured(self):
        trial = _trial(typed_molecular=[
            _envelope(_term(TP53), unknown_is_negative=True),
        ])
        assert _state(trial, 'molecular_markers', None) == 'not_matched'

    def test_a_present_marker_matches_its_concept(self):
        trial = _trial(typed_molecular=[_envelope(_term(TP53))])
        assert _state(trial, 'molecular_markers', ['tp53Mutation']) == 'matched'
        assert _state(trial, 'molecular_markers', ['krasMutation']) == 'not_matched'

    def test_an_or_marker_matches_either_gene(self):
        trial = _trial(
            legacy_molecular=['atmOrAtrMutations'],
            typed_molecular=[_envelope(_term(ATM), _term(ATR))],
        )
        assert _state(trial, 'molecular_markers', ['atmOrAtrMutations']) == 'matched'
        assert _state(trial, 'molecular_markers', ['tp53Mutation']) == 'not_matched'

    def test_a_composite_patient_code_does_not_prove_a_specific_gene(self):
        trial = _trial(
            legacy_molecular=['atmMutation'],
            typed_molecular=[_envelope(_term(ATM))],
        )
        assert _state(trial, 'molecular_markers', ['atmOrAtrMutations']) == 'not_matched'
        assert _state(trial, 'molecular_markers', ['tp53Mutation']) == 'not_matched'

    def test_a_specific_patient_code_satisfies_a_composite_trial(self):
        trial = _trial(
            legacy_molecular=['notch1or2Mutations'],
            typed_molecular=[_envelope(_term('35947617'), _term('35960756'))],
        )
        assert _state(trial, 'molecular_markers', ['notch1Mutation']) == 'matched'
        assert _state(trial, 'molecular_markers', ['notch1or2Mutations']) == 'matched'

    def test_a_composite_patient_code_does_not_satisfy_a_specific_trial(self):
        trial = _trial(
            legacy_molecular=['notch1Mutation'],
            typed_molecular=[_envelope(_term('35947617'))],
        )
        assert _state(trial, 'molecular_markers', ['notch1or2Mutations']) == 'not_matched'
        assert _state(trial, 'molecular_markers', ['notch1Mutation']) == 'matched'

    def test_excluded_all_of_requires_every_unresolved_code(self):
        trial = _trial(typed_molecular=[_envelope(
            operator='all_of', role='excluded', unresolved_codes=['a', 'b'])])
        assert _state(trial, 'molecular_markers', ['a']) == 'matched'
        assert _state(trial, 'molecular_markers', ['a', 'b']) == 'not_matched'

    def test_a_composite_is_not_credited_across_two_requirements(self):
        trial = _trial(
            legacy_molecular=['notch1or2Mutations'],
            typed_molecular=[
                _envelope(_term('35947617')),
                _envelope(_term('35960756')),
            ],
        )
        assert _state(
            trial, 'molecular_markers', ['notch1or2Mutations'],
        ) == 'not_matched'
        # Conservative: a two-requirement envelope is AND, and the current
        # slice cannot split a composite into its members.
        assert _state(trial, 'molecular_markers', ['notch1Mutation']) == 'not_matched'

    def test_all_of_applies_to_unresolved_codes_too(self):
        trial = _trial(
            typed_molecular=[_envelope(
                operator='all_of', unresolved_codes=['a', 'b'])],
        )
        assert _state(trial, 'molecular_markers', ['a']) == 'not_matched'
        assert _state(trial, 'molecular_markers', ['a', 'b']) == 'matched'

    def test_an_assertion_marker_matches_the_snomed_concept(self):
        trial = _trial(
            cytogenic=['hyperdiploidy'],
            typed_cytogenic=[_envelope(_term(HYPERDIPLOIDY, kind='assertion'))],
        )
        assert _state(trial, 'cytogenic_markers', ['hyperdiploidy']) == 'matched'
        assert _state(trial, 'cytogenic_markers', ['del17p13']) == 'not_matched'

    def test_an_unresolved_code_falls_back_to_the_canonical_string(self):
        trial = _trial(
            typed_molecular=[_envelope(_term(CD38), unresolved_codes=[BCMA])],
        )
        assert _state(trial, 'molecular_markers', ['cd38Expression']) == 'matched'
        assert _state(trial, 'molecular_markers', [BCMA]) == 'matched'
        assert _state(trial, 'molecular_markers', ['tp53Mutation']) == 'not_matched'

    def test_an_exclusion_only_trial_still_restricts(self):
        trial = SimpleNamespace(
            molecular_markers_required=[],
            molecular_markers_excluded=['tp53Mutation'],
            omop_molecular_markers_required=[],
            cytogenic_markers_required=[],
            cytogenic_markers_excluded=[],
            omop_cytogenic_markers_required=[],
        )
        assert _state(trial, 'molecular_markers', ['tp53Mutation']) == 'not_matched'
        assert _state(trial, 'molecular_markers', ['krasMutation']) == 'matched'

    def test_a_legacy_exclusion_still_applies_when_the_envelope_exists(self):
        """Trial 24917 carries tp53Mutation as both required and excluded.

        The typed envelope only compiles the required side; the legacy
        exclusion is the remaining restriction and must not be silently
        dropped into a match. (18489 is the excluded-only variant, covered
        separately below.)
        """
        trial = SimpleNamespace(
            molecular_markers_required=['tp53Mutation'],
            molecular_markers_excluded=['tp53Mutation'],
            omop_molecular_markers_required=[_envelope(_term(TP53))],
            cytogenic_markers_required=[],
            cytogenic_markers_excluded=[],
            omop_cytogenic_markers_required=[],
        )
        assert _state(trial, 'molecular_markers', ['tp53Mutation']) == 'not_matched'

    def test_a_malformed_role_or_operator_falls_back(self):
        for bad in (
            {'schema': 'cb-trial-criteria-1', 'role': [],
             'operator': 'any_of', 'terms': [_term(TP53)]},
            {'schema': 'cb-trial-criteria-1', 'role': 'required',
             'operator': {}, 'terms': [_term(TP53)]},
        ):
            trial = _trial(legacy_molecular=['tp53Mutation'], typed_molecular=[bad])
            assert _state(trial, 'molecular_markers', ['tp53Mutation']) == 'matched'

    def test_a_malformed_status_does_not_raise(self):
        bad = _term(TP53)
        bad['status'] = 'broken'
        trial = _trial(
            legacy_molecular=['tp53Mutation'], typed_molecular=[_envelope(bad)],
        )
        assert _state(trial, 'molecular_markers', ['tp53Mutation']) == 'matched'

    def test_a_non_list_typed_value_is_malformed(self):
        trial = SimpleNamespace(
            molecular_markers_required=['tp53Mutation'],
            molecular_markers_excluded=[],
            omop_molecular_markers_required={},
            cytogenic_markers_required=[],
            cytogenic_markers_excluded=[],
            omop_cytogenic_markers_required=[],
        )
        assert _state(trial, 'molecular_markers', ['tp53Mutation']) == 'matched'
        assert _state(trial, 'molecular_markers', []) == 'unknown'
        bare = SimpleNamespace(
            molecular_markers_required=[],
            molecular_markers_excluded=[],
            omop_molecular_markers_required={},
            cytogenic_markers_required=[],
            cytogenic_markers_excluded=[],
            omop_cytogenic_markers_required=[],
        )
        assert _state(bare, 'molecular_markers', []) == 'not_matched'

    def test_a_malformed_envelope_falls_back_to_the_legacy_list(self):
        trial = _trial(
            legacy_molecular=['tp53Mutation'],
            typed_molecular=[{}],
        )
        assert _state(trial, 'molecular_markers', ['tp53Mutation']) == 'matched'
        assert _state(trial, 'molecular_markers', ['krasMutation']) == 'not_matched'

    def test_a_broken_feature_falls_back_without_raising(self):
        broken = {
            'schema': 'cb-trial-criteria-1', 'role': 'required',
            'operator': 'any_of',
            'terms': [{'kind': 'finding', 'feature': 'broken'}],
        }
        trial = _trial(
            legacy_molecular=['tp53Mutation'],
            typed_molecular=[broken],
        )
        assert _state(trial, 'molecular_markers', ['tp53Mutation']) == 'matched'
        assert _state(trial, 'molecular_markers', ['krasMutation']) == 'not_matched'

    def test_a_malformed_typed_only_envelope_fails_closed(self):
        trial = _trial(typed_molecular=[{}])
        assert _state(trial, 'molecular_markers', ['tp53Mutation']) == 'not_matched'

    def test_a_requirement_with_no_terms_is_malformed(self):
        empty_requirement = {
            'schema': 'cb-trial-criteria-1', 'role': 'required',
            'operator': 'any_of', 'terms': [],
        }
        trial = _trial(
            legacy_molecular=['tp53Mutation'],
            typed_molecular=[empty_requirement],
        )
        assert _state(trial, 'molecular_markers', ['tp53Mutation']) == 'matched'
        assert _state(
            _trial(typed_molecular=[empty_requirement]),
            'molecular_markers', ['tp53Mutation'],
        ) == 'not_matched'

    def test_a_recompiled_bcma_envelope_matches_the_local_concept(self):
        trial = _trial(
            legacy_molecular=['bcmaExpression'],
            typed_molecular=[_envelope(_hk_term('2100007900'))],
        )
        assert _state(trial, 'molecular_markers', ['bcmaExpression']) == 'matched'

    def test_the_legacy_list_is_used_when_the_envelope_is_empty(self):
        trial = _trial(molecular=['tp53Mutation'])
        assert _state(trial, 'molecular_markers', ['tp53Mutation']) == 'matched'
        assert _state(trial, 'molecular_markers', ['krasMutation']) == 'not_matched'
        assert _state(trial, 'molecular_markers', []) == 'unknown'

    def test_an_excluded_envelope_rejects_the_match(self):
        trial = _trial(typed_molecular=[
            _envelope(_term(TP53), role='excluded'),
            _envelope(_term(KRAS)),
        ])
        assert _state(trial, 'molecular_markers', ['tp53Mutation']) == 'not_matched'
        assert _state(trial, 'molecular_markers', ['krasMutation']) == 'matched'

    def test_all_of_requires_every_term(self):
        trial = _trial(typed_molecular=[
            _envelope(_term(TP53), _term(KRAS), operator='all_of'),
        ])
        assert _state(trial, 'molecular_markers', ['tp53Mutation']) == 'not_matched'
        assert _state(
            trial, 'molecular_markers', ['tp53Mutation', 'krasMutation'],
        ) == 'matched'

    def test_an_unknown_status_does_not_assert_presence(self):
        trial = _trial(typed_molecular=[
            _envelope(_term(TP53, status='unknown')),
        ])
        assert _state(trial, 'molecular_markers', ['tp53Mutation']) == 'not_matched'


class TestTheSearchPath:
    def _trial(self, **fields):
        return TrialFactory(**fields)

    def test_the_typed_envelope_is_used(self, settings):
        settings.EXACT_OMOP_GENOMICS = True
        self._trial(
            molecular_markers_required=['tp53Mutation'],
            omop_molecular_markers_required=[_envelope(_term(TP53))],
        )
        self._trial(
            molecular_markers_required=['krasMutation'],
            omop_molecular_markers_required=[_envelope(_term(KRAS))],
        )
        assert list(
            Trial.objects.all().eligible_for_molecular_marker(['tp53Mutation'])
            .values_list('molecular_markers_required', flat=True)
        ) == [['tp53Mutation']]

    def test_a_malformed_envelope_does_not_crash_or_drop_legacy_matches(self, settings):
        settings.EXACT_OMOP_GENOMICS = True
        self._trial(
            molecular_markers_required=['tp53Mutation'],
            omop_molecular_markers_required=[{}],
        )
        assert Trial.objects.all().eligible_for_molecular_marker(
            ['tp53Mutation']).count() == 1
        assert Trial.objects.all().eligible_for_molecular_marker(
            ['krasMutation']).count() == 0

    def test_an_empty_patient_set_does_not_narrow(self, settings):
        settings.EXACT_OMOP_GENOMICS = True
        self._trial(
            molecular_markers_required=['tp53Mutation'],
            omop_molecular_markers_required=[_envelope(_term(TP53))],
        )
        assert Trial.objects.all().eligible_for_molecular_marker([]).count() == 1

    def test_the_local_gain_1q21_concept_matches_both_spellings(self, settings):
        settings.EXACT_OMOP_GENOMICS = True
        self._trial(
            cytogenic_markers_required=['1q21Amplification'],
            omop_cytogenic_markers_required=[_envelope(_term(GAIN1Q21))],
        )
        assert Trial.objects.all().eligible_for_cytogenic_markers(
            ['1q21Amplification']).count() == 1
        assert Trial.objects.all().eligible_for_cytogenic_markers(
            ['tp53Mutation']).count() == 0

    def test_a_composite_patient_code_does_not_match_a_specific_trial(self, settings):
        settings.EXACT_OMOP_GENOMICS = True
        self._trial(
            molecular_markers_required=['notch1Mutation'],
            omop_molecular_markers_required=[_envelope(_term('35947617'))],
        )
        assert Trial.objects.all().eligible_for_molecular_marker(
            ['notch1or2Mutations']).count() == 0
        assert Trial.objects.all().eligible_for_molecular_marker(
            ['notch1Mutation']).count() == 1

    def test_a_specific_patient_code_matches_a_composite_trial(self, settings):
        settings.EXACT_OMOP_GENOMICS = True
        self._trial(
            molecular_markers_required=['notch1or2Mutations'],
            omop_molecular_markers_required=[
                _envelope(_term('35947617'), _term('35960756'))],
        )
        assert Trial.objects.all().eligible_for_molecular_marker(
            ['notch1Mutation']).count() == 1
        assert Trial.objects.all().eligible_for_molecular_marker(
            ['notch1or2Mutations']).count() == 1

    def test_the_flag_off_keeps_the_legacy_columns(self, settings):
        settings.EXACT_OMOP_GENOMICS = False
        self._trial(
            molecular_markers_required=['tp53Mutation'],
            omop_molecular_markers_required=[_envelope(_term(TP53))],
        )
        assert Trial.objects.all().eligible_for_molecular_marker(
            ['tp53Mutation']).count() == 1
        assert Trial.objects.all().eligible_for_molecular_marker(
            ['krasMutation']).count() == 0


class TestThePerTrialMatcher:
    def _trial(self, **fields):
        return TrialFactory(**fields)

    def _verdict(self, trial, markers):
        patient = PatientInfo(disease='multiple myeloma', molecular_markers=markers)
        return UserToTrialAttrMatcher(
            trial, patient).attr_match_status('molecular_markers')

    def test_a_matching_marker_is_matched(self, settings):
        settings.EXACT_OMOP_GENOMICS = True
        trial = self._trial(
            molecular_markers_required=['tp53Mutation'],
            omop_molecular_markers_required=[_envelope(_term(TP53))],
        )
        assert self._verdict(trial, 'tp53Mutation') == 'matched'
        assert self._verdict(trial, 'krasMutation') == 'not_matched'

    def test_a_blank_marker_summary_is_unknown(self, settings):
        settings.EXACT_OMOP_GENOMICS = True
        trial = self._trial(
            molecular_markers_required=['tp53Mutation'],
            omop_molecular_markers_required=[_envelope(_term(TP53))],
        )
        assert self._verdict(trial, '') == 'unknown'

    def test_the_legacy_path_still_answers(self, settings):
        settings.EXACT_OMOP_GENOMICS = False
        trial = self._trial(molecular_markers_required=['tp53Mutation'])
        assert self._verdict(trial, 'tp53Mutation') == 'matched'
        assert self._verdict(trial, 'krasMutation') == 'not_matched'
