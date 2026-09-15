"""The PRomop aggregate must survive adaptation into EXACT matching."""
import pytest

from tests.factories import TrialFactory
from trials.models import Trial
from trials.services.patient_info.ctomop_adapter import build_patient_info_from_ctomop_row
from trials.services.patient_info.normalize import normalize_patient_info
from trials.api.patient_info_serializers import PatientInfoSerializer
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes
from trials.services.patient_info.resolve import _build_in_memory
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('key', ['tp53_disruption', 'tp53Disruption'])
@pytest.mark.parametrize('value', [None, True, False])
def test_inline_aggregate_survives_repeated_normalization(key, value):
    pi = _build_in_memory({'disease': 'chronic lymphocytic leukemia', key: value})
    normalize_patient_info(pi)
    assert pi.tp53_disruption is value
    assert PatientInfoAttributes(pi).tp53_disruption is value


@pytest.mark.parametrize('value', [None, True, False])
def test_source_row_preserves_explicit_aggregate(value):
    pi = build_patient_info_from_ctomop_row({'disease': 'chronic lymphocytic leukemia', 'tp53_disruption': value})
    assert pi.tp53_disruption is value
    assert PatientInfoAttributes(pi).tp53_disruption is value


@pytest.mark.parametrize('value', ['false', 0, 1, {}, []])
def test_non_boolean_aggregate_from_upstream_falls_back_to_the_derivation(value):
    """A value we cannot read is not an aggregate, so no provenance is kept.

    It used to raise `ValidationError`, and that had three consequences worth
    avoiding. `_build_in_memory` is shared: the CTOMOP adapter and four
    management commands reach it, so the raise turned a malformed UPSTREAM row
    into a client 400 — `trials_views._resolve_patient_info` re-raises
    `APIException` unchanged, while its own comment says a person_id-path
    failure must surface as 500 "rather than masking it as a misleading 400".
    In a batch command it was not a 400 at all but a traceback. And it broke
    the contract this function documents and implements next door, where
    `_coerce_dates`, `_coerce_numerics` and `_coerce_json_fields` all accept
    loose input and the module docstring says CancerBot depends on it.

    Falling back is also exactly what these values did BEFORE the aggregate
    was honoured at all, because `normalize` overwrote the field regardless —
    so this is the status quo, not a new leniency. Refusing a malformed
    aggregate is reasonable, but it belongs in the serializer layer, where
    the caller is known and 400 is the right answer.
    """
    pi = _build_in_memory({'tp53_disruption': value, 'molecular_markers': 'tp53Mutation'})
    assert pi.tp53_disruption is True
    assert not hasattr(pi, '_provided_tp53_disruption')


@pytest.mark.parametrize('markers', [
    {'cytogenic_markers': 'del17p13'},
    {'molecular_markers': 'del17p13'},
    {'molecular_markers': 'tp53Mutation'},
    {'p53_ihc': 60},
])
def test_explicit_null_does_not_discard_positive_evidence(markers):
    """An unresolved aggregate cannot retract findings in the same payload.

    This is the case the original 19 did not cover, and it is the one that
    mattered: a patient with documented del17p13 / tp53Mutation / p53_ihc >= 50
    went from `True` to `None` as soon as the source also sent
    `tp53_disruption: null`. Against a trial that EXCLUDES TP53-disrupted
    patients that is `not_matched` becoming `unknown` — the trial stops being
    filtered out and is offered as a candidate.

    Per the PR description the aggregate is `true` or `null`, so `null` is the
    source saying it did not compute the value, not a clinician saying the
    status is unknown. Treating the two as the same is what produced the
    inversion.
    """
    pi = _build_in_memory(dict(markers, tp53_disruption=None))
    assert pi.tp53_disruption is True
    assert PatientInfoAttributes(pi).tp53_disruption is True


@pytest.mark.parametrize('markers', [
    {'cytogenic_markers': 'del17p13'},
    {'molecular_markers': 'tp53Mutation'},
    {'p53_ihc': 60},
])
def test_an_explicit_false_against_positive_markers_is_a_contradiction(markers):
    """`False` over positive evidence yields unknown, not a confirmed negative.

    An earlier revision of this file asserted that the explicit value simply
    won, on the reasoning that a caller asserting something is different from a
    caller having nothing. That reasoning does not survive the facts:
    `tp53_disruption` is a field EXACT DERIVES, and
    `PatientInfoSerializer.to_representation` emits it verbatim. Measured — a
    record with no markers serialises `tp53_disruption: False`, so a caller
    that reads a patient record and posts it back carries EXACT's own
    derivation on the wire. Under the old rule that echo silenced the markers.

    `None` dominates `False` in both directions of harm. Against a trial that
    EXCLUDES TP53-disrupted patients, `False` reads as `matched` and offers the
    trial outright; against one that REQUIRES it, `False` reads as
    `not_matched` and denies a patient whose own markers qualify them. `None`
    reads as `unknown` in both, which leaves the question open instead of
    answering it wrongly.
    """
    pi = _build_in_memory(dict(markers, tp53_disruption=False))
    assert pi.tp53_disruption is None
    assert PatientInfoAttributes(pi).tp53_disruption is None


def test_an_explicit_false_still_confirms_a_negative_with_no_contrary_evidence():
    """No contradiction, so the explicit value stands — this is the ordinary
    case and it must not become unknown."""
    assert _build_in_memory({'tp53_disruption': False}).tp53_disruption is False
    assert _build_in_memory({'tp53_disruption': False,
                             'molecular_markers': ''}).tp53_disruption is False


def test_the_serializer_echo_no_longer_silences_the_markers():
    """The round trip that motivated the rule, end to end."""
    derived = PatientInfoSerializer(
        _build_in_memory({'molecular_markers': ''})
    ).data['tp53_disruption']
    assert derived is False, 'fixture assumes the derivation returns a False to echo'

    echoed = _build_in_memory({'molecular_markers': 'tp53Mutation',
                               'tp53_disruption': derived})
    assert echoed.tp53_disruption is None


def test_explicit_null_still_means_unknown_when_there_is_no_evidence():
    """The point of the original change, unchanged: absent any marker, an
    explicit null yields unknown rather than the `False` the derivation would
    otherwise return."""
    assert _build_in_memory({'tp53_disruption': None}).tp53_disruption is None
    assert _build_in_memory({'tp53_disruption': None,
                             'molecular_markers': ''}).tp53_disruption is None


def test_omitted_aggregate_keeps_existing_legacy_marker_derivation():
    assert _build_in_memory({'molecular_markers': 'tp53Mutation'}).tp53_disruption is True
    assert _build_in_memory({'molecular_markers': ''}).tp53_disruption is False


@pytest.mark.parametrize('restriction', ['tp53_disruption_required', 'tp53_disruption_excluded'])
def test_unknown_is_not_a_confirmed_eligibility_match(restriction):
    pi = _build_in_memory({'disease': 'chronic lymphocytic leukemia', 'tp53_disruption': None})
    trial = TrialFactory(disease='chronic lymphocytic leukemia', **{restriction: True})
    assert UserToTrialAttrMatcher(trial, pi).attr_match_status('tp53_disruption') == 'unknown'
    # Unknown stays a candidate, with the criterion explicitly unresolved.
    assert Trial.objects.eligible_for_tp53_disruption(pi.tp53_disruption).filter(pk=trial.pk).exists()


@pytest.mark.parametrize('restriction,expected', [('tp53_disruption_required', 'matched'), ('tp53_disruption_excluded', 'not_matched')])
def test_explicit_positive_reaches_eligibility_without_legacy_markers(restriction, expected):
    pi = _build_in_memory({'disease': 'chronic lymphocytic leukemia', 'tp53_disruption': True})
    trial = TrialFactory(disease='chronic lymphocytic leukemia', **{restriction: True})
    assert UserToTrialAttrMatcher(trial, pi).attr_match_status('tp53_disruption') == expected


@pytest.mark.parametrize('value', ['false', 0, 1, {}, []])
def test_non_boolean_aggregate_from_a_client_is_still_rejected(value):
    """Strictness is a property of the HTTP boundary, not of the builder.

    The inline payload is the one caller that IS a client, so it keeps the 400
    — a malformed aggregate there should not quietly become a marker-derived
    answer. The CTOMOP adapter and the management commands do not, because
    there the value came from UPSTREAM and a raise blames the wrong party:
    `trials_views._resolve_patient_info` re-raises `APIException` unchanged
    while its own comment requires a person_id-path failure to surface as 500,
    and in a batch command it is a traceback rather than any HTTP status.

    The error is namespaced under `patient_info` to match the sibling errors
    on that path.
    """
    from rest_framework.exceptions import ValidationError

    with pytest.raises(ValidationError) as caught:
        _build_in_memory({'tp53_disruption': value}, strict=True)
    assert 'patient_info' in caught.value.detail


def test_the_ctomop_adapter_does_not_raise_on_a_malformed_upstream_value():
    """The path that made the raise wrong in the first place."""
    pi = build_patient_info_from_ctomop_row({
        'disease': 'chronic lymphocytic leukemia',
        'tp53_disruption': 1,
        'molecular_markers': 'TP53 Mutation',
    })
    assert pi.tp53_disruption is True
