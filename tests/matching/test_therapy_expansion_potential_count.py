"""#5013: a therapy answer the therapy graph cannot expand must be counted as
still-to-fill-in, not as an answer.

The matcher answers `first_line_therapy` from the therapy GRAPH: it runs the
patient's therapies through `derive_component_and_type_values`, and when they
resolve to no regimen the component and type legs both come back `unknown`, so
any trial carrying a component or type criterion is Potential. The SQL potential
count only looked at the raw string — non-empty means answered — and left the
same trial Eligible.

That is 19% of CB patients with a first-line therapy (67% of FL patients), whose
stored value is a therapy TYPE or a procedure (`radiotherapy`, `chemotherapy`,
`aromatase_inhibitor`, `surgery`) rather than a regimen code: 1357 diverging
(patient, trial) pairs in the cancerbot#4841 census.

Contract: unexpandable therapies -> potential on both paths. The hard filter is
deliberately untouched — a value that contradicts a required regimen must keep
excluding the trial.
"""
import pytest

from trials.models import Therapy, TherapyComponent, TherapyComponentConnection, Trial
from trials.services.matching import status_equivalence as se
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from trials.services.user_to_trial_attrs_mapper import UserToTrialAttrsMapper
from tests.factories import TherapyComponentFactory, TherapyFactory, TrialFactory

# A value that is not a regimen in the vocab. In production these are real
# patient answers — `radiotherapy`, `chemotherapy`, `aromatase_inhibitor`,
# `surgery`, `ww` — i.e. therapy TYPES and procedures stored in a regimen-shaped
# field. The test seed loads a vocab that does contain some of them, so it uses
# a code no seeder authors to keep the precondition unambiguous.
UNEXPANDABLE = 'not_a_regimen_code_5013'


def _patient(first_line_therapy):
    return PatientInfo(
        disease='multiple myeloma', patient_age=65,
        first_line_therapy=first_line_therapy,
    )


@pytest.fixture
def expandable_therapy(db):
    """A regimen that expands into one component, i.e. an answer the matcher can use."""
    therapy = TherapyFactory()
    component = TherapyComponentFactory()
    TherapyComponentConnection.objects.create(therapy=therapy, component=component)
    return therapy, component


@pytest.mark.django_db
def test_unexpandable_therapy_is_not_counted_as_answered():
    """The precondition: a value that is not a regimen code expands to nothing,
    so the count must not treat it as an answer.

    The real-world instances are `radiotherapy`, `chemotherapy`,
    `aromatase_inhibitor`, `surgery`, `ww` — absent from the CB catalog this was
    measured on, but several of them DO exist in EXACT's seeded test vocab, hence
    the synthetic code here."""
    assert Therapy.objects.filter(code=UNEXPANDABLE).exists() is False

    service = PatientInfoAttributes(_patient(UNEXPANDABLE))

    assert service.is_attr_blank('first_line_therapy') is False, \
        'the raw value is present — this is exactly why the count used to trust it'
    assert UserToTrialAttrsMapper._therapies_do_not_expand(service) is True


@pytest.mark.django_db
def test_expandable_therapy_is_still_counted_as_answered(expandable_therapy):
    therapy, _component = expandable_therapy
    service = PatientInfoAttributes(_patient(therapy.code))

    assert UserToTrialAttrsMapper._therapies_do_not_expand(service) is False


@pytest.mark.django_db
def test_no_therapies_at_all_is_left_to_is_attr_blank():
    """Guard: the helper only speaks about values that exist; an empty answer is
    already handled by `is_attr_blank`, and must not be double-counted here."""
    service = PatientInfoAttributes(_patient(None))

    assert UserToTrialAttrsMapper._therapies_do_not_expand(service) is False


@pytest.mark.django_db
def test_unexpandable_therapy_vs_component_criterion_is_potential(expandable_therapy):
    _therapy, component = expandable_therapy
    trial = TrialFactory(disease='multiple myeloma',
                         therapy_components_required=[component.code])

    verdict = UserToTrialAttrMatcher(trial, _patient(UNEXPANDABLE)).trial_match_status()

    assert verdict == 'potential'


@pytest.mark.django_db
@pytest.mark.parametrize('criterion', [
    'therapy_components_required',
    'therapy_components_excluded',
    'therapy_types_required',
    'therapy_types_excluded',
    # The regimen leg is decided on the RAW values, so the matcher stays definite
    # about these two. They are here because counting the attr's whole six-column
    # fragment as potential would demote them — a divergence in the opposite
    # direction (452 such trials in a CB catalog, mostly exclusion-only).
    'therapies_required',
    'therapies_excluded',
])
def test_no_sql_matcher_divergence_on_unexpandable_therapy(criterion, expandable_therapy):
    """The #4832 contract, across the criteria that made the matcher say unknown."""
    _therapy, component = expandable_therapy
    if 'components' in criterion:
        value = component.code
    elif 'types' in criterion:
        value = 'chemotherapy_(alkylating_agent)'
    else:
        # A regimen the patient's unexpandable value cannot overlap. For
        # `therapies_required` the SQL filter drops the trial and the matcher says
        # not_eligible; for `therapies_excluded` both say eligible. Either way they
        # must agree.
        value = 'krd'
    trial = TrialFactory(disease='multiple myeloma', **{criterion: [value]})
    base = Trial.objects.filter(id=trial.id)

    divs = se.compare(base, _patient(UNEXPANDABLE))

    assert divs == [], (
        f"{criterion} still diverges: "
        f"{[(d.direction, d.matcher_unknown_attrs, d.sql_potential_attrs) for d in divs]}"
    )


@pytest.mark.django_db
def test_expandable_therapy_that_satisfies_the_criterion_stays_eligible(expandable_therapy):
    """Guard against over-firing: a real regimen that meets the requirement is
    still a definite yes, and the two paths still agree."""
    therapy, component = expandable_therapy
    trial = TrialFactory(disease='multiple myeloma',
                         therapy_components_required=[component.code])
    base = Trial.objects.filter(id=trial.id)
    patient = _patient(therapy.code)

    assert UserToTrialAttrMatcher(trial, patient).trial_match_status() == 'eligible'
    assert se.compare(base, patient) == []


@pytest.mark.django_db
def test_hard_filter_still_excludes_a_contradicted_regimen():
    """The filter is untouched: a trial requiring a regimen the patient does not
    have is still excluded by SQL, and the matcher still rejects it — the fix
    must not turn a definite no into a potential."""
    trial = TrialFactory(disease='multiple myeloma', therapies_required=['krd'])
    base = Trial.objects.filter(id=trial.id)
    patient = _patient(UNEXPANDABLE)

    assert UserToTrialAttrMatcher(trial, patient).trial_match_status() == 'not_eligible'
    assert se.sql_status_map(base, patient) == {trial.id: 'not_eligible'}
    assert se.compare(base, patient) == []


@pytest.mark.django_db
def test_trial_without_therapy_criteria_is_unaffected():
    trial = TrialFactory(disease='multiple myeloma')
    base = Trial.objects.filter(id=trial.id)
    patient = _patient(UNEXPANDABLE)

    assert UserToTrialAttrMatcher(trial, patient).trial_match_status() == 'eligible'
    assert se.compare(base, patient) == []


@pytest.mark.django_db
def test_no_prior_therapy_keeps_the_answer_as_answered():
    """A patient who says they have had no prior therapy is a definite answer,
    not an unknown: `_match_therapy_things` skips its unknown branch when
    `has_no_prior_therapy`, so the matcher returns a definite verdict and the
    count must keep treating the attr as filled."""
    # An EXCLUDING trial, deliberately: the no-prior hard filter keeps only trials
    # whose *_required lists are all empty, so a component-REQUIRED trial never
    # reaches the count and the guard would go untested (it did — deleting the
    # guard left every test green).
    trial = TrialFactory(disease='multiple myeloma',
                         therapy_components_excluded=['lenalidomide'],
                         therapies_required=[], therapy_components_required=[],
                         therapy_types_required=[])
    base = Trial.objects.filter(id=trial.id)
    patient = PatientInfo(
        disease='multiple myeloma', patient_age=65,
        first_line_therapy=UNEXPANDABLE, prior_therapy='None',
    )

    assert UserToTrialAttrMatcher(trial, patient).trial_match_status() == 'eligible'
    assert se.sql_status_map(base, patient) == {trial.id: 'eligible'}
    assert se.compare(base, patient) == []


@pytest.mark.django_db
def test_partial_expansion_still_counts_as_answered(monkeypatch):
    """Boundary pin for healthkey-ai/exact#390.

    The count only stops trusting the answer when BOTH legs are unknown. When one
    leg resolves (possible under EXACT_OMOP_THERAPY, where components come from
    the consumer while types are derived separately), the attr stays 'answered'
    here — deliberately, because a known leg can still decide its own criterion,
    and splitting the count per leg is part of the OMOP-path work in #390, not
    this fix. If #390 changes that, this test should fail and be updated.
    """
    import trials.services.omop.therapy_graph as therapy_graph

    monkeypatch.setattr(
        therapy_graph, 'derive_component_and_type_values',
        lambda values, component_ids=None, measure=False, patient_class_ids=None: (
            None, ['chemotherapy_(alkylating_agent)']),
    )
    service = PatientInfoAttributes(_patient(UNEXPANDABLE))

    assert UserToTrialAttrsMapper._therapies_do_not_expand(service) is False


@pytest.mark.django_db
def test_the_two_count_fragments_are_scoped_to_their_legs():
    """The shape of the fix, stated directly: the potential fragment keys on the
    component/type columns only, the match-score fragment additionally requires a
    regimen column, so a regimen-only trial stays answered."""
    attrs, eligible = UserToTrialAttrsMapper().potential_attrs_to_check(
        _patient(UNEXPANDABLE), with_eligible=True)

    potential_sql = attrs['first_line_therapy']
    eligible_sql = eligible['first_line_therapy']

    for column in ('therapy_components_required', 'therapy_components_excluded',
                   'therapy_types_required', 'therapy_types_excluded'):
        assert column in potential_sql
    for column in ('therapies_required', 'therapies_excluded'):
        assert column not in potential_sql
        assert column in eligible_sql


@pytest.mark.django_db
@pytest.mark.xfail(strict=True, reason='healthkey-ai/exact#392 — a regimen with no '
                                       'components resolves to ([], []), which the matcher '
                                       'reads as a definite no and SQL as no constraint')
def test_zero_component_regimen_does_not_diverge():
    """The gap this fix deliberately does not cover, pinned so #392 has a test.

    `derive_component_and_type_values` returns (None, None) only when NO regimen
    resolves. A regimen that exists with no components gives ([], []) — known-empty,
    not unknown — so `_therapies_do_not_expand` is False and the count is unchanged,
    while the matcher rejects the component criterion outright.
    """
    therapy = TherapyFactory()  # no TherapyComponentConnection
    component = TherapyComponentFactory()
    trial = TrialFactory(disease='multiple myeloma',
                         therapy_components_required=[component.code])
    base = Trial.objects.filter(id=trial.id)
    patient = _patient(therapy.code)

    assert se.compare(base, patient) == []
