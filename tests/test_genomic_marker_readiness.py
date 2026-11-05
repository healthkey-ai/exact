"""Readiness audit for the typed marker envelopes (#5674)."""
import json
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db

TP53 = '35948926'
GAIN1Q21 = '2100007862'


def _term(concept_id):
    return {
        'kind': 'finding',
        'status': {'value': {'kind': 'string', 'value': 'present'}},
        'feature': {
            'domain_id': 'Measurement', 'concept_id': concept_id,
            'concept_code': 'x', 'vocabulary_id': 'OMOP Genomic',
            'concept_class_id': 'Genetic Variation',
        },
        'association': 'same_finding',
    }


def _envelope(*terms, unresolved=None):
    requirement = {
        'schema': 'cb-trial-criteria-1', 'role': 'required',
        'operator': 'any_of', 'terms': list(terms), 'unknown_is_negative': False,
    }
    if unresolved:
        requirement['unresolved_codes'] = list(unresolved)
    return requirement


def _run(*args):
    out = StringIO()
    call_command('check_omop_genomic_markers_readiness', *args, stdout=out)
    return out.getvalue()


def _json(*args):
    return json.loads(_run('--json', *args))


def test_a_valid_envelope_is_ok():
    TrialFactory(
        molecular_markers_required=['tp53Mutation'],
        omop_molecular_markers_required=[_envelope(_term(TP53))],
    )
    payload = _json()
    assert payload['statuses']['ok'] == 1


def test_a_legacy_only_trial_is_a_missing_envelope():
    TrialFactory(molecular_markers_required=['tp53Mutation'])
    payload = _json()
    assert payload['statuses']['missing_envelope'] == 1


def test_an_envelope_without_a_legacy_list_is_an_orphan():
    TrialFactory(
        molecular_markers_required=[],
        omop_molecular_markers_required=[_envelope(_term(TP53))],
    )
    payload = _json()
    assert payload['statuses']['orphan_envelope'] == 1


def test_a_malformed_envelope_is_reported_and_strict_fails():
    TrialFactory(
        molecular_markers_required=['tp53Mutation'],
        omop_molecular_markers_required=[{}],
    )
    payload = _json()
    assert payload['statuses']['malformed_envelope'] == 1
    with pytest.raises(CommandError):
        _run('--strict')


def test_an_unknown_unresolved_code_is_reported():
    TrialFactory(
        molecular_markers_required=['tp53Mutation'],
        omop_molecular_markers_required=[_envelope(_term(TP53), unresolved=['newCode'])],
    )
    payload = _json()
    assert payload['statuses']['unresolved_code'] == 1


def test_an_unmapped_concept_is_reported():
    TrialFactory(
        molecular_markers_required=['tp53Mutation'],
        omop_molecular_markers_required=[_envelope(_term('999999999'))],
    )
    payload = _json()
    assert payload['statuses']['unmapped_concept'] == 1


def test_a_required_plus_excluded_contradiction_is_reported():
    TrialFactory(
        molecular_markers_required=['tp53Mutation'],
        molecular_markers_excluded=['tp53Mutation'],
        omop_molecular_markers_required=[_envelope(_term(TP53))],
    )
    payload = _json()
    assert payload['statuses']['contradiction'] == 1


def test_a_known_unresolved_code_is_not_a_strict_problem():
    TrialFactory(
        molecular_markers_required=['bcmaExpression'],
        omop_molecular_markers_required=[
            _envelope(_term('35976963'), unresolved=['bcmaExpression'])
        ],
    )
    payload = _json()
    assert payload['statuses']['ok'] == 1


def test_a_known_concept_is_ok():
    TrialFactory(
        cytogenic_markers_required=['1q21Amplification'],
        omop_cytogenic_markers_required=[_envelope(_term(GAIN1Q21))],
    )
    payload = _json()
    assert payload['statuses']['ok'] == 1


def test_a_composite_envelope_is_known():
    TrialFactory(
        molecular_markers_required=['notch1or2Mutations'],
        omop_molecular_markers_required=[
            _envelope(_term('35947617'), _term('35960756'))
        ],
    )
    payload = _json()
    assert payload['statuses']['ok'] == 1


def test_an_excluded_only_legacy_trial_is_reported_separately():
    TrialFactory(molecular_markers_required=[], molecular_markers_excluded=['tp53Mutation'])
    payload = _json()
    assert payload['statuses']['missing_excluded_envelope'] == 1


def test_a_blank_string_is_not_a_missing_envelope():
    TrialFactory(molecular_markers_required=['tp53Mutation'],
                 omop_molecular_markers_required='')
    payload = _json()
    assert payload['statuses']['malformed_envelope'] == 1


def test_a_blank_typed_only_string_is_malformed_and_strict():
    TrialFactory(molecular_markers_required=[], omop_molecular_markers_required='')
    payload = _json()
    assert payload['statuses']['malformed_envelope'] == 1
    with pytest.raises(CommandError):
        _run('--strict')


def test_a_blank_unresolved_code_is_malformed():
    TrialFactory(
        molecular_markers_required=['tp53Mutation'],
        omop_molecular_markers_required=[
            {'schema': 'cb-trial-criteria-1', 'role': 'required',
             'operator': 'any_of', 'terms': [], 'unresolved_codes': [' ']},
        ],
    )
    payload = _json()
    assert payload['statuses']['malformed_envelope'] == 1


def test_a_no_requirement_trial_is_not_scanned():
    TrialFactory(molecular_markers_required=[], molecular_markers_excluded=[],
                 omop_molecular_markers_required=[])
    payload = _json()
    assert payload['scanned'] == 0


def test_an_unresolved_only_known_code_is_ok():
    TrialFactory(
        molecular_markers_required=['bcmaExpression'],
        omop_molecular_markers_required=[
            _envelope(unresolved=['bcmaExpression']),
        ],
    )
    payload = _json()
    assert payload['statuses']['ok'] == 1


def test_an_atm_composite_envelope_is_known():
    TrialFactory(
        molecular_markers_required=['atmOrAtrMutations'],
        omop_molecular_markers_required=[
            _envelope(_term('35960740'), _term('35963934'))
        ],
    )
    payload = _json()
    assert payload['statuses']['ok'] == 1


def test_strict_json_still_emits_json_and_fails():
    TrialFactory(
        molecular_markers_required=['tp53Mutation'],
        omop_molecular_markers_required=[{}],
    )
    out = StringIO()
    with pytest.raises(CommandError):
        call_command('check_omop_genomic_markers_readiness', '--json', '--strict', stdout=out)
    payload = json.loads(out.getvalue())
    assert payload['statuses']['malformed_envelope'] == 1
