"""
Tests for compare_trials management command helpers.

Covers:
- _q() SQL escaping
- _study_preferences_from_cb() field mapping
- Zip priority logic (JSON zip > CB geo > absent)
"""
import pytest

from trials.management.commands.compare_trials import _q, _study_preferences_from_cb


# ---------------------------------------------------------------------------
# _q — SQL string escaping
# ---------------------------------------------------------------------------

class TestSqlEscaping:
    def test_plain_string_unchanged(self):
        assert _q("hello") == "hello"

    def test_single_quote_doubled(self):
        assert _q("O'Brien") == "O''Brien"

    def test_multiple_single_quotes(self):
        assert _q("it's O'Brien's") == "it''s O''Brien''s"

    def test_empty_string(self):
        assert _q("") == ""

    def test_no_special_chars_besides_quotes(self):
        # Semicolons, dashes, etc. pass through — only quotes are escaped
        assert _q("a-b; c") == "a-b; c"


# ---------------------------------------------------------------------------
# _study_preferences_from_cb — reference study_info → StudyPreferences
# ---------------------------------------------------------------------------

class TestStudyPreferencesFromCb:
    def test_empty_dict_gives_defaults(self):
        prefs = _study_preferences_from_cb({})
        assert prefs.search_title is None
        assert prefs.search_disease is None
        assert prefs.distance is None
        assert prefs.distance_units == 'miles'
        assert prefs.validated_only is False

    def test_maps_camel_case_search_fields(self):
        prefs = _study_preferences_from_cb({
            'searchTitle': 'Phase 3 trial',
            'searchDisease': 'breast cancer',
            'searchTreatment': 'chemotherapy',
        })
        assert prefs.search_title == 'Phase 3 trial'
        assert prefs.search_disease == 'breast cancer'
        assert prefs.search_treatment == 'chemotherapy'

    def test_maps_sponsor_register_study_id(self):
        prefs = _study_preferences_from_cb({
            'sponsor': 'ACME Corp',
            'register': 'clinicaltrials.gov',
            'studyId': 'NCT12345',
        })
        assert prefs.sponsor == 'ACME Corp'
        assert prefs.register == 'clinicaltrials.gov'
        assert prefs.study_id == 'NCT12345'

    def test_maps_trial_type_study_type_phase(self):
        prefs = _study_preferences_from_cb({
            'trialType': 'Interventional',
            'studyType': 'Double Blind',
            'phase': 'III',
        })
        assert prefs.trial_type == 'Interventional'
        assert prefs.study_type == 'Double Blind'
        assert prefs.phase == 'III'

    def test_maps_distance_as_float(self):
        prefs = _study_preferences_from_cb({'distance': '50', 'distanceUnits': 'km'})
        assert prefs.distance == 50.0
        assert prefs.distance_units == 'km'

    def test_invalid_distance_becomes_none(self):
        prefs = _study_preferences_from_cb({'distance': 'far away'})
        assert prefs.distance is None

    def test_none_distance_stays_none(self):
        prefs = _study_preferences_from_cb({'distance': None})
        assert prefs.distance is None

    def test_empty_string_fields_become_none(self):
        prefs = _study_preferences_from_cb({'searchTitle': '', 'sponsor': ''})
        assert prefs.search_title is None
        assert prefs.sponsor is None

    def test_validated_only_true(self):
        prefs = _study_preferences_from_cb({'validatedOnly': True})
        assert prefs.validated_only is True

    def test_validated_only_falsy_becomes_false(self):
        prefs = _study_preferences_from_cb({'validatedOnly': 0})
        assert prefs.validated_only is False

    def test_postal_code_intentionally_omitted(self):
        # postalCode is excluded — distance geo-point comes from compare_input.json zipcode,
        # not from the CB study_info dict (the CB postalCode may lag behind).
        prefs = _study_preferences_from_cb({'postalCode': '02169'})
        assert prefs.postal_code is None

    def test_country_and_region_mapped(self):
        prefs = _study_preferences_from_cb({'country': 'US', 'region': 'Northeast'})
        assert prefs.country == 'US'
        assert prefs.region == 'Northeast'

    def test_recruitment_status_mapped(self):
        prefs = _study_preferences_from_cb({'recruitmentStatus': 'RECRUITING'})
        assert prefs.recruitment_status == 'RECRUITING'

    def test_last_update_and_first_enrolment(self):
        prefs = _study_preferences_from_cb({
            'lastUpdate': '2024-01-01',
            'firstEnrolment': '2023-06-01',
        })
        assert prefs.last_update == '2024-01-01'
        assert prefs.first_enrolment == '2023-06-01'


# ---------------------------------------------------------------------------
# Zip priority logic (mirrors compare_trials.Command.handle())
#
# Priority:
#   1. patient['zipcode'] / patient['country_code']   (compare_input.json)
#   2. cb_geo['postalCode'] / cb_geo['country']        (reference_patients_data.json)
#   3. '' / 'US'                                       (defaults)
# ---------------------------------------------------------------------------

def _resolve_zip(patient, cb_geo):
    """Mirror the zip-priority block from compare_trials.Command.handle()."""
    json_zip     = patient.get('zipcode')
    json_country = patient.get('country_code')
    use_zip      = json_zip     or cb_geo.get('postalCode')
    use_country  = json_country or cb_geo.get('country') or 'US'
    return use_zip, use_country


class TestZipPriority:
    def test_json_zip_takes_priority_over_cb_geo(self):
        use_zip, _ = _resolve_zip(
            patient={'zipcode': '02169', 'country_code': 'US'},
            cb_geo={'postalCode': '90210', 'country': 'US'},
        )
        assert use_zip == '02169'

    def test_cb_geo_used_when_json_zip_absent(self):
        use_zip, _ = _resolve_zip(
            patient={},
            cb_geo={'postalCode': '90210', 'country': 'US'},
        )
        assert use_zip == '90210'

    def test_json_country_takes_priority_over_cb_geo(self):
        _, use_country = _resolve_zip(
            patient={'country_code': 'CA'},
            cb_geo={'country': 'US'},
        )
        assert use_country == 'CA'

    def test_cb_geo_country_used_when_json_country_absent(self):
        _, use_country = _resolve_zip(
            patient={},
            cb_geo={'country': 'DE'},
        )
        assert use_country == 'DE'

    def test_country_defaults_to_us_when_both_absent(self):
        _, use_country = _resolve_zip(patient={}, cb_geo={})
        assert use_country == 'US'

    def test_no_zip_when_both_absent(self):
        use_zip, _ = _resolve_zip(patient={}, cb_geo={})
        assert use_zip is None

    def test_empty_string_json_zip_falls_back_to_cb(self):
        # Empty string is falsy — falls through to cb_geo
        use_zip, _ = _resolve_zip(
            patient={'zipcode': ''},
            cb_geo={'postalCode': '06101'},
        )
        assert use_zip == '06101'


# ---------------------------------------------------------------------------
# Zip override warning condition (mirrors the if-branch in handle())
# ---------------------------------------------------------------------------

class TestZipOverrideWarningCondition:
    """
    The command warns when json_zip differs from the CTOMOP DB postal_code.
    This tests the condition logic only, not the Django output.
    """

    def _should_warn(self, use_zip, db_zip):
        return (
            use_zip and db_zip
            and str(use_zip).strip() != str(db_zip).strip()
        )

    def test_warns_when_zips_differ(self):
        assert self._should_warn('02169', '02468') is True

    def test_no_warn_when_zips_match(self):
        assert self._should_warn('02169', '02169') is False

    def test_no_warn_when_use_zip_absent(self):
        assert not self._should_warn(None, '02468')

    def test_no_warn_when_db_zip_absent(self):
        assert not self._should_warn('02169', None)

    def test_no_warn_when_both_absent(self):
        assert not self._should_warn(None, None)

    def test_strips_whitespace_before_compare(self):
        assert self._should_warn(' 02169 ', '02169') is False
