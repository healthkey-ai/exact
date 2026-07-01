"""Tests for /form-settings/ disease-aware overrides on the 8 lists (#63).

The view applies per-disease subsets for FLIPI / GELF / cytogenic /
molecular / Binet / Richter / tumorBurden / diseaseActivity when
`?disease=` is set. Without these overrides, a BC patient hitting
/form-settings/?disease=BC would still see the union FLIPI / GELF /
Binet / Richter / tumorBurden / diseaseActivity dropdowns even though
those are clinically meaningless for BC.
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


def _values(data, key):
    return {opt['value'] for opt in data[key]['options']}


@pytest.mark.django_db
class TestFormSettingsDiseaseAwareLists:
    def test_no_disease_param_returns_union_for_all_lists(self):
        data = _form_settings_response('')
        v = ValueOptions()
        # Union lists pass through unchanged when no disease is specified.
        assert _values(data, 'flipiScore') == set(v.flipi_scores.keys())
        assert _values(data, 'binetStages') == set(v.binet_stages.keys())
        assert _values(data, 'tumorBurdens') == set(v.tumor_burdens.keys())

    def test_bc_drops_flipi_gelf_binet_richter_activity(self):
        data = _form_settings_response('BC')
        v = ValueOptions()
        # BC patients see only the '' sentinel for each list (or nothing).
        for key, source in [
            ('flipiScore', v.flipi_scores),
            ('gelfCriteriaStatus', v.gelf_criteria_statuses),
            ('binetStages', v.binet_stages),
            ('richterTransformations', v.richter_transformations),
            ('tumorBurdens', v.tumor_burdens),
            ('diseaseActivities', v.disease_activities),
        ]:
            actual = _values(data, key)
            expected = {''} if '' in source else set()
            assert actual == expected, key
        # Cytogenic / molecular are EMPTY for BC (BC uses ER/PR/HER2 + HRD).
        for key, source in [
            ('cytogenicMarkers', v.cytogenic_markers),
            ('molecularMarkers', v.molecular_markers),
        ]:
            actual = _values(data, key)
            expected = {''} if '' in source else set()
            assert actual == expected, key

    def test_fl_keeps_flipi_gelf_tumorburden(self):
        data = _form_settings_response('FL')
        v = ValueOptions()
        assert _values(data, 'flipiScore') == set(v.flipi_scores.keys())
        assert _values(data, 'gelfCriteriaStatus') == set(v.gelf_criteria_statuses.keys())
        assert _values(data, 'tumorBurdens') == set(v.tumor_burdens.keys())
        # FL is a hematological malignancy → cytogenic / molecular full.
        assert _values(data, 'cytogenicMarkers') == set(v.cytogenic_markers.keys())
        assert _values(data, 'molecularMarkers') == set(v.molecular_markers.keys())
        # FL drops CLL-specific Binet / Richter / diseaseActivity.
        assert _values(data, 'binetStages') == ({''} if '' in v.binet_stages else set())

    def test_cll_keeps_binet_richter_tumorburden_activity(self):
        data = _form_settings_response('CLL')
        v = ValueOptions()
        assert _values(data, 'binetStages') == set(v.binet_stages.keys())
        assert _values(data, 'richterTransformations') == set(v.richter_transformations.keys())
        assert _values(data, 'tumorBurdens') == set(v.tumor_burdens.keys())
        assert _values(data, 'diseaseActivities') == set(v.disease_activities.keys())
        assert _values(data, 'cytogenicMarkers') == set(v.cytogenic_markers.keys())
        assert _values(data, 'molecularMarkers') == set(v.molecular_markers.keys())
        # CLL drops FL-specific FLIPI / GELF.
        assert _values(data, 'flipiScore') == ({''} if '' in v.flipi_scores else set())

    def test_mcl_keeps_tumorburden_cytogenic_molecular(self):
        data = _form_settings_response('MCL')
        v = ValueOptions()
        assert _values(data, 'tumorBurdens') == set(v.tumor_burdens.keys())
        assert _values(data, 'cytogenicMarkers') == set(v.cytogenic_markers.keys())
        assert _values(data, 'molecularMarkers') == set(v.molecular_markers.keys())
        # MCL drops CLL-specific Binet / Richter / diseaseActivity.
        assert _values(data, 'binetStages') == ({''} if '' in v.binet_stages else set())

    def test_mm_keeps_cytogenic_molecular(self):
        data = _form_settings_response('MM')
        v = ValueOptions()
        assert _values(data, 'cytogenicMarkers') == set(v.cytogenic_markers.keys())
        assert _values(data, 'molecularMarkers') == set(v.molecular_markers.keys())
        # MM drops FL-specific FLIPI / GELF + CLL-specific lists.
        assert _values(data, 'flipiScore') == ({''} if '' in v.flipi_scores else set())
        assert _values(data, 'binetStages') == ({''} if '' in v.binet_stages else set())

    def test_trial_attributes_still_reads_union(self):
        """Trial-side `*Required`/`*Excluded` aliases must keep showing the
        full union so trials can require any marker independent of the
        current patient's disease. `TrialAttributes.__init__` builds its
        own fresh `ValueOptions().all_options()` (see trial_attributes.py:39),
        so the view-level mutations above don't leak into trial detail
        rendering. Confirm by asserting the union keys still resolve to
        the full set when read directly off a fresh ValueOptions instance.
        """
        v = ValueOptions()
        opts = v.all_options()
        # Source-of-truth: the bare union keys are still present in
        # all_options() and still expose the full union — only the
        # view's per-request `out` dict is mutated for `?disease=`.
        assert {o['value'] for o in opts['cytogenicMarkers']['options']} == set(v.cytogenic_markers.keys())
        assert {o['value'] for o in opts['binetStages']['options']} == set(v.binet_stages.keys())
