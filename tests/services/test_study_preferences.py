"""Tests for StudyPreferences dataclass + query-param parsing."""
from django.http import QueryDict

from trials.services.study_preferences import (
    StudyPreferences,
    study_preferences_from_query_params,
)


class TestStudyPreferencesParsing:
    def test_trial_purpose_param_populated(self):
        """`?trialPurpose=treatment` should land on `study_info.trial_purpose` (#44)."""
        prefs = study_preferences_from_query_params({'trialPurpose': 'treatment'})
        assert prefs.trial_purpose == ['treatment']

    def test_trial_purpose_param_absent_defaults_to_empty(self):
        prefs = study_preferences_from_query_params({})
        assert prefs.trial_purpose == []

    def test_trial_purpose_empty_string_normalized_away(self):
        prefs = study_preferences_from_query_params({'trialPurpose': ''})
        assert prefs.trial_purpose == []

    def test_trial_purpose_repeated_param(self):
        """Several purposes at once (CB #4663), the therapy_id spelling.

        Through a real QueryDict, which is what DRF hands the parser.
        """
        params = QueryDict('trialPurpose=treatment&trialPurpose=supportive_care')
        prefs = study_preferences_from_query_params(params)
        assert prefs.trial_purpose == ['treatment', 'supportive_care']

    def test_trial_purpose_blank_repeat_dropped(self):
        params = QueryDict('trialPurpose=&trialPurpose=treatment')
        assert study_preferences_from_query_params(params).trial_purpose == ['treatment']

    def test_trial_purpose_none_value_is_not_a_code(self):
        # A dict-like caller can hand us None where a QueryDict never would;
        # stringified it would become the code 'None' and match nothing.
        prefs = study_preferences_from_query_params({'trialPurpose': None})
        assert prefs.trial_purpose == []

    def test_trial_purpose_deduplicated_case_insensitively(self):
        # The queryset matches iexact, so these are one purpose, not two.
        prefs = study_preferences_from_query_params(
            {'trialPurpose': 'treatment,TREATMENT'}
        )
        assert prefs.trial_purpose == ['treatment']

    def test_trial_purpose_comma_separated(self):
        prefs = study_preferences_from_query_params(
            {'trialPurpose': 'treatment, supportive_care'}
        )
        assert prefs.trial_purpose == ['treatment', 'supportive_care']

    def test_trial_purpose_deduplicated(self):
        prefs = study_preferences_from_query_params(
            {'trialPurpose': 'treatment,treatment,,supportive_care'}
        )
        assert prefs.trial_purpose == ['treatment', 'supportive_care']

    def test_trial_purpose_is_capped(self):
        # Each code becomes another OR clause and the taxonomy has nine
        # purposes, so a query string must not be able to ask for thousands.
        many = ','.join(f'code_{i}' for i in range(200))
        prefs = study_preferences_from_query_params({'trialPurpose': many})
        assert len(prefs.trial_purpose) == 50
        assert prefs.trial_purpose[0] == 'code_0'

    def test_trial_purpose_independent_from_trial_type(self):
        prefs = study_preferences_from_query_params({
            'trialType': 'interventional',
            'trialPurpose': 'treatment',
        })
        assert prefs.trial_type == 'interventional'
        assert prefs.trial_purpose == ['treatment']

    def test_dataclass_default(self):
        assert StudyPreferences().trial_purpose == []

    def test_dataclass_default_is_not_shared(self):
        first = StudyPreferences()
        first.trial_purpose.append('treatment')
        assert StudyPreferences().trial_purpose == []
