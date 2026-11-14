"""Legacy (#605) and OMOP (CB #5350) language translations read the same booleans.

`translate_language_capabilities` builds the legacy H/A first (keeping the eight
booleans) and then the OMOP pairs (consuming them when the OMOP path is active).
Whichever path the readiness snapshot picks has its fields, on both the inline
and the PROMOP-row paths.
"""
from io import StringIO

import pytest
from django.core.management import call_command

from exact_matching.omop.languages_match_profile import omop_languages_enabled, reset_languages_decision
from trials.models import Language, Trial
from trials.services.loaders.load_lang_options import LoadLangOptions
from trials.services.omop.patient_languages import LANGUAGE_CAPABILITY_FIELDS
from trials.services.patient_info.promop_adapter import build_patient_info_from_promop_row
from trials.services.patient_info.resolve import _build_in_memory
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db

EN_SPEAK = '4180186:2100007853'
NULLS = {name: None for name in LANGUAGE_CAPABILITY_FIELDS}


@pytest.fixture
def vocab(db):
    LoadLangOptions().load_all()
    call_command('load_language_omop_concept_ids', stdout=StringIO())


def _row(**booleans):
    return {'disease': 'Multiple Myeloma', 'languages_skills': 'English language: speak', **NULLS, **booleans}


BUILDERS = {
    'inline': lambda **b: _build_in_memory(_row(**b)),
    'promop_row': lambda **b: build_patient_info_from_promop_row(_row(**b)),
}


def _set_state(settings, state):
    settings.EXACT_OMOP_LANGUAGES = state != 'off'
    if state == 'on_not_ready':
        Language.objects.update(omop_concept_id=None)
    reset_languages_decision()


def _verdict(trial, patient):
    scope, _ = Trial.objects.filter(id=trial.id).filter_by_patient_info(patient)
    return scope.exists(), UserToTrialAttrMatcher(trial, patient).attr_match_status('languages_skills')


@pytest.mark.usefixtures('vocab')
@pytest.mark.parametrize('path', sorted(BUILDERS))
@pytest.mark.parametrize('state', ['off', 'on_ready', 'on_not_ready'])
class TestBothPathsInEveryState:
    def _trial(self):
        return TrialFactory(disease='multiple myeloma', languages_skills_required=['speak__en'],
                            omop_languages_skills_required=[EN_SPEAK])

    def test_fields_of_both_paths(self, settings, state, path):
        trial = self._trial()
        _set_state(settings, state)
        patient = BUILDERS[path](english_speak=True)
        assert omop_languages_enabled() is (state == 'on_ready')
        # the legacy fields are always built, so the "Yours" cell has codes
        assert patient.languages_skills == 'speak__en'
        assert patient.languages_asked == 'en'
        if state == 'on_ready':
            assert patient.language_skill_concept_ids == [EN_SPEAK]
            assert patient.language_asked_concept_ids == ['4180186']
        else:
            assert patient.language_skill_concept_ids is None
        assert _verdict(trial, patient) == (True, 'matched')

    def test_asked_and_not_able_is_excluded_in_every_state(self, settings, state, path):
        trial = self._trial()
        _set_state(settings, state)
        carlos = BUILDERS[path](english_speak=False, english_read=True)
        assert _verdict(trial, carlos) == (False, 'not_matched')

    def test_never_asked_is_not_filtered_in_every_state(self, settings, state, path):
        trial = self._trial()
        _set_state(settings, state)
        boris = BUILDERS[path]()
        assert boris.languages_skills is None      # the display string is dropped
        assert _verdict(trial, boris) == (True, 'unknown')
