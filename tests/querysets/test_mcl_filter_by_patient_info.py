"""
Regression test for #94 — filter_by_patient_info must not raise when an
MCL patient has any of the 8 MCL custom_search attrs non-blank.

Pre-fix, configs.py marked them with `custom_search=True` (or, for
largest_lesion_size, `custom_search=True` on a `min_max_value` type) but
the queryset had no corresponding `eligible_for_*` method and the
attrs were absent from `_CUSTOM_SEARCH_DISPATCH`. Any MCL patient
flowing through `filter_by_patient_info` with any of them set would
hit `raise Exception(...)` in the dispatch default branch.
"""
import pytest

from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from tests.factories import TrialFactory


class TestMclFilterByPatientInfoSmoke:
    @pytest.mark.django_db
    def test_blank_mcl_patient_does_not_raise(self):
        TrialFactory(disease='mantle cell lymphoma')
        pi = PatientInfo(disease='mantle cell lymphoma')
        # Default values are None / [] — the dispatch handlers must not
        # raise on blanks; they should be no-ops.
        result, _ = Trial.objects.filter_by_patient_info(pi)
        assert result.count() >= 1

    @pytest.mark.django_db
    def test_filled_mcl_patient_does_not_raise(self):
        TrialFactory(disease='mantle cell lymphoma')
        pi = PatientInfo(
            disease='mantle cell lymphoma',
            morphologic_variant='classic',
            largest_lesion_size=5.0,
            disease_behavior='indolent',
            disease_subtype='cmcl',
            extranodal_sites=['bone_marrow', 'gi_tract'],
            mipi_risk='low',
            mipi_c_risk='low',
            bulky_disease_criteria=['bulky_lesion_5cm'],
        )
        # Pre-fix this would have raised
        #   Exception('type ... is not supported for user_attr "morphologic_variant"')
        # at the first MCL attr the loop encountered. Post-fix the
        # dispatch routes each attr through its eligible_for_* method.
        result, _ = Trial.objects.filter_by_patient_info(pi)
        # Result count is a property of the underlying data; we just need
        # the call to succeed without raising.
        assert result.count() >= 0


class TestMclQuerysetFilters:
    """Each eligible_for_* method must correctly include trials that don't
    restrict on the attr, exclude trials whose required-list is disjoint
    from the patient value, and include trials whose required-list
    overlaps."""

    @pytest.mark.django_db
    @pytest.mark.parametrize('method_name,trial_field,patient_value', [
        ('eligible_for_disease_behaviors', 'disease_behaviors_required', ['indolent']),
        ('eligible_for_disease_subtypes', 'disease_subtypes_required', ['cmcl']),
        ('eligible_for_extranodal_sites', 'extranodal_sites_required', ['bone_marrow']),
        ('eligible_for_mipi_risks', 'mipi_risks_required', ['low']),
        ('eligible_for_mipi_c_risks', 'mipi_c_risks_required', ['low']),
        ('eligible_for_bulky_disease_criteria', 'bulky_disease_criteria_required', ['bulky_lesion_5cm']),
    ])
    def test_required_list_overlap(self, method_name, trial_field, patient_value):
        t_no_req = TrialFactory(disease='mantle cell lymphoma', **{trial_field: []})
        t_matches = TrialFactory(disease='mantle cell lymphoma', **{trial_field: patient_value})
        t_excludes = TrialFactory(
            disease='mantle cell lymphoma',
            **{trial_field: ['some_other_value']},
        )

        method = getattr(Trial.objects.all(), method_name)
        result = set(method(patient_value).values_list('id', flat=True))

        assert t_no_req.id in result, f'{method_name} must keep trials with empty required list'
        assert t_matches.id in result, f'{method_name} must keep trials whose required overlaps patient'
        assert t_excludes.id not in result, \
            f'{method_name} must drop trials whose required is disjoint from patient'

    @pytest.mark.django_db
    def test_morphologic_variants_required_and_excluded(self):
        # Two-list variant: required AND excluded must both be considered.
        t_no_req = TrialFactory(
            disease='mantle cell lymphoma',
            morphologic_variants_required=[],
            morphologic_variants_excluded=[],
        )
        t_matches = TrialFactory(
            disease='mantle cell lymphoma',
            morphologic_variants_required=['classic'],
            morphologic_variants_excluded=[],
        )
        t_excluded = TrialFactory(
            disease='mantle cell lymphoma',
            morphologic_variants_required=['classic'],
            morphologic_variants_excluded=['classic'],
        )

        result = set(
            Trial.objects.all()
            .eligible_for_morphologic_variants(['classic'])
            .values_list('id', flat=True)
        )
        assert t_no_req.id in result
        assert t_matches.id in result
        assert t_excluded.id not in result

    @pytest.mark.django_db
    @pytest.mark.parametrize('method_name', [
        'eligible_for_disease_behaviors',
        'eligible_for_disease_subtypes',
        'eligible_for_extranodal_sites',
        'eligible_for_mipi_risks',
        'eligible_for_mipi_c_risks',
        'eligible_for_bulky_disease_criteria',
        'eligible_for_morphologic_variants',
    ])
    def test_none_input_is_noop(self, method_name):
        # The dispatch lambdas wrap empty patient values as None so the
        # delegate (`eligible_for_required_lists` / `_and_excluded_lists`)
        # short-circuits to `self`. Verify the wrappers honor that.
        t = TrialFactory(disease='mantle cell lymphoma')
        method = getattr(Trial.objects.all(), method_name)
        result = set(method(None).values_list('id', flat=True))
        assert t.id in result
