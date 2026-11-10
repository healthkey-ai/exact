"""PROMOP's language booleans replace its display string (#591).

PROMOP serves `languages_skills` as "English language: read, speak; Spanish
language: speak", which can never equal a trial code like `speak__en`, so a
PROMOP patient who recorded a language failed every trial with a language
requirement. The same record carries eight three-valued booleans
(`english_speak` … `spanish_understand`, NULL = never asked); these tests pin
that they, not the string, reach the matcher.
"""
import pytest
from rest_framework.exceptions import ValidationError

from trials.models import Trial
from trials.querysets.trial import _csv
from trials.services.patient_info.promop_adapter import build_patient_info_from_promop_row
from trials.services.patient_info.resolve import (
    _build_in_memory,
    languages_skills_from_capabilities,
)
from tests.factories import TrialFactory

DISPLAY = 'English language: read, speak; Spanish language: speak'
NOT_ASKED = {f'{lang}_{skill}': None for lang in ('english', 'spanish')
             for skill in ('speak', 'read', 'write', 'understand')}


def promop(**held):
    """A PROMOP-shaped record: display string plus all eight booleans."""
    record = {'languages_skills': DISPLAY, **NOT_ASKED}
    record.update(held)
    return record


class TestTheBooleansReplaceTheString:
    def test_true_capabilities_become_codes(self):
        out = languages_skills_from_capabilities(promop(
            english_speak=True, english_read=True, english_write=False, english_understand=False))
        assert out['languages_skills'] == 'speak__en'
        # 2omop keeps the booleans for the OMOP builder; the field filter drops them.
        assert all(name in out for name in NOT_ASKED)

    def test_only_trial_vocabulary_becomes_codes(self):
        # read/understand match no trial and the "Yours" cell could not label
        # them, so they are not coded. English still counts as asked, so this
        # patient is not_matched on a speak__en trial (#605).
        out = languages_skills_from_capabilities(promop(
            english_read=True, english_understand=True, english_speak=False, english_write=False))
        assert out['languages_skills'] is None

    def test_nothing_asked_is_no_value(self):
        assert languages_skills_from_capabilities(promop())['languages_skills'] is None

    def test_only_a_real_true_counts(self):
        out = languages_skills_from_capabilities(promop(english_speak='false', spanish_write='TRUE'))
        assert out['languages_skills'] == 'write__es'

    @pytest.mark.parametrize('value, held, asked', [
        (True, 'speak__en', 'en'), (1, 'speak__en', 'en'), ('1', 'speak__en', 'en'),
        ('t', 'speak__en', 'en'), (' Yes ', 'speak__en', 'en'),
        (False, None, 'en'), (0, None, 'en'), ('f', None, 'en'), ('no', None, 'en'),
        ('maybe', None, None), (2, None, None),
    ])
    def test_the_same_tolerant_parsing_as_the_omop_builder(self, value, held, asked):
        out = languages_skills_from_capabilities(promop(english_speak=value))
        assert out['languages_skills'] == held
        assert out.get('languages_asked') == asked

    def test_a_caller_sending_codes_is_left_alone(self):
        data = {'languages_skills': 'speak__en,write__es'}
        assert languages_skills_from_capabilities(data) is data

    def test_codes_survive_a_form_that_sends_every_boolean_as_null(self):
        out = languages_skills_from_capabilities({'languages_skills': 'speak__en', **NOT_ASKED})
        assert out['languages_skills'] == 'speak__en'

    def test_codes_survive_empty_strings_from_a_form(self):
        blank = {name: '' for name in NOT_ASKED}
        out = languages_skills_from_capabilities({'languages_skills': 'speak__en', **blank})
        assert out['languages_skills'] == 'speak__en'

    def test_a_boolean_with_a_value_wins_over_sent_codes(self):
        out = languages_skills_from_capabilities({'languages_skills': 'speak__es', 'english_write': True})
        assert out['languages_skills'] == 'write__en'

    def test_idempotent(self):
        once = languages_skills_from_capabilities(promop(spanish_speak=True))
        assert once['languages_skills'] == 'speak__es'
        assert languages_skills_from_capabilities(dict(once)) == once


class TestBothPaths:
    def test_inline_payload(self):
        pi = _build_in_memory(promop(english_speak=True))
        assert pi.languages_skills == 'speak__en'

    def test_inline_camel_case(self):
        pi = _build_in_memory({'languagesSkills': DISPLAY, 'englishSpeak': True})
        assert pi.languages_skills == 'speak__en'

    def test_promop_row_with_nothing_asked_drops_the_string(self):
        # The fetched-row path strips None values before `_build_in_memory`,
        # so only the adapter still sees that this is a PROMOP record.
        pi = build_patient_info_from_promop_row(promop())
        assert pi.languages_skills is None

    def test_promop_row_with_a_capability(self):
        pi = build_patient_info_from_promop_row(promop(english_write=True, english_speak=False))
        assert pi.languages_skills == 'write__en'


@pytest.mark.django_db
class TestTheRequirementMatches:
    @pytest.fixture
    def speaks_english_trial(self):
        return TrialFactory(languages_skills_required=['speak__en'])

    def eligible(self, record):
        pi = _build_in_memory(record)
        return list(Trial.objects.eligible_for_languages_skills(_csv(pi.languages_skills)))

    def test_speaker_matches(self, speaks_english_trial):
        assert speaks_english_trial in self.eligible(promop(english_speak=True))

    def test_writer_only_does_not(self, speaks_english_trial):
        assert speaks_english_trial not in self.eligible(promop(english_write=True, english_speak=False))

    def test_not_asked_is_not_filtered(self, speaks_english_trial):
        assert speaks_english_trial in self.eligible(promop())
