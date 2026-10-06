"""Language-skill (language, skill) concept pairs: mapping CSV, loader, backfill (ported from CB #5350)."""
import csv
import os
from io import StringIO

import pytest
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError

from trials.models import Language, LanguageSkillLevel
from trials.services.loaders.load_lang_options import LoadLangOptions
from trials.services.omop.languages import (
    build_omop_languages_skills,
    language_skill_concept_key,
    load_concept_ids,
)
from trials.services.value_options import ValueOptions
from tests.factories import TrialFactory

ENGLISH, SPANISH = 4180186, 4182511
SPEAK, WRITE = 2_100_007_853, 2_100_007_855
MAPPING_DIR = os.path.join(settings.BASE_DIR, 'docs', 'omop', 'mapping')
LANGUAGE_CSV = os.path.join(MAPPING_DIR, 'language_omop_mapping.csv')


def _rows(path):
    with open(path) as f:
        return list(csv.DictReader(f))


@pytest.fixture
def lang_vocab(db):
    LoadLangOptions().load_all()
    call_command('load_language_omop_concept_ids', stdout=StringIO())


class TestMappingCsv:
    def test_same_format_as_the_therapy_mapping(self):
        with open(LANGUAGE_CSV) as f, open(os.path.join(MAPPING_DIR, 'therapy_omop_mapping.csv')) as g:
            assert f.readline() == g.readline()

    def test_levels_and_matches_are_known(self):
        for row in _rows(LANGUAGE_CSV):
            assert row['level'] in {'language', 'skill'}
            assert row['match'] in {'auto', 'curated', 'llm', 'needs_review', 'no_omop'}
            assert bool(row['omop_concept_id']) == (row['match'] not in {'needs_review', 'no_omop'})


@pytest.mark.django_db
def test_every_seeded_vocab_code_has_a_csv_row():
    LoadLangOptions().load_all()
    in_csv = {(r['level'], r['cb_code']) for r in _rows(LANGUAGE_CSV)}
    seeded = {('language', c) for c in Language.objects.values_list('code', flat=True)}
    seeded |= {('skill', c) for c in LanguageSkillLevel.objects.values_list('code', flat=True)}
    assert seeded - in_csv == set()


@pytest.mark.django_db
class TestLoader:
    def test_sets_concept_ids(self, lang_vocab):
        assert Language.objects.get(code='en').omop_concept_id == ENGLISH
        assert Language.objects.get(code='es').omop_concept_id == SPANISH
        assert Language.objects.get(code='other').omop_concept_id is None  # no_omop
        assert LanguageSkillLevel.objects.get(code='speak').omop_concept_id == SPEAK
        assert LanguageSkillLevel.objects.get(code='write').omop_concept_id == WRITE

    def test_dry_run_does_not_write(self, db):
        LoadLangOptions().load_all()
        out = StringIO()
        call_command('load_language_omop_concept_ids', '--dry-run', stdout=out)
        assert Language.objects.get(code='en').omop_concept_id is None
        assert '[dry-run] language: set=2' in out.getvalue()

    def test_clears_what_the_csv_does_not_map(self, lang_vocab):
        # a stale id on a no_omop row, and a language added in admin with no CSV row
        Language.objects.filter(code='other').update(omop_concept_id=123)
        Language.objects.create(code='fr', title='French', omop_concept_id=456)
        out = StringIO()
        call_command('load_language_omop_concept_ids', stdout=out)
        assert Language.objects.get(code='other').omop_concept_id is None
        assert Language.objects.get(code='fr').omop_concept_id is None
        assert 'language: set=0 unchanged=2 cleared=2' in out.getvalue()

    def test_duplicate_row_is_refused(self, db, tmp_path):
        LoadLangOptions().load_all()
        path = tmp_path / 'dup.csv'
        path.write_text(open(LANGUAGE_CSV).read() + 'language,en,English,,,,no_omop\n')
        with pytest.raises(CommandError, match='duplicate row for language:en'):
            call_command('load_language_omop_concept_ids', '--csv', str(path), stdout=StringIO())
        assert Language.objects.get(code='en').omop_concept_id is None  # nothing written

    def test_same_concept_id_twice_at_one_level_is_refused(self, lang_vocab, tmp_path):
        # a pre-existing mapping must survive the refused load
        path = tmp_path / 'twice.csv'
        path.write_text(open(LANGUAGE_CSV).read().replace(
            'language,other,Other,,,,no_omop', 'language,other,Other,4180186,English language,SNOMED,curated'))
        with pytest.raises(CommandError, match='concept_id 4180186 accepted twice at level language'):
            call_command('load_language_omop_concept_ids', '--csv', str(path), stdout=StringIO())
        assert Language.objects.get(code='other').omop_concept_id is None

    def test_same_concept_id_at_different_levels_is_allowed(self, db, tmp_path):
        LoadLangOptions().load_all()
        path = tmp_path / 'cross.csv'
        path.write_text(open(LANGUAGE_CSV).read().replace(
            'skill,write,Write,2100007855', 'skill,write,Write,4182511'))
        call_command('load_language_omop_concept_ids', '--csv', str(path), stdout=StringIO())

    def test_idempotent(self, lang_vocab):
        out = StringIO()
        call_command('load_language_omop_concept_ids', stdout=out)
        assert 'language: set=0 unchanged=3 cleared=0' in out.getvalue()
        assert 'skill: set=0 unchanged=2 cleared=0' in out.getvalue()


@pytest.mark.django_db
class TestLanguageSkillConceptKey:
    @pytest.mark.parametrize('code, key', [
        ('speak__en', f'{ENGLISH}:{SPEAK}'),
        ('write__en', f'{ENGLISH}:{WRITE}'),
        ('speak__es', f'{SPANISH}:{SPEAK}'),
        ('write__es', f'{SPANISH}:{WRITE}'),
    ])
    def test_maps_each_pair(self, lang_vocab, code, key):
        assert language_skill_concept_key(code, load_concept_ids()) == key

    @pytest.mark.parametrize('code', [
        'speak__other',  # names no language
        'en_speak',      # the spelling the old model comment claimed
        'read__en',      # a PROMOP skill CB does not offer
        '', None, 'speak', '__en', 'speak__',
    ])
    def test_unmapped(self, lang_vocab, code):
        assert language_skill_concept_key(code, load_concept_ids()) is None

    def test_nothing_maps_before_the_loader_runs(self, db):
        LoadLangOptions().load_all()
        assert language_skill_concept_key('speak__en', load_concept_ids()) is None

    def test_follows_the_vocab_row(self, lang_vocab):
        Language.objects.filter(code='en').update(omop_concept_id=999)
        assert language_skill_concept_key('speak__en', load_concept_ids()) == f'999:{SPEAK}'


@pytest.mark.django_db
def test_every_vocab_option_but_other_has_a_pair(lang_vocab):
    # The codes the options endpoint actually offers, built from the real loader,
    # so a new language or skill added there without a CSV row fails here.
    codes = [code for code in ValueOptions().languages_skills if code]
    assert codes, 'loader produced no options'
    concept_ids = load_concept_ids()
    unmapped = [code for code in codes if language_skill_concept_key(code, concept_ids) is None]
    assert unmapped == ['speak__other', 'write__other']


@pytest.mark.django_db
class TestBuildOmopLanguagesSkills:
    def test_maps_dedupes_and_reports_unmapped(self, lang_vocab):
        trial = TrialFactory(languages_skills_required=['write__es', 'speak__en', 'speak__en', 'speak__other'])
        values, unmapped = build_omop_languages_skills(trial)
        assert values == {'omop_languages_skills_required': [f'{ENGLISH}:{SPEAK}', f'{SPANISH}:{WRITE}']}
        assert unmapped == ['speak__other']

    def test_empty(self, lang_vocab):
        trial = TrialFactory(languages_skills_required=[])
        assert build_omop_languages_skills(trial) == ({'omop_languages_skills_required': []}, [])

    def test_non_string_elements_are_unmapped_not_fatal(self, lang_vocab):
        # ingest can leave {'code': ...} dicts in multi-option lists
        trial = TrialFactory(languages_skills_required=['speak__en', {'code': 'write__en'}, 7])
        values, unmapped = build_omop_languages_skills(trial)
        assert values == {'omop_languages_skills_required': [f'{ENGLISH}:{SPEAK}']}
        assert unmapped == ['7', "{'code': 'write__en'}"]

    def test_pairs_do_not_cross(self, lang_vocab):
        # speaks English + writes Spanish must not yield a "speaks Spanish" pair
        trial = TrialFactory(languages_skills_required=['speak__en', 'write__es'])
        values, _ = build_omop_languages_skills(trial)
        assert f'{SPANISH}:{SPEAK}' not in values['omop_languages_skills_required']


@pytest.mark.django_db
class TestBackfillCommand:
    def test_backfill_populates(self, lang_vocab):
        trial = TrialFactory(languages_skills_required=['write__en'])
        call_command('backfill_omop_languages_skills_column', stdout=StringIO())
        trial.refresh_from_db()
        assert trial.omop_languages_skills_required == [f'{ENGLISH}:{WRITE}']

    def test_dry_run_does_not_write_and_lists_unmapped(self, lang_vocab):
        trial = TrialFactory(languages_skills_required=['speak__es', 'en_speak'])
        out = StringIO()
        call_command('backfill_omop_languages_skills_column', '--dry-run', stdout=out)
        trial.refresh_from_db()
        assert trial.omop_languages_skills_required == []
        assert 'dry-run' in out.getvalue()
        assert "'en_speak': 1" in out.getvalue()

    def test_reports_requirements_that_map_to_nothing(self, lang_vocab):
        TrialFactory(languages_skills_required=['speak__other'])
        TrialFactory(languages_skills_required=['speak__en', 'speak__other'])
        TrialFactory(languages_skills_required=[])  # no requirement to lose
        out = StringIO()
        call_command('backfill_omop_languages_skills_column', '--dry-run', stdout=out)
        assert 'maps to no pair (empty = no requirement at cutover): 1' in out.getvalue()

    def test_warns_when_the_vocab_is_not_loaded(self, db):
        LoadLangOptions().load_all()
        TrialFactory(languages_skills_required=['speak__en'])
        out = StringIO()
        call_command('backfill_omop_languages_skills_column', '--dry-run', stdout=out)
        assert 'no OMOP concept_ids loaded' in out.getvalue()

    def test_no_warning_once_loaded(self, lang_vocab):
        out = StringIO()
        call_command('backfill_omop_languages_skills_column', '--dry-run', stdout=out)
        assert 'no OMOP concept_ids loaded' not in out.getvalue()

    def test_clears_stale_pairs_when_the_requirement_is_emptied(self, lang_vocab):
        trial = TrialFactory(languages_skills_required=[])
        type(trial).objects.filter(pk=trial.pk).update(omop_languages_skills_required=[f'{ENGLISH}:{SPEAK}'])
        call_command('backfill_omop_languages_skills_column', stdout=StringIO())
        trial.refresh_from_db()
        assert trial.omop_languages_skills_required == []

    def test_idempotent(self, lang_vocab):
        TrialFactory(languages_skills_required=['speak__en'])
        call_command('backfill_omop_languages_skills_column', stdout=StringIO())
        out = StringIO()
        call_command('backfill_omop_languages_skills_column', stdout=out)
        assert 'updated 0' in out.getvalue()
