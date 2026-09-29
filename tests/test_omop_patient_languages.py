"""EXACT builds the patient's language pairs from PROMOP's capability booleans (CB #5350).

PROMOP serves english_/spanish_ x speak/read/write/understand as three-valued
booleans; under EXACT_OMOP_LANGUAGES resolve turns the True ones into
"<Language.omop_concept_id>:<LanguageSkillLevel.omop_concept_id>" pairs on
PatientInfo.language_skill_concept_ids, on the inline and the PROMOP-row paths.
"""
from io import StringIO

import pytest
from django.core.management import call_command
from django.test import override_settings

from trials.models import Trial
from trials.services.loaders.load_lang_options import LoadLangOptions
from trials.services.omop.patient_languages import (
    LANGUAGE_CAPABILITY_FIELDS,
    language_skill_concept_ids_from_capabilities,
)
from trials.services.patient_info.promop_adapter import build_patient_info_from_promop_row
from trials.services.patient_info.resolve import _build_in_memory
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db

EN_SPEAK = '4180186:2100007853'
EN_WRITE = '4180186:2100007855'
ES_SPEAK = '4182511:2100007853'


@pytest.fixture
def omop_on(settings):
    settings.EXACT_OMOP_LANGUAGES = True


@pytest.fixture
def lang_vocab(db):
    LoadLangOptions().load_all()
    call_command('load_language_omop_concept_ids', stdout=StringIO())


def _promop_row(**booleans):
    # every one of the eight present, NULL unless given: PROMOP's shape
    row = {'disease': 'Multiple Myeloma', 'languages_skills': 'English language: speak'}
    row.update({name: None for name in LANGUAGE_CAPABILITY_FIELDS})
    row.update(booleans)
    return row


@pytest.mark.usefixtures('omop_on')
class TestBuilder:
    def test_true_speak_write_become_sorted_deduped_pairs(self, lang_vocab):
        out = language_skill_concept_ids_from_capabilities(
            {'spanish_speak': True, 'english_write': True, 'english_speak': True, 'english_read': False})
        assert out == {'language_skill_concept_ids': [EN_SPEAK, EN_WRITE, ES_SPEAK]}

    def test_null_only_gives_empty(self, lang_vocab):
        out = language_skill_concept_ids_from_capabilities({name: None for name in LANGUAGE_CAPABILITY_FIELDS})
        assert out == {'language_skill_concept_ids': []}

    def test_read_or_understand_only_gives_empty(self, lang_vocab):
        # interim limitation: no LanguageSkillLevel rows for read/understand
        out = language_skill_concept_ids_from_capabilities({'english_read': True, 'spanish_understand': True})
        assert out['language_skill_concept_ids'] == []

    @pytest.mark.parametrize('value, expected', [
        (True, [EN_SPEAK]), ('true', [EN_SPEAK]), (' TRUE ', [EN_SPEAK]), ('t', [EN_SPEAK]),
        ('Yes', [EN_SPEAK]), (1, [EN_SPEAK]), ('1', [EN_SPEAK]), (' 1 ', [EN_SPEAK]),
        (False, []), (0, []), ('false', []), ('f', []), ('no', []), ('0', []), (None, []),
        (2, []), ('on', []), ('', []), (1.0, []),
    ])
    def test_true_is_true_or_the_string_true_never_truthiness(self, lang_vocab, value, expected):
        assert language_skill_concept_ids_from_capabilities(
            {'english_speak': value})['language_skill_concept_ids'] == expected

    def test_before_the_loader_runs_the_gate_leaves_the_payload_alone(self, db):
        LoadLangOptions().load_all()  # vocab rows, but no concept ids yet: not ready
        data = {'english_speak': True}
        assert language_skill_concept_ids_from_capabilities(data) is data

    def test_booleans_override_a_directly_sent_list(self, lang_vocab):
        out = language_skill_concept_ids_from_capabilities(
            {'english_speak': True, 'language_skill_concept_ids': [ES_SPEAK]})
        assert out['language_skill_concept_ids'] == [EN_SPEAK]

    def test_all_null_booleans_still_override_a_directly_sent_list(self, lang_vocab):
        # Present-but-NULL is PROMOP saying "not asked", which beats a stale list.
        row = {name: None for name in LANGUAGE_CAPABILITY_FIELDS}
        out = language_skill_concept_ids_from_capabilities({**row, 'language_skill_concept_ids': ['1:2']})
        assert out['language_skill_concept_ids'] == []

    def test_unloaded_vocab_warns(self, db, caplog, monkeypatch):
        # Only reachable if the gate says ready while the vocab is not (a race);
        # force the gate open to exercise the builder's own warning.
        from trials.services.omop import languages_readiness
        monkeypatch.setattr(languages_readiness, 'languages_ready', lambda: True)
        out = language_skill_concept_ids_from_capabilities({'english_speak': True})
        assert out['language_skill_concept_ids'] == []
        assert 'speak__en' in caplog.text

    def test_no_boolean_leaves_a_directly_sent_list_alone(self, lang_vocab):
        data = {'language_skill_concept_ids': [ES_SPEAK], 'disease': 'x'}
        assert language_skill_concept_ids_from_capabilities(data) is data

    def test_vocab_read_once(self, lang_vocab, django_assert_max_num_queries):
        from exact_matching.omop.languages_match_profile import omop_languages_enabled
        assert omop_languages_enabled()  # readiness computed and cached first
        with django_assert_max_num_queries(2):
            language_skill_concept_ids_from_capabilities(
                {name: True for name in LANGUAGE_CAPABILITY_FIELDS})


@override_settings(EXACT_OMOP_LANGUAGES=False)
def test_flag_off_leaves_the_payload_alone(lang_vocab, django_assert_num_queries):
    data = {'english_speak': True, 'languages_skills': 'speak__en'}
    with django_assert_num_queries(0):
        assert language_skill_concept_ids_from_capabilities(data) is data
    patient = _build_in_memory(dict(data))
    assert patient.language_skill_concept_ids is None
    assert patient.languages_skills == 'speak__en'


@pytest.mark.usefixtures('omop_on')
class TestBothPaths:
    def test_inline_camelcase_booleans(self, lang_vocab):
        patient = _build_in_memory({'disease': 'multiple myeloma', 'englishSpeak': True, 'spanishWrite': None})
        assert patient.language_skill_concept_ids == [EN_SPEAK]

    def test_promop_row(self, lang_vocab):
        patient = build_patient_info_from_promop_row(_promop_row(english_speak=True, english_read=True))
        assert patient.language_skill_concept_ids == [EN_SPEAK]

    def test_promop_row_all_null_is_empty_not_missing(self, lang_vocab):
        # built before the adapter strips None values
        patient = build_patient_info_from_promop_row(_promop_row())
        assert patient.language_skill_concept_ids == []


@pytest.mark.usefixtures('omop_on')
class TestEndToEnd:
    def _trials(self):
        # backfilled the real way: loader already ran (fixture), then the backfill
        speaks_en = TrialFactory(disease='multiple myeloma', languages_skills_required=['speak__en'])
        anyone = TrialFactory(disease='multiple myeloma', languages_skills_required=[])
        call_command('backfill_omop_languages_skills_column', stdout=StringIO())
        speaks_en.refresh_from_db()
        assert speaks_en.omop_languages_skills_required == [EN_SPEAK]
        return speaks_en, anyone

    def _kept(self, patient, trials):
        scope, _ = Trial.objects.filter(id__in=[t.id for t in trials]).filter_by_patient_info(patient)
        return set(scope.values_list('id', flat=True))

    def test_english_speaker_matches(self, lang_vocab):
        speaks_en, anyone = self._trials()
        patient = build_patient_info_from_promop_row(_promop_row(english_speak=True))
        assert self._kept(patient, [speaks_en, anyone]) == {speaks_en.id, anyone.id}
        assert UserToTrialAttrMatcher(speaks_en, patient).attr_match_status('languages_skills') == 'matched'

    def test_english_writer_only_does_not(self, lang_vocab):
        speaks_en, anyone = self._trials()
        patient = build_patient_info_from_promop_row(_promop_row(english_write=True))
        assert self._kept(patient, [speaks_en, anyone]) == {anyone.id}
        assert UserToTrialAttrMatcher(speaks_en, patient).attr_match_status('languages_skills') == 'not_matched'

    def test_reader_only_is_unknown_not_rejected(self, lang_vocab):
        speaks_en, anyone = self._trials()
        patient = build_patient_info_from_promop_row(_promop_row(english_read=True))
        assert self._kept(patient, [speaks_en, anyone]) == {speaks_en.id, anyone.id}
        assert UserToTrialAttrMatcher(speaks_en, patient).attr_match_status('languages_skills') == 'unknown'
