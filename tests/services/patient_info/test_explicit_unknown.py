"""An explicit `null` must not come back as a confirmed negative (#502).

Five derivations end in a terminal `return False` meaning "I could not work it
out". `False` is an ASSERTION to the matcher: it reaches `not_matched`,
`match_score_and_status()` short-circuits the trial to `not_eligible`, and the
SQL prefilter removes it. So a caller saying "we do not know" silently DELETED
trials from the patient's results — the same defect #488 fixed for
`tp53_disruption` alone.
"""
import pytest

from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.configs import USER_TO_TRIAL_ATTRS_MAPPING
from trials.services.patient_info.patient_info_attributes import (
    EXPLICIT_UNKNOWN_FIELDS,
    explicitly_unknown,
)
from trials.services.patient_info.resolve import _build_in_memory
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db

FIVE = [
    ('renal_adequacy_status', {'disease': 'multiple myeloma'}),
    ('hepatic_adequacy_status', {'disease': 'multiple myeloma'}),
    ('haematological_adequacy_status', {'disease': 'multiple myeloma'}),
    ('tnbc_status', {'disease': 'breast cancer'}),
    ('metastatic_status', {'disease': 'breast cancer'}),
]


@pytest.mark.parametrize('blank', [None, ''])
@pytest.mark.parametrize('field,inputs', [
    ('metastatic_status', 'stage'),
    ('tnbc_status', 'estrogen_receptor_status'),
])
def test_a_blank_input_is_missing_input_not_a_negative_one(field, inputs, blank):
    """An empty string is how this vocabulary spells "Unknown".

    `value_options` renders `''` as the literal label "Unknown" for stage and
    for each receptor. Checking only `is None` let a payload carrying
    `stage: ""` skip the unknown branch and derive a confirmed negative — the
    very thing the explicit null was asking not to happen.
    """
    pi = _build_in_memory({'disease': 'breast cancer', field: None, inputs: blank})

    assert getattr(pi, field) is None


@pytest.mark.parametrize('field,base', FIVE)
def test_an_explicit_null_is_unknown_not_a_negative(field, base):
    pi = _build_in_memory(dict(base, **{field: None}))

    assert getattr(pi, field) is None, (
        'the caller said "we do not know" and got a confirmed no back'
    )


@pytest.mark.parametrize('field,base', FIVE)
def test_an_absent_key_keeps_the_legacy_false(field, base):
    """Deliberately unchanged.

    Twelve other `bool_restriction` attributes already sit at None for any
    patient who never supplied them, so widening this to a bare None would move
    matching for every patient at once. That is the open half of #502 and wants
    a corpus measurement of its own.
    """
    assert getattr(_build_in_memory(dict(base)), field) is False


class TestAnExplicitNullDoesNotRetractAnAnswer:
    """#488's first lesson, which was expensive to find.

    A null must yield unknown only where the derivation has nothing to go on.
    Where the inputs ARE present, the derived answer stands — otherwise a source
    that habitually sends `null` for aggregates would erase evidence it also
    sent in the same payload.
    """

    def test_a_derivable_positive_survives_it(self):
        pi = _build_in_memory({'disease': 'multiple myeloma',
                               'renal_adequacy_status': None,
                               'serum_creatinine_level': 0.8, 'gender': 'M',
                               'patient_age': 20, 'creatinine_clearance_rate': 110})

        assert pi.renal_adequacy_status is True

    def test_a_derivable_negative_survives_it_too(self):
        pi = _build_in_memory({'disease': 'multiple myeloma',
                               'renal_adequacy_status': None,
                               'estimated_glomerular_filtration_rate': 40,
                               'creatinine_clearance_rate': 110})

        assert pi.renal_adequacy_status is False

    def test_a_stage_that_settles_the_question_survives_it(self):
        assert _build_in_memory({'disease': 'breast cancer', 'stage': 'IV',
                                 'metastatic_status': None}).metastatic_status is True

    @pytest.mark.parametrize('receptors', [
        {'estrogen_receptor_status': 'er_plus'},
        {'progesterone_receptor_status': 'pr_plus'},
        {'her2_status': 'her2_plus'},
    ])
    def test_one_stated_positive_receptor_settles_tnbc_on_its_own(self, receptors):
        """Incomplete is not unknown when what IS known is decisive.

        ER+ proves the tumour is not triple negative whether or not PR and HER2
        were ever measured. Answering "unknown" would keep a TNBC-only trial as
        a candidate for a patient their own receptor rules out.
        """
        pi = _build_in_memory(dict({'disease': 'breast cancer', 'tnbc_status': None},
                                   **receptors))

        assert pi.tnbc_status is False

    def test_receptors_that_settle_the_question_survive_it(self):
        pi = _build_in_memory({'disease': 'breast cancer', 'tnbc_status': None,
                               'estrogen_receptor_status': 'er_plus',
                               'progesterone_receptor_status': 'pr_minus',
                               'her2_status': 'her2_minus'})

        assert pi.tnbc_status is False


class TestWhatTheMatcherDoesWithIt:
    """The derivation change alone is not the fix, measured.

    `_match_type_bool_restriction` opened with `value = False if ctx.value is
    None else ctx.value`, so a None was collapsed straight back before any
    decision. On the base branch an explicit null reached `not_matched` and the
    trial was deleted; with only the derivation fixed it reached `not_matched`
    while SURVIVING the prefilter — a trial in the results labelled "not
    eligible". The two halves are one fix.
    """

    def _trial(self):
        return TrialFactory(disease='multiple myeloma', renal_adequacy_required=True)

    def test_an_explicit_unknown_leaves_the_trial_a_candidate(self):
        trial = self._trial()
        pi = _build_in_memory({'disease': 'multiple myeloma', 'patient_age': 40,
                               'renal_adequacy_status': None})
        m = UserToTrialAttrMatcher(trial, pi)

        assert m.attr_match_status('renal_adequacy_status') == 'unknown'
        assert m.match_score_and_status()[1] == 'potential'
        assert Trial.objects.filter_by_patient_info(pi)[0].filter(pk=trial.pk).exists(), (
            'the trial was deleted from the results by an explicit "unknown"'
        )

    def test_a_patient_who_never_said_anything_is_untouched(self):
        trial = self._trial()
        pi = _build_in_memory({'disease': 'multiple myeloma', 'patient_age': 40})
        m = UserToTrialAttrMatcher(trial, pi)

        assert m.attr_match_status('renal_adequacy_status') == 'not_matched'
        assert m.match_score_and_status()[1] == 'not_eligible'

    def test_a_trial_that_asks_nothing_is_not_demoted(self):
        """The gate is the TRIAL as well as the patient.

        Where the trial carries no requirement the attribute is irrelevant to
        it, and the legacy path lands on `matched`. Returning "unknown" there
        would cost score on every trial that never asked about the field.
        """
        trial = TrialFactory(disease='multiple myeloma', renal_adequacy_required=False)
        pi = _build_in_memory({'disease': 'multiple myeloma', 'patient_age': 40,
                               'renal_adequacy_status': None})

        assert UserToTrialAttrMatcher(trial, pi).attr_match_status(
            'renal_adequacy_status') == 'matched'

    def test_an_attribute_that_is_simply_none_is_untouched(self):
        """The gate is provenance, not the value.

        `meets_crab` is None for any myeloma patient who supplied none of its
        inputs. Reading a bare None as unknown would change matching for twelve
        such attributes at once, for every patient — so it must not.
        """
        trial = TrialFactory(disease='multiple myeloma', meets_crab=True)
        pi = _build_in_memory({'disease': 'multiple myeloma', 'patient_age': 40})

        assert pi.meets_crab is None
        assert UserToTrialAttrMatcher(trial, pi).attr_match_status('meets_crab') != 'unknown'


def test_a_record_that_never_came_from_a_payload_has_no_provenance():
    """The marker is set only where a caller's own keys are visible.

    A value read back from the database is EXACT's own derivation, not an
    assertion by anyone, and must not be mistaken for one.
    """
    pi = PatientInfo(disease='multiple myeloma', renal_adequacy_status=None)

    assert not hasattr(pi, '_provided_fields')
    assert UserToTrialAttrMatcher(
        TrialFactory(disease='multiple myeloma', renal_adequacy_required=True), pi
    ).attr_match_status('renal_adequacy_status') == 'not_matched'


class TestTheSeamStaysOnTheFiveFields:
    """`_provided_fields` names EVERY key the caller sent, and the matcher's two
    `bool_restriction` handlers see all 36 attributes of that type.

    That matters because of the round trip: `PatientInfoSerializer` emits
    `vars(instance)`, so a myeloma patient with no CRAB or IMWG inputs is
    serialised with dozens of mapped attributes as explicit `null`. A client
    that reads a record and posts it back NAMES all of them. Without a name
    list, 11 attributes move from `matched` to `unknown` on that round trip —
    `meets_crab` among them, where a trial the patient was excluded from becomes
    a candidate.

    Widening to those is the open half of #502 and wants a corpus measurement,
    so these pin that it has not happened by accident.
    """

    # The `bool_restriction` attributes that already sit at None for a patient
    # who supplied none of their inputs. Naming one of them as null is a no-op
    # today, which is exactly why they are the ones at risk: the round trip
    # names them all, and without the gate the seam would fire for every one.
    #
    # The other non-five `bool_restriction` attributes (`no_hiv_status`,
    # `no_active_infection_status`, ...) default to True, so naming them null
    # already moves them from `matched` to `not_matched` — measured identically
    # on the base branch, so it is not this change's doing and not asserted here.
    ALREADY_NONE = ['meets_crab', 'meets_slim', 'meets_gelf', 'meets_lugano',
                    'measurable_disease_imwg', 'measurable_disease_iwcll',
                    'lymphadenopathy', 'splenomegaly', 'hepatomegaly',
                    'bone_marrow_involvement', 'abnormal_kappa_lambda_ratio',
                    'autoimmune_cytopenias_refractory_to_steroids']

    @pytest.mark.parametrize('field', ALREADY_NONE)
    def test_the_seam_does_not_fire_for_them(self, field):
        pi = _build_in_memory({'disease': 'multiple myeloma', 'patient_age': 40,
                               field: None})

        assert explicitly_unknown(pi, field) is False, (
            f'{field} was named as null and read as a caller assertion'
        )

    @pytest.mark.parametrize('field', ALREADY_NONE)
    def test_naming_one_of_them_as_null_changes_nothing(self, field):
        trial_attr = USER_TO_TRIAL_ATTRS_MAPPING[field]['attr']
        if not hasattr(Trial, trial_attr):
            pytest.skip(f'{trial_attr} is not a Trial column')
        trial = TrialFactory(disease='multiple myeloma', **{trial_attr: True})
        base = {'disease': 'multiple myeloma', 'patient_age': 40}

        lean = UserToTrialAttrMatcher(trial, _build_in_memory(dict(base)))
        named = UserToTrialAttrMatcher(trial, _build_in_memory(dict(base, **{field: None})))

        assert named.attr_match_status(field) == lean.attr_match_status(field), (
            f'naming {field} as null moved it; the seam escaped the five fields'
        )
        assert named.match_score_and_status() == lean.match_score_and_status()

    def test_the_whole_record_echoed_back_matches_the_lean_one(self):
        """The round trip itself, which is how a real client reaches this.

        `PatientInfoSerializer` emits `vars(instance)`, so a client that reads a
        record and posts it back NAMES every attribute. Measured without the
        gate: 11 of these move from `matched` to `unknown`, `meets_crab` among
        them — a trial the patient was excluded from turns into a candidate.
        """
        trial = TrialFactory(disease='multiple myeloma', meets_crab=True)
        base = {'disease': 'multiple myeloma', 'patient_age': 40}

        lean = _build_in_memory(dict(base))
        echoed = _build_in_memory(dict(base, **{f: None for f in self.ALREADY_NONE}))

        assert UserToTrialAttrMatcher(trial, echoed).attr_match_status('meets_crab') == 'not_matched'
        assert (UserToTrialAttrMatcher(trial, echoed).match_score_and_status()
                == UserToTrialAttrMatcher(trial, lean).match_score_and_status())

    def test_the_five_are_exactly_what_the_seam_covers(self):
        assert EXPLICIT_UNKNOWN_FIELDS == {
            'renal_adequacy_status', 'hepatic_adequacy_status',
            'haematological_adequacy_status', 'tnbc_status', 'metastatic_status',
        }
