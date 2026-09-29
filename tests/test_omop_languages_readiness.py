"""EXACT_OMOP_LANGUAGES readiness gate (CB #5350).

The flag takes effect only when the language vocab is loaded, the trial column
is backfilled, and the trial pairs are ones EXACT's vocab can produce. Otherwise
queryset and matcher both stay on the legacy path and an ERROR is logged.
"""
import logging
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from exact_matching.omop.languages_match_profile import omop_languages_enabled
from trials.models import Language, Trial
from trials.services.loaders.load_lang_options import LoadLangOptions
from trials.services.omop import languages_readiness
from trials.services.omop.languages_readiness import check_languages_readiness, reset_readiness_cache
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db

EN_SPEAK = '4180186:2100007853'


@pytest.fixture
def lang_vocab(db):
    LoadLangOptions().load_all()
    call_command('load_language_omop_concept_ids', stdout=StringIO())


@pytest.fixture
def flag_on(settings):
    settings.EXACT_OMOP_LANGUAGES = True


def _probe():
    """A backfilled trial and a patient whose two vocabularies disagree on purpose:
    OMOP path -> matched (pair overlaps); legacy path -> not_matched."""
    trial = TrialFactory(languages_skills_required=['speak__en'], omop_languages_skills_required=[EN_SPEAK])
    patient = PatientInfo(languages_skills='write__es', language_skill_concept_ids=[EN_SPEAK])
    return trial, patient


def _path(trial, patient):
    scope, _ = Trial.objects.filter(id=trial.id).filter_by_patient_info(patient)
    kept = scope.exists()
    status = UserToTrialAttrMatcher(trial, patient).attr_match_status('languages_skills')
    if kept and status == 'matched':
        return 'omop'
    if not kept and status == 'not_matched':
        return 'legacy'
    return f'mixed(kept={kept}, status={status})'


def _break_vocab():
    Language.objects.filter(code='es').update(omop_concept_id=None)


def _break_backfill():
    TrialFactory(languages_skills_required=['write__en'], omop_languages_skills_required=[])


def _break_backfill_reverse():
    # requirement removed, OMOP column not refreshed
    TrialFactory(languages_skills_required=[], omop_languages_skills_required=[EN_SPEAK])


def _break_drift():
    TrialFactory(languages_skills_required=[], omop_languages_skills_required=['999:888'])


@pytest.mark.usefixtures('flag_on', 'lang_vocab')
class TestGate:
    def test_all_checks_pass_gives_the_omop_path(self, caplog):
        trial, patient = _probe()
        with caplog.at_level(logging.ERROR):
            assert _path(trial, patient) == 'omop'
        assert 'not ready' not in caplog.text

    @pytest.mark.parametrize('breaker, reason', [
        (_break_vocab, 'vocab:'),
        (_break_backfill, 'backfill:'),
        (_break_backfill_reverse, 'backfill:'),
        (_break_drift, 'drift:'),
    ])
    def test_each_failing_check_falls_back_to_legacy_and_logs(self, caplog, breaker, reason):
        trial, patient = _probe()
        breaker()
        with caplog.at_level(logging.ERROR, logger=languages_readiness.__name__):
            assert _path(trial, patient) == 'legacy'
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1  # once per TTL window, not per surface
        assert reason in errors[0].getMessage()

    def test_unloaded_vocab_is_not_ready(self, db):
        Language.objects.update(omop_concept_id=None)
        report = check_languages_readiness()
        assert not report.ok and report.reasons[0].startswith('vocab:')


@pytest.mark.usefixtures('lang_vocab')
class TestReport:
    def test_ready(self):
        _probe()
        assert check_languages_readiness().ok

    def test_other_languages_are_a_warning_not_a_failure(self):
        _probe()
        TrialFactory(languages_skills_required=['speak__fr', 'write__other'], omop_languages_skills_required=[])
        report = check_languages_readiness()
        assert report.ok
        assert report.warnings and 'fr, other' in report.warnings[0]

    def test_backfill_check_is_a_constant_number_of_queries(self, django_assert_max_num_queries):
        for i in range(5):
            TrialFactory(languages_skills_required=['speak__en'], omop_languages_skills_required=[EN_SPEAK])
        with django_assert_max_num_queries(5):
            check_languages_readiness()


@pytest.mark.usefixtures('flag_on', 'lang_vocab')
class TestCache:
    def test_cached_within_the_ttl_and_reset_clears_it(self):
        _probe()
        assert omop_languages_enabled()
        _break_drift()
        assert omop_languages_enabled()  # still the cached answer
        reset_readiness_cache()
        assert not omop_languages_enabled()

    def test_recomputed_after_the_ttl(self, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(languages_readiness.time, 'monotonic', lambda: clock[0])
        _probe()
        assert omop_languages_enabled()
        _break_drift()
        clock[0] += languages_readiness.READINESS_TTL_SECONDS - 1
        assert omop_languages_enabled()
        clock[0] += 2
        assert not omop_languages_enabled()


def test_flag_off_makes_no_readiness_query(settings, django_assert_num_queries, monkeypatch):
    settings.EXACT_OMOP_LANGUAGES = False
    monkeypatch.setattr(languages_readiness, 'check_languages_readiness',
                        lambda: pytest.fail('readiness consulted with the flag off'))
    with django_assert_num_queries(0):
        assert omop_languages_enabled() is False


@pytest.mark.usefixtures('lang_vocab')
class TestCommand:
    def test_ready_exits_zero(self):
        _probe()
        out = StringIO()
        call_command('check_omop_languages_readiness', stdout=out)
        assert 'ready' in out.getvalue()

    def test_not_ready_exits_non_zero(self):
        _probe()
        _break_drift()
        out = StringIO()
        with pytest.raises(CommandError):
            call_command('check_omop_languages_readiness', stdout=out)
        assert 'drift:' in out.getvalue()



@pytest.mark.usefixtures('lang_vocab')
def test_null_list_elements_do_not_break_the_check():
    _probe()
    TrialFactory(languages_skills_required=[None, 'speak__fr'], omop_languages_skills_required=[None])
    report = check_languages_readiness()
    assert report.ok
    assert 'fr' in report.warnings[0]


@pytest.mark.usefixtures('flag_on', 'lang_vocab')
def test_a_raising_check_falls_back_to_legacy(monkeypatch, caplog):
    trial, patient = _probe()

    def boom():
        raise RuntimeError('db gone')
    monkeypatch.setattr(languages_readiness, 'check_languages_readiness', boom)
    with caplog.at_level(logging.ERROR, logger=languages_readiness.__name__):
        assert _path(trial, patient) == 'legacy'
    assert 'readiness check raised' in caplog.text
