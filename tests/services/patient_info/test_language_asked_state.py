"""The third language state: asked, and not able (P1-1, #591).

Worked examples from `language_capability`. Each one is checked on every
surface that decides a trial's fate -- the search (`filter_by_patient_info`),
the per-trial matcher status, and the potential-attrs SQL -- and the three must
agree: kept by search <=> matcher status is not `not_matched`; potential in the
count SQL <=> matcher status is `unknown`.
"""
import pytest
from django.db import models
from django.db.models.expressions import RawSQL

from tests.factories import TrialFactory
from trials.models import Trial
from trials.services.patient_info.language_capability import verdict
from trials.services.patient_info.resolve import _build_in_memory
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from trials.services.user_to_trial_attrs_mapper import UserToTrialAttrsMapper

pytestmark = pytest.mark.django_db

NULLS = {f'{lang}_{skill}': None for lang in ('english', 'spanish')
         for skill in ('speak', 'read', 'write', 'understand')}


def promop(**booleans):
    return _build_in_memory({'languages_skills': 'English language: speak', **NULLS, **booleans})


def surfaces(trial, patient):
    """(kept by search, matcher status, potential by count SQL, eligible by count SQL)."""
    scope, _traces = Trial.objects.filter(id=trial.id).filter_by_patient_info(patient)
    kept = scope.exists()
    status = UserToTrialAttrMatcher(trial=trial, patient_info=patient).attr_match_status('languages_skills')
    potential_sql, eligible_sql = UserToTrialAttrsMapper().potential_attrs_to_check(patient, with_eligible=True)

    def value(sqls):
        sql = sqls.get('languages_skills')
        if sql is None:
            return None
        return (Trial.objects.filter(id=trial.id)
                .annotate(_v=RawSQL(sql, [], output_field=models.IntegerField()))
                .values_list('_v', flat=True).first())

    return kept, status, value(potential_sql), value(eligible_sql)


def assert_agree(trial, patient):
    kept, status, potential, eligible = surfaces(trial, patient)
    assert kept == (status != 'not_matched'), (kept, status)
    if status == 'unknown':
        assert potential == 1, (status, potential)
    if status == 'matched':
        assert eligible == 1 and potential is None, (status, potential, eligible)
    return kept, status


@pytest.fixture
def en_speak():
    return TrialFactory(languages_skills_required=['speak__en'])


@pytest.fixture
def es_speak():
    return TrialFactory(languages_skills_required=['speak__es'])


@pytest.fixture
def en_or_es_speak():
    return TrialFactory(languages_skills_required=['speak__en', 'speak__es'])


class TestWorkedExamples:
    def test_anna_speaks_english(self, en_speak):
        assert assert_agree(en_speak, promop(english_speak=True)) == (True, 'matched')

    def test_boris_was_never_asked(self, en_speak):
        assert assert_agree(en_speak, promop()) == (True, 'unknown')

    def test_carlos_asked_and_does_not_speak_english(self, en_speak):
        carlos = promop(english_speak=False, english_read=True)
        assert assert_agree(en_speak, carlos) == (False, 'not_matched')

    def test_carlos_against_a_spanish_trial_is_unknown(self, es_speak):
        carlos = promop(english_speak=False, english_read=True)
        assert assert_agree(es_speak, carlos) == (True, 'unknown')

    def test_english_or_spanish_asked_english_only(self, en_or_es_speak):
        patient = promop(english_speak=False, english_write=True)
        assert assert_agree(en_or_es_speak, patient) == (True, 'unknown')

    def test_english_or_spanish_both_asked_neither_spoken(self, en_or_es_speak):
        patient = promop(english_speak=False, english_read=True, spanish_speak=False, spanish_write=True)
        assert assert_agree(en_or_es_speak, patient) == (False, 'not_matched')

    def test_all_eight_false(self, en_speak):
        patient = promop(**{name: False for name in NULLS})
        assert assert_agree(en_speak, patient) == (False, 'not_matched')

    def test_other_language_is_never_asked(self):
        trial = TrialFactory(languages_skills_required=['speak__other'])
        patient = promop(english_speak=False, spanish_speak=False)
        assert assert_agree(trial, patient) == (True, 'unknown')

    def test_no_requirement(self):
        trial = TrialFactory(languages_skills_required=[])
        kept, status, _p, _e = surfaces(trial, promop(english_speak=False))
        assert kept and status == 'not_evaluated'


class TestPatientsAlsoGainTrials:
    """An unasked language is unknown, so a trial that used to be excluded on no
    overlap is now Potential."""

    def test_anna_against_a_spanish_trial_is_kept(self, es_speak):
        assert assert_agree(es_speak, promop(english_speak=True)) == (True, 'unknown')

    def test_anna_against_an_other_language_trial_is_kept(self):
        trial = TrialFactory(languages_skills_required=['speak__other'])
        assert assert_agree(trial, promop(english_speak=True)) == (True, 'unknown')


class TestNonCodeElements:
    """JSON null and blank strings in R are ignored the same way everywhere."""

    @pytest.mark.parametrize('required', [[''], [None], ['  '], [None, '']])
    def test_only_non_codes_is_no_requirement(self, required):
        trial = TrialFactory(languages_skills_required=required)
        carlos = promop(english_speak=False, english_read=True)
        kept, status, potential, _e = surfaces(trial, carlos)
        assert kept and status == 'not_evaluated' and potential is None
        assert verdict(required, [], ['en']) == 'not_evaluated'

    def test_non_codes_beside_a_code_are_ignored(self):
        trial = TrialFactory(languages_skills_required=[None, 'speak__en', ''])
        carlos = promop(english_speak=False, english_read=True)
        assert assert_agree(trial, carlos) == (False, 'not_matched')


class TestNestedQuery:
    """The filter must bind to the INNER alias when the scope becomes a subquery
    (`blank_attribute_records_count` does `pk__in=scope.values('pk')`). A
    hard-coded table name there made a correlated subquery that timed out."""

    def test_pk_in_subquery_uses_the_inner_alias(self, en_speak):
        carlos = promop(english_speak=False, english_read=True)
        scope = Trial.objects.all().eligible_for_languages_skills([], asked=['en'])
        nested = Trial.objects.filter(pk__in=scope.values('pk'))
        # The outer SELECT lists every column; only the subquery holds the filter.
        inner = str(nested.query).split(' IN (SELECT ', 1)[1]
        assert 'U0."languages_skills_required"' in inner
        assert '"trials_trial"."languages_skills_required"' not in inner
        assert list(nested) == []  # en_speak excluded for an asked-English, no-code patient

    def test_count_through_the_nested_path_agrees(self, en_speak, es_speak):
        scope = Trial.objects.all().eligible_for_languages_skills([], asked=['en'])
        nested = Trial.objects.filter(pk__in=scope.values('pk'))
        assert set(nested.values_list('id', flat=True)) == {es_speak.id}


class TestFillIn:
    """"languages" is offered only where this trial's language verdict is unknown."""

    def asks(self, trial, patient):
        return any(i['userAttributeName'] == 'languagesSkills'
                   for i in trial.attrs_to_fill_in({'languages_skills': 1}, patient_info=patient))

    def test_matched_trial_does_not_ask(self, en_speak):
        assert not self.asks(en_speak, promop(english_speak=True))

    def test_unknown_trial_asks(self, es_speak):
        assert self.asks(es_speak, promop(english_speak=True))

    def test_without_asked_set_every_language_trial_may_ask(self, en_speak):
        assert self.asks(en_speak, _build_in_memory({'languages_skills': 'speak__en'}))
        assert self.asks(en_speak, None)


class TestCallersSendingCodesAreUnchanged:
    """No booleans: exactly the legacy two-state overlap."""

    def test_codes_match(self, en_speak):
        patient = _build_in_memory({'languages_skills': 'speak__en'})
        assert patient.languages_asked is None
        assert assert_agree(en_speak, patient) == (True, 'matched')

    def test_codes_that_do_not_overlap_are_excluded_as_before(self, es_speak):
        patient = _build_in_memory({'languages_skills': 'speak__en'})
        kept, status, _p, _e = surfaces(es_speak, patient)
        assert (kept, status) == (False, 'not_matched')


class TestVerdictUnit:
    @pytest.mark.parametrize('required, held, asked, expected', [
        ([], [], ['en'], 'not_evaluated'),
        (['speak__en'], ['speak__en'], ['en'], 'matched'),
        (['speak__en'], [], ['en'], 'not_matched'),
        (['speak__es'], [], ['en'], 'unknown'),
        (['speak__en', 'speak__es'], [], ['en'], 'unknown'),
        (['speak__en', 'speak__es'], [], ['en', 'es'], 'not_matched'),
        (['speak__other'], [], ['en', 'es'], 'unknown'),
        (['garbage'], [], ['en'], 'unknown'),
    ])
    def test_cases(self, required, held, asked, expected):
        assert verdict(required, held, asked) == expected
