"""
Tests for disease-aware therapyOutcome in /form-settings/ (#60 / #70 / #83).

Calling /form-settings/?disease=BC or ?disease=MCL downgrades the union
therapyOutcome enum to the 4-value RECIST / Cheson-Lugano subset
(CR / PR / SD / PD). MM / FL / CLL still see the full 7-value IMWG
enum. Without this override, BC or MCL patients on a frontend that
reads /form-settings/ therapyOutcome see MM-specific IMWG categories
(sCR / VGPR / MRD) that don't apply clinically.
"""
import pytest
from unittest.mock import MagicMock

from trials.api.trials_views import FormSettingsViewSet
from trials.services.value_options import ValueOptions


def _form_settings_response(disease_param):
    """Drive FormSettingsViewSet.list with a query-param request, bypass auth."""
    view = FormSettingsViewSet()
    mock_request = MagicMock()
    mock_request.query_params = {'disease': disease_param} if disease_param else {}
    return view.list(mock_request).data


class TestTherapyOutcomesByDiseaseCode:
    """Unit-level: per-disease subsetting in ValueOptions."""

    @pytest.mark.parametrize('code', ['BC', 'MCL'])
    def test_recist_diseases_return_four_value_subset(self, code):
        # BC uses RECIST, MCL uses Cheson 2014 / Lugano 2014 — both yield
        # the same 4-value CR/PR/SD/PD subset.
        result = ValueOptions().therapy_outcomes_by_disease_code(code)
        assert set(result.keys()) == {'CR', 'PR', 'SD', 'PD'}

    @pytest.mark.parametrize('code', ['MM', 'FL', 'CLL'])
    def test_imwg_diseases_return_full_enum(self, code):
        result = ValueOptions().therapy_outcomes_by_disease_code(code)
        # Full IMWG set: CR + sCR + VGPR + PR + MRD + SD + PD.
        assert set(result.keys()) == {'CR', 'sCR', 'VGPR', 'PR', 'MRD', 'SD', 'PD'}

    def test_unknown_disease_falls_through_to_union(self):
        result = ValueOptions().therapy_outcomes_by_disease_code('UNKNOWN')
        assert set(result.keys()) == {'CR', 'sCR', 'VGPR', 'PR', 'MRD', 'SD', 'PD'}

    @pytest.mark.parametrize('lower', ['bc', 'mcl'])
    def test_lowercase_disease_code_is_normalized(self, lower):
        # therapy_outcomes_by_disease_code uppercases the input.
        result = ValueOptions().therapy_outcomes_by_disease_code(lower)
        assert set(result.keys()) == {'CR', 'PR', 'SD', 'PD'}


class TestFormSettingsTherapyOutcomeOverride:
    """Integration-level: FormSettingsViewSet.list substitutes therapyOutcome."""

    @pytest.mark.django_db
    def test_no_disease_param_returns_union(self):
        data = _form_settings_response('')
        codes = [opt['value'] for opt in data['therapyOutcome']['options']]
        assert set(codes) == {'CR', 'sCR', 'VGPR', 'PR', 'MRD', 'SD', 'PD'}

    @pytest.mark.django_db
    @pytest.mark.parametrize('disease', ['breast cancer', 'mantle cell lymphoma'])
    def test_recist_diseases_downgrade_therapy_outcome(self, disease):
        data = _form_settings_response(disease)
        codes = [opt['value'] for opt in data['therapyOutcome']['options']]
        assert set(codes) == {'CR', 'PR', 'SD', 'PD'}, \
            f'{disease!r} ?disease= must downgrade therapyOutcome to 4-value subset'

    @pytest.mark.django_db
    @pytest.mark.parametrize('disease,_code', [
        ('multiple myeloma', 'MM'),
        ('follicular lymphoma', 'FL'),
        ('chronic lymphocytic leukemia', 'CLL'),
    ])
    def test_imwg_diseases_keep_full_enum(self, disease, _code):
        data = _form_settings_response(disease)
        codes = [opt['value'] for opt in data['therapyOutcome']['options']]
        assert set(codes) == {'CR', 'sCR', 'VGPR', 'PR', 'MRD', 'SD', 'PD'}

    @pytest.mark.django_db
    @pytest.mark.parametrize('shortcode', ['MM', 'BC', 'FL', 'CLL', 'MCL'])
    def test_disease_shortcodes_route_through_override(self, shortcode):
        # _normalize_disease_code must accept every supported shortcode, not
        # just MM/BC/FL/CLL. Without MCL in the tuple, ?disease=MCL falls
        # through to disease_code='', the override block is skipped, and the
        # union therapyOutcome leaks out.
        data = _form_settings_response(shortcode)
        codes = [opt['value'] for opt in data['therapyOutcome']['options']]
        if shortcode in ('BC', 'MCL'):
            assert set(codes) == {'CR', 'PR', 'SD', 'PD'}
        else:
            assert set(codes) == {'CR', 'sCR', 'VGPR', 'PR', 'MRD', 'SD', 'PD'}

    @pytest.mark.django_db
    def test_per_disease_outcome_keys_still_present(self):
        # Sanity: the per-disease registry keys remain (they're what the
        # trial-detail layer at trial_attributes._DISEASE_TO_OUTCOME_KEY
        # consumes).
        data = _form_settings_response('')
        for key in ('therapyOutcomeMm', 'therapyOutcomeFl', 'therapyOutcomeBc',
                    'therapyOutcomeCll', 'therapyOutcomeMcl'):
            assert key in data, f'Missing {key} in form-settings response'
