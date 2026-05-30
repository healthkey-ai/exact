"""
Tests for disease-aware therapyOutcome in /form-settings/ (#60 / #70).

Calling /form-settings/?disease=BC must downgrade the union therapyOutcome
enum to the BC-applicable 4-value subset (CR / PR / SD / PD). All other
diseases (MM / FL / CLL / MCL) still see the full 7-value enum. Without
this override, BC patients on a frontend that reads /form-settings/
therapyOutcome see MM-specific IMWG categories (sCR / VGPR / MRD) that
don't apply clinically.
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

    def test_bc_returns_recist_only(self):
        result = ValueOptions().therapy_outcomes_by_disease_code('BC')
        assert set(result.keys()) == {'CR', 'PR', 'SD', 'PD'}

    @pytest.mark.parametrize('code', ['MM', 'FL', 'CLL', 'MCL'])
    def test_non_bc_returns_full_enum(self, code):
        result = ValueOptions().therapy_outcomes_by_disease_code(code)
        # Full IMWG set: CR + sCR + VGPR + PR + MRD + SD + PD.
        assert set(result.keys()) == {'CR', 'sCR', 'VGPR', 'PR', 'MRD', 'SD', 'PD'}

    def test_unknown_disease_falls_through_to_union(self):
        result = ValueOptions().therapy_outcomes_by_disease_code('UNKNOWN')
        assert set(result.keys()) == {'CR', 'sCR', 'VGPR', 'PR', 'MRD', 'SD', 'PD'}

    def test_lowercase_disease_code_is_normalized(self):
        # therapy_outcomes_by_disease_code uppercases the input; lowercase 'bc'
        # must yield the BC subset.
        result = ValueOptions().therapy_outcomes_by_disease_code('bc')
        assert set(result.keys()) == {'CR', 'PR', 'SD', 'PD'}


class TestFormSettingsTherapyOutcomeOverride:
    """Integration-level: FormSettingsViewSet.list substitutes therapyOutcome."""

    @pytest.mark.django_db
    def test_no_disease_param_returns_union(self):
        data = _form_settings_response('')
        codes = [opt['value'] for opt in data['therapyOutcome']['options']]
        assert set(codes) == {'CR', 'sCR', 'VGPR', 'PR', 'MRD', 'SD', 'PD'}

    @pytest.mark.django_db
    def test_bc_disease_downgrades_therapy_outcome_to_recist(self):
        data = _form_settings_response('breast cancer')
        codes = [opt['value'] for opt in data['therapyOutcome']['options']]
        assert set(codes) == {'CR', 'PR', 'SD', 'PD'}, \
            'BC ?disease= must downgrade therapyOutcome to RECIST 4-value subset'

    @pytest.mark.django_db
    @pytest.mark.parametrize('disease,_code', [
        ('multiple myeloma', 'MM'),
        ('follicular lymphoma', 'FL'),
        ('chronic lymphocytic leukemia', 'CLL'),
        ('mantle cell lymphoma', 'MCL'),
    ])
    def test_non_bc_disease_keeps_full_enum(self, disease, _code):
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
        if shortcode == 'BC':
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
