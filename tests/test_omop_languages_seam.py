"""Language-skill OMOP seam: EXACT_OMOP_LANGUAGES flips the trial column AND the
patient field, on the search queryset and the per-trial matcher together.

Flag off: legacy codes on both sides (``speak__en`` vs ``languages_skills_required``).
Flag on: the consumer's ``language_skill_concept_ids`` pairs vs
``omop_languages_skills_required``. EXACT translates nothing (therapy precedent).
"""
import pytest
from django.test import override_settings

from trials.models import Trial
from trials.services.omop.languages_match_profile import (
    LANGUAGES_MATCH_PROFILE,
    LEGACY_LANGUAGES_MATCH_PROFILE,
    OMOP_LANGUAGES_MATCH_PROFILE,
)
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db

EN_SPEAK = '4180186:2100007853'
ES_WRITE = '4182511:2100007855'


def _trials():
    # Legacy and omop columns populated consistently, as a correct backfill leaves them.
    speaks_en = TrialFactory(languages_skills_required=['speak__en'], omop_languages_skills_required=[EN_SPEAK])
    writes_es = TrialFactory(languages_skills_required=['write__es'], omop_languages_skills_required=[ES_WRITE])
    anyone = TrialFactory(languages_skills_required=[], omop_languages_skills_required=[])
    return speaks_en, writes_es, anyone


def _search(patient, trials):
    ids = [t.id for t in trials]
    scope, _traces = Trial.objects.filter(id__in=ids).filter_by_patient_info(patient)
    return set(scope.values_list('id', flat=True))


def _status(trial, patient):
    return UserToTrialAttrMatcher(trial, patient).attr_match_status('languages_skills')


class TestProfile:
    @override_settings(EXACT_OMOP_LANGUAGES=False)
    def test_legacy_by_default(self):
        assert LANGUAGES_MATCH_PROFILE.languages_skills_required == 'languages_skills_required'
        assert LANGUAGES_MATCH_PROFILE.patient_languages_skills == 'languages_skills'

    @override_settings(EXACT_OMOP_LANGUAGES=True)
    def test_omop_under_the_flag(self):
        assert LANGUAGES_MATCH_PROFILE.languages_skills_required == 'omop_languages_skills_required'
        assert LANGUAGES_MATCH_PROFILE.patient_languages_skills == 'language_skill_concept_ids'

    def test_read_only(self):
        with pytest.raises(AttributeError):
            LANGUAGES_MATCH_PROFILE.languages_skills_required = 'x'

    def test_the_two_profiles_field_by_field(self):
        from dataclasses import asdict
        assert asdict(LEGACY_LANGUAGES_MATCH_PROFILE) == {
            'languages_skills_required': 'languages_skills_required',
            'patient_languages_skills': 'languages_skills',
        }
        assert asdict(OMOP_LANGUAGES_MATCH_PROFILE) == {
            'languages_skills_required': 'omop_languages_skills_required',
            'patient_languages_skills': 'language_skill_concept_ids',
        }


class TestQuerysetColumn:
    @override_settings(EXACT_OMOP_LANGUAGES=False)
    def test_legacy_column_when_off(self):
        where = str(Trial.objects.eligible_for_languages_skills(['speak__en']).query).split(' WHERE ', 1)[1]
        assert '"languages_skills_required"' in where
        assert 'omop_languages_skills_required' not in where

    @override_settings(EXACT_OMOP_LANGUAGES=True)
    def test_omop_column_when_on(self):
        where = str(Trial.objects.eligible_for_languages_skills([EN_SPEAK]).query).split(' WHERE ', 1)[1]
        assert '"omop_languages_skills_required"' in where
        assert '"languages_skills_required"' not in where


class TestFlagOff:
    @override_settings(EXACT_OMOP_LANGUAGES=False)
    def test_legacy_codes_match(self):
        speaks_en, writes_es, anyone = _trials()
        patient = PatientInfo(languages_skills='speak__en', language_skill_concept_ids=[ES_WRITE])
        # the pair list is ignored when the flag is off
        assert _search(patient, [speaks_en, writes_es, anyone]) == {speaks_en.id, anyone.id}
        assert _status(speaks_en, patient) == 'matched'
        assert _status(writes_es, patient) == 'not_matched'
        assert _status(anyone, patient) == 'matched'


class TestFlagOn:
    @override_settings(EXACT_OMOP_LANGUAGES=True)
    def test_pairs_match_by_overlap(self):
        speaks_en, writes_es, anyone = _trials()
        # PROMOP sends its display string AND the pairs; only the pairs are read.
        patient = PatientInfo(
            languages_skills='English language: read, speak',
            language_skill_concept_ids=[EN_SPEAK],
        )
        assert _search(patient, [speaks_en, writes_es, anyone]) == {speaks_en.id, anyone.id}
        assert _status(speaks_en, patient) == 'matched'
        assert _status(writes_es, patient) == 'not_matched'
        assert _status(anyone, patient) == 'matched'

    @override_settings(EXACT_OMOP_LANGUAGES=True)
    def test_legacy_code_is_not_translated(self):
        speaks_en, writes_es, anyone = _trials()
        # a patient speaking legacy codes in the pair field matches nothing that requires
        # a language: EXACT does not translate
        patient = PatientInfo(language_skill_concept_ids=['speak__en'])
        assert _search(patient, [speaks_en, writes_es, anyone]) == {anyone.id}

    @override_settings(EXACT_OMOP_LANGUAGES=True)
    @pytest.mark.parametrize('pairs', [None, []])
    def test_no_pairs_skips_the_filter(self, pairs):
        speaks_en, writes_es, anyone = _trials()
        # nothing recorded (or consumer did not send the field): not filtered, matcher unknown
        patient = PatientInfo(languages_skills='speak__en', language_skill_concept_ids=pairs)
        assert _search(patient, [speaks_en, writes_es, anyone]) == {speaks_en.id, writes_es.id, anyone.id}
        assert _status(speaks_en, patient) == 'unknown'
        assert _status(anyone, patient) == 'matched'


@pytest.mark.parametrize('flag', [False, True])
def test_queryset_and_matcher_agree(flag):
    with override_settings(EXACT_OMOP_LANGUAGES=flag):
        trials = _trials()
        patient = PatientInfo(languages_skills='write__es', language_skill_concept_ids=[ES_WRITE])
        kept = _search(patient, trials)
        for trial in trials:
            assert (trial.id in kept) == (_status(trial, patient) != 'not_matched'), trial


# ── count / blank-check SQL flips with the flag ───────────────────────

def _potential_count(trial, patient):
    return (Trial.objects.filter(id=trial.id).with_potential_attrs_count(patient)
            .values('potential_attrs_count').first()['potential_attrs_count'])


@override_settings(EXACT_OMOP_LANGUAGES=True)
def test_count_reads_the_omop_column_under_the_flag():
    from trials.services.matching import status_equivalence as se
    # legacy requirement present, OMOP column empty (e.g. only speak__other): under the
    # flag the trial imposes no language requirement, and the count must agree
    trial = TrialFactory(disease='multiple myeloma',
                         languages_skills_required=['speak__other'], omop_languages_skills_required=[])
    blank = PatientInfo(disease='multiple myeloma', patient_age=65)
    assert _potential_count(trial, blank) == 0
    assert se.compare(Trial.objects.filter(id=trial.id), blank) == []


@override_settings(EXACT_OMOP_LANGUAGES=True)
def test_count_still_potential_on_a_real_omop_requirement():
    from trials.services.matching import status_equivalence as se
    trial = TrialFactory(disease='multiple myeloma',
                         languages_skills_required=[], omop_languages_skills_required=[EN_SPEAK])
    blank = PatientInfo(disease='multiple myeloma', patient_age=65)
    assert _potential_count(trial, blank) == 1
    assert se.compare(Trial.objects.filter(id=trial.id), blank) == []


@override_settings(EXACT_OMOP_LANGUAGES=False)
def test_count_reads_the_legacy_column_when_off():
    trial = TrialFactory(disease='multiple myeloma',
                         languages_skills_required=['speak__en'], omop_languages_skills_required=[])
    blank = PatientInfo(disease='multiple myeloma', patient_age=65)
    assert _potential_count(trial, blank) == 1


def _asks_for_languages(trial):
    return any(item['userAttributeName'] == 'languagesSkills'
               for item in trial.attrs_to_fill_in({'languages_skills': 1}))


@pytest.mark.parametrize('flag, legacy, omop, asked', [
    (True, ['speak__other'], [], False),   # flag on: OMOP column decides
    (True, [], [EN_SPEAK], True),
    (False, ['speak__en'], [], True),      # flag off: legacy column decides
    (False, [], [EN_SPEAK], False),
])
def test_attrs_to_fill_in_follows_the_same_column(flag, legacy, omop, asked):
    with override_settings(EXACT_OMOP_LANGUAGES=flag):
        trial = TrialFactory(disease='multiple myeloma',
                             languages_skills_required=legacy, omop_languages_skills_required=omop)
        assert _asks_for_languages(trial) is asked


# ── the consumer field reaches PatientInfo on both resolve paths ─────

def test_inline_camelcase_payload_keeps_the_pairs():
    from trials.services.patient_info.resolve import _build_in_memory
    patient = _build_in_memory({'disease': 'multiple myeloma', 'languageSkillConceptIds': [EN_SPEAK, ES_WRITE]})
    assert patient.language_skill_concept_ids == [EN_SPEAK, ES_WRITE]


def test_promop_row_keeps_the_pairs():
    from trials.services.patient_info.promop_adapter import build_patient_info_from_promop_row
    patient = build_patient_info_from_promop_row({
        'disease': 'Multiple Myeloma',
        'languages_skills': 'English language: speak',
        'language_skill_concept_ids': [EN_SPEAK],
    })
    assert patient.language_skill_concept_ids == [EN_SPEAK]
