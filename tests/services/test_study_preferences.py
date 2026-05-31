"""Tests for StudyPreferences dataclass + query-param parsing."""
from trials.services.study_preferences import (
    StudyPreferences,
    study_preferences_from_query_params,
)


class TestStudyPreferencesParsing:
    def test_trial_purpose_param_populated(self):
        """`?trialPurpose=treatment` should land on `study_info.trial_purpose` (#44)."""
        prefs = study_preferences_from_query_params({'trialPurpose': 'treatment'})
        assert prefs.trial_purpose == 'treatment'

    def test_trial_purpose_param_absent_defaults_to_none(self):
        prefs = study_preferences_from_query_params({})
        assert prefs.trial_purpose is None

    def test_trial_purpose_empty_string_normalized_to_none(self):
        prefs = study_preferences_from_query_params({'trialPurpose': ''})
        assert prefs.trial_purpose is None

    def test_trial_purpose_independent_from_trial_type(self):
        prefs = study_preferences_from_query_params({
            'trialType': 'interventional',
            'trialPurpose': 'treatment',
        })
        assert prefs.trial_type == 'interventional'
        assert prefs.trial_purpose == 'treatment'

    def test_dataclass_default(self):
        assert StudyPreferences().trial_purpose is None
