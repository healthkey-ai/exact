import math

import pytest

import datetime as dt

from django.contrib.gis.geos import Point
from django.contrib.gis.measure import D
from django.db.models import F

from trials.models import *
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.normalize import normalize_patient_info
from tests.factories import *


def _complement(x: int | None, normalize_factor: int) -> float:
    """
    Returns 1 - x/n
    But if x is None, returns the worst value
    """
    if x is None:
        return 0
    return 1 - x / normalize_factor


class TestTrialQuerySet:
    @pytest.mark.django_db
    def test_eligible_for_plasma_cell_leukemia(self):
        t1 = TrialFactory(no_plasma_cell_leukemia_required=False, plasma_cell_leukemia_required=False)  # always eligible
        t2 = TrialFactory(no_plasma_cell_leukemia_required=True, plasma_cell_leukemia_required=False)
        t3 = TrialFactory(no_plasma_cell_leukemia_required=False, plasma_cell_leukemia_required=True)
        t4 = TrialFactory(no_plasma_cell_leukemia_required=True, plasma_cell_leukemia_required=True)  # wrong setup

        assert Trial.objects.count() == 4

        # plasma_cell_leukemia is blank
        assert Trial.objects.eligible_for_plasma_cell_leukemia(None).count() == 4

        plasma_cell_leukemia = False
        assert list(Trial.objects.eligible_for_plasma_cell_leukemia(plasma_cell_leukemia).order_by('id')) == [t1, t2]

        plasma_cell_leukemia = True
        assert list(Trial.objects.eligible_for_plasma_cell_leukemia(plasma_cell_leukemia).order_by('id')) == [t1, t3]

    @pytest.mark.django_db
    def test_eligible_for_progression(self):
        t1 = TrialFactory(disease_progression_active_required=False, disease_progression_smoldering_required=False)  # always eligible
        t2 = TrialFactory(disease_progression_active_required=True, disease_progression_smoldering_required=False)
        t3 = TrialFactory(disease_progression_active_required=False, disease_progression_smoldering_required=True)
        t4 = TrialFactory(disease_progression_active_required=True, disease_progression_smoldering_required=True)  # wrong setup probably?

        assert Trial.objects.count() == 4

        progression = None
        assert Trial.objects.eligible_for_progression(progression).count() == 4

        progression = 'active'
        assert list(Trial.objects.eligible_for_progression(progression).order_by('id')) == [t1, t2]

        progression = 'smoldering'
        assert list(Trial.objects.eligible_for_progression(progression).order_by('id')) == [t1, t3]

    @pytest.mark.django_db
    def test_eligible_for_treatment_refractory_status(self):
        t1 = TrialFactory(refractory_required=False, not_refractory_required=False)  # always eligible
        t2 = TrialFactory(refractory_required=True, not_refractory_required=False)
        t3 = TrialFactory(refractory_required=False, not_refractory_required=True)
        t4 = TrialFactory(refractory_required=True, not_refractory_required=True)

        assert Trial.objects.count() == 4

        #  "notRefractory": "Not Refractory (progression halted)",
        #  "primaryRefractory": "Primary Refractory",
        #  "secondaryRefractory": "Secondary Refractory",
        #  "multiRefractory": "Multi-Refractory",

        value = None
        assert Trial.objects.eligible_for_treatment_refractory_status(value).count() == 4

        value = ""
        assert Trial.objects.eligible_for_treatment_refractory_status(value).count() == 4

        value = "notRefractory"
        assert list(Trial.objects.eligible_for_treatment_refractory_status(value).order_by('id')) == [t1, t3]
        value = "Not Refractory (progression halted)"
        assert list(Trial.objects.eligible_for_treatment_refractory_status(value).order_by('id')) == [t1, t3]

        value = "primaryRefractory"
        assert list(Trial.objects.eligible_for_treatment_refractory_status(value).order_by('id')) == [t1, t2]
        value = "Primary Refractory"
        assert list(Trial.objects.eligible_for_treatment_refractory_status(value).order_by('id')) == [t1, t2]

    @pytest.mark.django_db
    def test_eligible_for_prior_therapy(self):
        t1 = TrialFactory(prior_therapy_lines=None, therapy_lines_count_min=None, therapy_lines_count_max=None)  # always eligible
        t2 = TrialFactory(prior_therapy_lines=None, therapy_lines_count_max=0)
        t3 = TrialFactory(prior_therapy_lines=None, therapy_lines_count_min=1)
        t4 = TrialFactory(prior_therapy_lines=None, therapy_lines_count_max=1)
        t5 = TrialFactory(prior_therapy_lines=None, therapy_lines_count_min=1, therapy_lines_count_max=1)
        t6 = TrialFactory(prior_therapy_lines=None, therapy_lines_count_min=2)
        t7 = TrialFactory(prior_therapy_lines=None, therapy_lines_count_max=2)
        t8 = TrialFactory(prior_therapy_lines=None, therapy_lines_count_min=2, therapy_lines_count_max=2)
        t9 = TrialFactory(prior_therapy_lines=None, therapy_lines_count_min=3)

        assert Trial.objects.count() == 9

        # prior_therapy is blank
        assert Trial.objects.eligible_for_prior_therapy(None).count() == 9
        assert Trial.objects.eligible_for_prior_therapy('').count() == 9

        prior_therapy = 'None'
        assert list(Trial.objects.eligible_for_prior_therapy(prior_therapy)) == [t1, t2, t4, t7]

        prior_therapy = 'One line'
        assert list(Trial.objects.eligible_for_prior_therapy(prior_therapy)) == [t1, t3, t4, t5, t7]

        prior_therapy = 'Two lines'
        assert list(Trial.objects.eligible_for_prior_therapy(prior_therapy)) == [t1, t3, t6, t7, t8]

        prior_therapy = 'More than two lines of therapy'
        assert list(Trial.objects.eligible_for_prior_therapy(prior_therapy)) == [t1, t3, t6, t9]

    @pytest.mark.django_db
    def test_eligible_for_required_and_excluded_lists(self):
        t1 = TrialFactory(therapies_required=[], therapies_excluded=[])  # always eligible
        t2 = TrialFactory(therapies_required=['val1', 'val2'], therapies_excluded=['val3', 'val4'])
        t3 = TrialFactory(therapies_required=['val1'], therapies_excluded=[])
        t4 = TrialFactory(therapies_required=[], therapies_excluded=['val1'])
        t5 = TrialFactory(therapies_required=['val1', 'val2'], therapies_excluded=['val3'])
        t6 = TrialFactory(therapies_required=['val3'], therapies_excluded=['val1', 'val2'])
        t7 = TrialFactory(therapies_required=['val4'], therapies_excluded=['val2'])

        assert Trial.objects.count() == 7

        # therapy_from_line is blank
        assert Trial.objects.eligible_for_required_and_excluded_lists(
            None, required_attr_name='therapies_required', excluded_attr_name='therapies_excluded').count() == 7
        assert Trial.objects.eligible_for_required_and_excluded_lists(
            [], required_attr_name='therapies_required', excluded_attr_name='therapies_excluded').count() == 7

        therapy_from_line = ['foo']
        assert list(Trial.objects.eligible_for_required_and_excluded_lists(
            therapy_from_line, required_attr_name='therapies_required', excluded_attr_name='therapies_excluded'
        )) == [t1, t4]

        therapy_from_line = ['val1']
        assert list(Trial.objects.eligible_for_required_and_excluded_lists(
            therapy_from_line, required_attr_name='therapies_required', excluded_attr_name='therapies_excluded'
        )) == [t1, t2, t3, t5]

        therapy_from_line = ['val2']
        assert list(Trial.objects.eligible_for_required_and_excluded_lists(
            therapy_from_line, required_attr_name='therapies_required', excluded_attr_name='therapies_excluded'
        )) == [t1, t2, t4, t5]

        therapy_from_line = ['val3']
        assert list(Trial.objects.eligible_for_required_and_excluded_lists(
            therapy_from_line, required_attr_name='therapies_required', excluded_attr_name='therapies_excluded'
        )) == [t1, t4, t6]

        therapy_from_line = ['val4']
        assert list(Trial.objects.eligible_for_required_and_excluded_lists(
            therapy_from_line, required_attr_name='therapies_required', excluded_attr_name='therapies_excluded'
        )) == [t1, t4, t7]

        therapy_from_line = ['val1', 'val4']
        assert list(Trial.objects.eligible_for_required_and_excluded_lists(
            therapy_from_line, required_attr_name='therapies_required', excluded_attr_name='therapies_excluded'
        )) == [t1, t3, t5, t7]

        therapy_from_line = ['val1', 'val4', 'foo']
        assert list(Trial.objects.eligible_for_required_and_excluded_lists(
            therapy_from_line, required_attr_name='therapies_required', excluded_attr_name='therapies_excluded'
        )) == [t1, t3, t5, t7]

        therapy_from_line = ['val2', 'val4']
        assert list(Trial.objects.eligible_for_required_and_excluded_lists(
            therapy_from_line, required_attr_name='therapies_required', excluded_attr_name='therapies_excluded'
        )) == [t1, t4, t5]

    @pytest.mark.django_db
    def test_eligible_for_therapy_related_things_from_lines(self):
        from trials.services.loaders.load_therapies import LoadTherapies
        LoadTherapies().load_all()

        t1 = TrialFactory(therapies_required=[], therapies_excluded=[])  # always eligible
        t2 = TrialFactory(therapies_required=['vrd', 'val2'], therapies_excluded=['val3', 'val4'])
        t3 = TrialFactory(therapies_required=['vrd'], therapies_excluded=[])
        t4 = TrialFactory(therapies_required=[], therapies_excluded=['vrd'])
        t5 = TrialFactory(therapies_required=['vrd', 'val2'], therapies_excluded=['val3'])
        t6 = TrialFactory(therapies_required=['val3'], therapies_excluded=['vrd', 'val2'])
        t7 = TrialFactory(therapies_required=['val4'], therapies_excluded=['val2'])

        assert Trial.objects.count() == 7

        # therapy_from_line is blank
        assert Trial.objects.eligible_for_therapy_related_things_from_lines(None).count() == 7
        assert Trial.objects.eligible_for_therapy_related_things_from_lines([]).count() == 7

        therapy_from_line = ['foo']
        assert list(Trial.objects.eligible_for_therapy_related_things_from_lines(therapy_from_line)) == [t1, t4]

        therapy_from_line = ['vrd']
        assert list(Trial.objects.eligible_for_therapy_related_things_from_lines(therapy_from_line)) == [t1, t2, t3, t5]

        therapy_from_line = ['val2']
        assert list(Trial.objects.eligible_for_therapy_related_things_from_lines(therapy_from_line)) == [t1, t2, t4, t5]

        therapy_from_line = ['val3']
        assert list(Trial.objects.eligible_for_therapy_related_things_from_lines(therapy_from_line)) == [t1, t4, t6]

        therapy_from_line = ['val4']
        assert list(Trial.objects.eligible_for_therapy_related_things_from_lines(therapy_from_line)) == [t1, t4, t7]

        therapy_from_line = ['vrd', 'val4']
        assert list(Trial.objects.eligible_for_therapy_related_things_from_lines(therapy_from_line)) == [t1, t3, t5, t7]

        therapy_from_line = ['vrd', 'val4', 'foo']
        assert list(Trial.objects.eligible_for_therapy_related_things_from_lines(therapy_from_line)) == [t1, t3, t5, t7]

        therapy_from_line = ['val2', 'val4']
        assert list(Trial.objects.eligible_for_therapy_related_things_from_lines(therapy_from_line)) == [t1, t4, t5]

        # with components
        t8 = TrialFactory(therapy_components_required=['bortezomib'], therapy_components_excluded=[])
        t9 = TrialFactory(therapy_components_required=[], therapy_components_excluded=['cyclophosphamide'])

        therapy_from_line = ['vrd']
        assert list(Trial.objects.eligible_for_therapy_related_things_from_lines(therapy_from_line)) == [t1, t2, t3, t5, t8, t9]

        therapy_from_line = ['dara_vrd']
        assert list(Trial.objects.eligible_for_therapy_related_things_from_lines(therapy_from_line)) == [t1, t4, t8, t9]

        therapy_from_line = ['cy_bor_d']
        assert list(Trial.objects.eligible_for_therapy_related_things_from_lines(therapy_from_line)) == [t1, t4, t8]

        # with types
        t10 = TrialFactory(therapy_types_required=['proteasome_inhibitor'], therapy_types_excluded=[])
        t11 = TrialFactory(therapy_types_required=[], therapy_types_excluded=['monoclonal_antibody_(anti_cd38)'])

        therapy_from_line = ['vrd']
        assert list(Trial.objects.eligible_for_therapy_related_things_from_lines(therapy_from_line)) == [t1, t2, t3, t5, t8, t9, t10, t11]

        therapy_from_line = ['dara_vrd']
        assert list(Trial.objects.eligible_for_therapy_related_things_from_lines(therapy_from_line)) == [t1, t4, t8, t9, t10]

    @pytest.mark.django_db
    def test_eligible_for_therapy_related_things_from_lines_treatment_naive(self):
        """
        has_no_prior_therapy=True restricts to trials with all three *_required
        fields empty (treatment-naive patients can only enroll in trials that
        don't require prior therapy, components, or types).

        The therapy_codes argument is irrelevant when has_no_prior_therapy=True —
        only the empty-list check matters.
        """
        from trials.services.loaders.load_therapies import LoadTherapies
        LoadTherapies().load_all()

        # t1: all required fields empty → always eligible for treatment-naive
        t1 = TrialFactory(
            therapies_required=[], therapy_components_required=[], therapy_types_required=[],
        )
        # t2: requires a specific therapy → ineligible for treatment-naive
        t2 = TrialFactory(
            therapies_required=['vrd'], therapy_components_required=[], therapy_types_required=[],
        )
        # t3: requires a specific component → ineligible for treatment-naive
        t3 = TrialFactory(
            therapies_required=[], therapy_components_required=['bortezomib'], therapy_types_required=[],
        )
        # t4: requires a specific type → ineligible for treatment-naive
        t4 = TrialFactory(
            therapies_required=[], therapy_components_required=[], therapy_types_required=['proteasome_inhibitor'],
        )
        # t5: all three non-empty → ineligible for treatment-naive
        t5 = TrialFactory(
            therapies_required=['vrd'], therapy_components_required=['bortezomib'],
            therapy_types_required=['proteasome_inhibitor'],
        )

        # With has_no_prior_therapy=True: only t1 qualifies regardless of codes passed
        assert list(Trial.objects.eligible_for_therapy_related_things_from_lines(
            [], has_no_prior_therapy=True
        )) == [t1]
        assert list(Trial.objects.eligible_for_therapy_related_things_from_lines(
            ['vrd'], has_no_prior_therapy=True
        )) == [t1]
        assert list(Trial.objects.eligible_for_therapy_related_things_from_lines(
            None, has_no_prior_therapy=True
        )) == [t1]

        # Sanity check: without the flag the normal path returns all 5 (codes=None → no filter)
        assert Trial.objects.eligible_for_therapy_related_things_from_lines(None).count() == 5

    @pytest.mark.django_db
    def test_eligible_for_min_max_value(self):
        t1 = TrialFactory(age_low_limit=18, age_high_limit=60)
        t2 = TrialFactory(age_low_limit=18, age_high_limit=None)
        t3 = TrialFactory(age_low_limit=None, age_high_limit=60)
        t4 = TrialFactory(age_low_limit=None, age_high_limit=None)

        attr_min_name = "age_low_limit"
        attr_max_name = "age_high_limit"

        assert Trial.objects.count() == 4

        assert Trial.objects.eligible_for_min_max_value(attr_min_name, attr_max_name, None).count() == 4
        assert Trial.objects.eligible_for_min_max_value(attr_min_name, attr_max_name, 0).count() == 4

        assert list(Trial.objects.eligible_for_min_max_value(attr_min_name, attr_max_name, 16)) == [t3, t4]
        assert list(Trial.objects.eligible_for_min_max_value(attr_min_name, attr_max_name, 18)) == [t1, t2, t3, t4]
        assert list(Trial.objects.eligible_for_min_max_value(attr_min_name, attr_max_name, 45)) == [t1, t2, t3, t4]
        assert list(Trial.objects.eligible_for_min_max_value(attr_min_name, attr_max_name, 60)) == [t1, t2, t3, t4]
        assert list(Trial.objects.eligible_for_min_max_value(attr_min_name, attr_max_name, 75)) == [t2, t4]

    @pytest.mark.django_db
    def test_eligible_for_bool_value(self):
        t1 = TrialFactory(contraceptive_use_requirement=None)
        t2 = TrialFactory(contraceptive_use_requirement=False)
        t3 = TrialFactory(contraceptive_use_requirement=True)

        attr_name = "contraceptive_use_requirement"

        assert Trial.objects.count() == 3

        assert Trial.objects.eligible_for_bool_value(attr_name, None).count() == 3

        assert list(Trial.objects.eligible_for_bool_value(attr_name, False)) == [t1, t2]
        assert list(Trial.objects.eligible_for_bool_value(attr_name, True)) == [t1, t3]

    @pytest.mark.django_db
    def test_eligible_for_str_value(self):
        t1 = TrialFactory(disease=None)
        t2 = TrialFactory(disease="")
        t3 = TrialFactory(disease="Multiple Myeloma")
        t4 = TrialFactory(disease="Follicular Lymphoma")

        attr_name = "disease"
        allow_blank = True

        assert Trial.objects.count() == 4

        assert Trial.objects.eligible_for_str_value(attr_name, None, allow_blank).count() == 4
        assert Trial.objects.eligible_for_str_value(attr_name, '', allow_blank).count() == 4

        assert list(Trial.objects.eligible_for_str_value(attr_name, "multiple myeloma", allow_blank)) == [t1, t2, t3]
        assert list(Trial.objects.eligible_for_str_value(attr_name, "follicular lymphoma", allow_blank)) == [t1, t2, t4]

        allow_blank = False

        assert Trial.objects.eligible_for_str_value(attr_name, None, allow_blank).count() == 4
        assert Trial.objects.eligible_for_str_value(attr_name, '', allow_blank).count() == 4

        assert list(Trial.objects.eligible_for_str_value(attr_name, "multiple myeloma", allow_blank)) == [t3]
        assert list(Trial.objects.eligible_for_str_value(attr_name, "follicular lymphoma", allow_blank)) == [t4]

    @pytest.mark.django_db
    def test_eligible_for_relation(self):
        t1 = TrialFactory()
        t2 = TrialFactory()
        t3 = TrialFactory(trial_type=None)

        attr_name = "trial_type__code"
        value = t1.trial_type.code

        assert Trial.objects.count() == 3

        assert Trial.objects.eligible_for_str_value(attr_name, None).count() == 3
        assert Trial.objects.eligible_for_str_value(attr_name, '').count() == 3

        assert list(Trial.objects.eligible_for_str_value(attr_name, value)) == [t1, t3]
        assert list(Trial.objects.eligible_for_str_value(attr_name, "foo")) == [t3]

    @pytest.mark.django_db
    def test_eligible_for_inversed_bool_restriction_value(self):
        t1 = TrialFactory(no_tobacco_use_required=True)
        t2 = TrialFactory(no_tobacco_use_required=False)
        t3 = TrialFactory(no_tobacco_use_required=None)

        assert Trial.objects.count() == 3

        assert list(Trial.objects.eligible_for_inversed_bool_restriction_value(
            'no_tobacco_use_required', True)) == [t2, t3]

        assert list(Trial.objects.eligible_for_inversed_bool_restriction_value(
            'no_tobacco_use_required', False)) == [t1, t2, t3]

        assert list(Trial.objects.eligible_for_inversed_bool_restriction_value(
            'no_tobacco_use_required', None)) == [t1, t2, t3]

    @pytest.mark.django_db
    def test_eligible_for_bool_requirement_value(self):
        t1 = TrialFactory(consent_capability_required=True)
        t2 = TrialFactory(consent_capability_required=False)
        t3 = TrialFactory(consent_capability_required=None)

        assert Trial.objects.count() == 3

        is_under_user_control = False
        assert list(Trial.objects.eligible_for_bool_requirement_value(
            'consent_capability_required', True, is_under_user_control)) == [t1, t2, t3]

        assert list(Trial.objects.eligible_for_bool_requirement_value(
            'consent_capability_required', False, is_under_user_control)) == [t2, t3]

        assert list(Trial.objects.eligible_for_bool_requirement_value(
            'consent_capability_required', None, is_under_user_control)) == [t2, t3]

        is_under_user_control = True
        assert list(Trial.objects.eligible_for_bool_requirement_value(
            'consent_capability_required', True, is_under_user_control)) == [t1, t2, t3]

        assert list(Trial.objects.eligible_for_bool_requirement_value(
            'consent_capability_required', False, is_under_user_control)) == [t1, t2, t3]

        assert list(Trial.objects.eligible_for_bool_requirement_value(
            'consent_capability_required', None, is_under_user_control)) == [t1, t2, t3]

    @pytest.mark.django_db
    def test_eligible_for_stage(self):
        t1 = TrialFactory(stages=[])
        t2 = TrialFactory(stages=["I", "II"])
        t3 = TrialFactory(stages=["II", "III"])
        t4 = TrialFactory(stages=["II"])
        t5 = TrialFactory(stages=["III"])

        assert Trial.objects.count() == 5

        assert list(Trial.objects.eligible_for_stage(None)) == [t1, t2, t3, t4, t5]
        assert list(Trial.objects.eligible_for_stage('')) == [t1, t2, t3, t4, t5]

        assert list(Trial.objects.eligible_for_stage('I')) == [t1, t2]

        assert list(Trial.objects.eligible_for_stage('II')) == [t1, t2, t3, t4]

        assert list(Trial.objects.eligible_for_stage('III')) == [t1, t3, t5]

        assert list(Trial.objects.eligible_for_stage('IV')) == [t1]

    @pytest.mark.django_db
    def test_eligible_for_pre_existing_condition(self) -> None:
        t1 = TrialFactory(pre_existing_conditions_excluded=[])
        t2 = TrialFactory(pre_existing_conditions_excluded=["Diabetes"])
        t3 = TrialFactory(pre_existing_conditions_excluded=["Diabetes", "Hypertension"])
        t4 = TrialFactory(pre_existing_conditions_excluded=["Hypertension"])

        assert Trial.objects.count() == 4
        assert list(Trial.objects.eligible_for_pre_existing_condition(None)) == [t1, t2, t3, t4]
        assert list(Trial.objects.eligible_for_pre_existing_condition('')) == [t1, t2, t3, t4]
        assert list(Trial.objects.eligible_for_pre_existing_condition([])) == [t1, t2, t3, t4]
        assert list(Trial.objects.eligible_for_pre_existing_condition(['Diabetes'])) == [t1, t4]
        assert list(Trial.objects.eligible_for_pre_existing_condition(['Hypertension'])) == [t1, t2]
        assert list(Trial.objects.eligible_for_pre_existing_condition(['Diabetes', 'Hypertension'])) == [t1]
        assert list(Trial.objects.eligible_for_pre_existing_condition(['Cancer'])) == [t1, t2, t3, t4]

    @pytest.mark.django_db
    def test_eligible_for_stem_cell_transplant_history(self) -> None:
        t1 = TrialFactory(
            stem_cell_transplant_history_required=False,
            stem_cell_transplant_history_excluded=[],
        )
        t2 = TrialFactory(
            stem_cell_transplant_history_required=True,
            stem_cell_transplant_history_excluded=[],
        )
        t3 = TrialFactory(
            stem_cell_transplant_history_required=False,
            stem_cell_transplant_history_excluded=["Autologous"],
        )
        t4 = TrialFactory(
            stem_cell_transplant_history_required=False,
            stem_cell_transplant_history_excluded=["Autologous", "Allogeneic", "priorAutologousSCT"],
        )
        t5 = TrialFactory(
            stem_cell_transplant_history_required=True,
            stem_cell_transplant_history_excluded=["Autologous", "Allogeneic"],
        )

        assert Trial.objects.count() == 5
        assert list(Trial.objects.eligible_for_stem_cell_transplant_history(None)) == [t1, t2, t3, t4, t5]
        assert list(Trial.objects.eligible_for_stem_cell_transplant_history('')) == [t1, t2, t3, t4, t5]
        assert list(Trial.objects.eligible_for_stem_cell_transplant_history([])) == [t1, t2, t3, t4, t5]
        assert list(Trial.objects.eligible_for_stem_cell_transplant_history('none')) == [t1, t3, t4]
        assert list(Trial.objects.eligible_for_stem_cell_transplant_history(['Autologous'])) == [t1, t2]
        assert list(Trial.objects.eligible_for_stem_cell_transplant_history(['Allogeneic'])) == [t1, t2, t3]
        assert list(Trial.objects.eligible_for_stem_cell_transplant_history(['Autologous', 'Allogeneic'])) == [t1, t2]
        assert list(Trial.objects.eligible_for_stem_cell_transplant_history(['AnythingElse'])) == [t1, t2, t3, t4, t5]
        assert list(Trial.objects.eligible_for_stem_cell_transplant_history('AnythingElse')) == [t1, t2, t3, t4, t5]
        assert list(Trial.objects.eligible_for_stem_cell_transplant_history(['completedASCT'])) == [t1, t2, t3, t5]
        assert list(Trial.objects.eligible_for_stem_cell_transplant_history('completedASCT')) == [t1, t2, t3, t5]

    @pytest.mark.django_db
    def test_eligible_for_concomitant_medications_and_washout_period(self) -> None:
        t1 = TrialFactory(
            concomitant_medications_excluded=[],
            concomitant_medications_washout_period_duration=None,
        )
        t2 = TrialFactory(
            concomitant_medications_excluded=['anticoagulants'],
            concomitant_medications_washout_period_duration=None,
        )
        t3 = TrialFactory(
            concomitant_medications_excluded=['anticoagulants', 'corticosteroids'],
            concomitant_medications_washout_period_duration=None,
        )
        t4 = TrialFactory(
            concomitant_medications_excluded=['corticosteroids'],
            concomitant_medications_washout_period_duration=50,
        )
        t5 = TrialFactory(
            concomitant_medications_excluded=['corticosteroids'],
            concomitant_medications_washout_period_duration=100,
        )

        assert Trial.objects.count() == 5
        assert list(Trial.objects.eligible_for_concomitant_medications_and_washout_period(concomitant_medications=None, concomitant_medication_date=None)) == [t1, t2, t3, t4, t5]
        assert list(Trial.objects.eligible_for_concomitant_medications_and_washout_period(concomitant_medications='', concomitant_medication_date=None)) == [t1, t2, t3, t4, t5]
        assert list(Trial.objects.eligible_for_concomitant_medications_and_washout_period(concomitant_medications=[], concomitant_medication_date=None)) == [t1, t2, t3, t4, t5]
        assert list(Trial.objects.eligible_for_concomitant_medications_and_washout_period(concomitant_medications='none', concomitant_medication_date=None)) == [t1, t2, t3, t4, t5]
        assert list(Trial.objects.eligible_for_concomitant_medications_and_washout_period(concomitant_medications='anticoagulants', concomitant_medication_date=None)) == [t1, t4, t5]
        assert list(Trial.objects.eligible_for_concomitant_medications_and_washout_period(concomitant_medications='anticoagulants,foo', concomitant_medication_date=None)) == [t1, t4, t5]
        assert list(Trial.objects.eligible_for_concomitant_medications_and_washout_period(concomitant_medications='corticosteroids, anticoagulants', concomitant_medication_date=None)) == [t1]
        concomitant_medication_date = None
        assert list(Trial.objects.eligible_for_concomitant_medications_and_washout_period(concomitant_medications='corticosteroids', concomitant_medication_date=concomitant_medication_date)) == [t1, t2]
        concomitant_medication_date = dt.date.today() - dt.timedelta(days=25)
        assert list(Trial.objects.eligible_for_concomitant_medications_and_washout_period(concomitant_medications='corticosteroids', concomitant_medication_date=concomitant_medication_date)) == [t1, t2]
        concomitant_medication_date = dt.date.today() - dt.timedelta(days=75)
        assert list(Trial.objects.eligible_for_concomitant_medications_and_washout_period(concomitant_medications='corticosteroids', concomitant_medication_date=concomitant_medication_date)) == [t1, t2, t4]
        concomitant_medication_date = dt.date.today() - dt.timedelta(days=110)
        assert list(Trial.objects.eligible_for_concomitant_medications_and_washout_period(concomitant_medications='corticosteroids', concomitant_medication_date=concomitant_medication_date)) == [t1, t2, t4, t5]

    @pytest.mark.django_db
    def test_eligible_for_washout_period_duration(self) -> None:
        t1 = TrialFactory(washout_period_duration=None)
        t2 = TrialFactory(washout_period_duration=50)
        t3 = TrialFactory(washout_period_duration=100)

        assert Trial.objects.count() == 3

        last_treatment = None
        assert list(Trial.objects.eligible_for_washout_period_duration(last_treatment)) == [t1, t2, t3]

        last_treatment = dt.date.today() - dt.timedelta(days=25)
        assert list(Trial.objects.eligible_for_washout_period_duration(last_treatment)) == [t1]

        last_treatment = dt.date.today() - dt.timedelta(days=75)
        assert list(Trial.objects.eligible_for_washout_period_duration(last_treatment)) == [t1, t2]

        last_treatment = dt.date.today() - dt.timedelta(days=110)
        assert list(Trial.objects.eligible_for_washout_period_duration(last_treatment)) == [t1, t2, t3]

    @pytest.mark.django_db
    def test_eligible_for_molecular_marker(self) -> None:
        t1 = TrialFactory(
            molecular_markers_required=[],
            molecular_markers_excluded=[],
        )
        t2 = TrialFactory(
            molecular_markers_required=["CD138"],
            molecular_markers_excluded=[],
        )
        t3 = TrialFactory(
            molecular_markers_required=["CD138", "CD19"],
            molecular_markers_excluded=[],
        )
        t4 = TrialFactory(
            molecular_markers_required=[],
            molecular_markers_excluded=["CD138"],
        )
        t5 = TrialFactory(
            molecular_markers_required=[],
            molecular_markers_excluded=["CD138", "CD19"],
        )
        t6 = TrialFactory(
            molecular_markers_required=["CD138"],
            molecular_markers_excluded=["CD19"],
        )

        assert Trial.objects.count() == 6
        assert list(Trial.objects.eligible_for_molecular_marker(None).order_by('id')) == [t1, t2, t3, t4, t5, t6]
        assert list(Trial.objects.eligible_for_molecular_marker([]).order_by('id')) == [t1, t2, t3, t4, t5, t6]
        assert list(Trial.objects.eligible_for_molecular_marker(['CD138']).order_by('id')) == [t1, t2, t3, t6]
        assert list(Trial.objects.eligible_for_molecular_marker(['CD19']).order_by('id')) == [t1, t3, t4]
        assert list(Trial.objects.eligible_for_molecular_marker(['CD138', 'CD19']).order_by('id')) == [t1, t2, t3]
        assert list(Trial.objects.eligible_for_molecular_marker(['CD138', 'foo']).order_by('id')) == [t1, t2, t3, t6]

    @pytest.mark.django_db
    def test_by_posted_date(self):
        t1 = TrialFactory(last_update_date=dt.datetime.now())
        t2 = TrialFactory(last_update_date=dt.datetime.now() - dt.timedelta(days=800))
        t3 = TrialFactory(last_update_date=None)

        assert Trial.objects.count() == 3

        patient_last_update = None
        assert Trial.objects.by_last_update(patient_last_update).count() == 3

        patient_last_update = 1
        assert Trial.objects.by_last_update(patient_last_update).count() == 2
        assert list(Trial.objects.by_last_update(patient_last_update)) == [t1, t3]

        patient_last_update = "1"
        assert Trial.objects.by_last_update(patient_last_update).count() == 2
        assert list(Trial.objects.by_last_update(patient_last_update)) == [t1, t3]

        patient_last_update = 3
        assert Trial.objects.by_last_update(patient_last_update).count() == 3

        patient_last_update = "not a number"
        assert Trial.objects.by_last_update(patient_last_update).count() == 3

    @pytest.mark.django_db
    def test_by_titles(self):
        t1 = TrialFactory(brief_title='THE TiTle', official_title='PHASE 1 tomato red')
        t2 = TrialFactory(brief_title='red tomato', official_title='PHASE 2-3 The Title')
        t3 = TrialFactory(brief_title='EARLY PHASE 1', official_title='tomato red tomato')
        t4 = TrialFactory(brief_title='', official_title='')

        assert Trial.objects.count() == 4
        assert list(Trial.objects.by_titles("").order_by('id')) == [t1, t2, t3, t4]
        assert list(Trial.objects.by_titles("red tomato").order_by('id')) == [t1, t2, t3]
        assert list(Trial.objects.by_titles("tomato red").order_by('id')) == [t1, t2, t3]
        assert list(Trial.objects.by_titles("PHASE 1").order_by('id')) == [t1, t3]
        assert list(Trial.objects.by_titles("PHASE 2").order_by('id')) == [t2]
        assert list(Trial.objects.by_titles("PHASE 3").order_by('id')) == []
        assert list(Trial.objects.by_titles("Early Phase 1").order_by('id')) == [t3]

    @pytest.mark.django_db
    def test_by_intervention_treatment(self):
        t1 = TrialFactory(intervention_treatments_text='Velcade, Prednisone, Melphalan, Rituximab')
        t2 = TrialFactory(intervention_treatments_text='Loncastuximab Tesirine, Obinutuzumab, Polatuzumab Vedotin, Glofitamab, Mosunetuzumab')
        t3 = TrialFactory(intervention_treatments_text='Bortezomib + Rituximab, Rituximab')
        t4 = TrialFactory(intervention_treatments_text='Dexamethasone, Cyclophosphamide, Ixazomib')
        t5 = TrialFactory(intervention_treatments_text='')

        assert Trial.objects.count() == 5
        assert list(Trial.objects.by_intervention_treatment("").order_by('id')) == [t1, t2, t3, t4, t5]
        assert list(Trial.objects.by_intervention_treatment("Velcade").order_by('id')) == [t1]
        assert list(Trial.objects.by_intervention_treatment("Velc").order_by('id')) == []
        assert list(Trial.objects.by_intervention_treatment("Velcade and Mosunetuzumab").order_by('id')) == []
        assert list(Trial.objects.by_intervention_treatment("Velcade or Mosunetuzumab").order_by('id')) == [t1, t2]
        assert list(Trial.objects.by_intervention_treatment("not Melphalan").order_by('id')) == [t2, t3, t4, t5]
        assert list(Trial.objects.by_intervention_treatment("Rituximab not Melphalan").order_by('id')) == [t3]
        assert list(Trial.objects.by_intervention_treatment("Rituximab and not Melphalan").order_by('id')) == [t3]
        assert list(Trial.objects.by_intervention_treatment("Rituximab or Obinutuzumab or Dexamethasone").order_by('id')) == [t1, t2, t3, t4]

    @pytest.mark.django_db
    def test_by_register(self):
        t1 = TrialFactory(register='REGister2')
        t2 = TrialFactory(register='regISter1')
        t3 = TrialFactory(register='')

        assert Trial.objects.count() == 3

        assert list(Trial.objects.by_register("")) == [t1, t2, t3]

        assert list(Trial.objects.by_register("THE")) == []

        assert list(Trial.objects.by_register("register2")) == [t1]

        assert list(Trial.objects.by_register("register1")) == [t2]

    @pytest.mark.django_db
    def test_by_validated_only(self):
        t1 = TrialFactory(is_validated=False)
        t2 = TrialFactory(is_validated=True)

        assert Trial.objects.count() == 2

        assert list(Trial.objects.by_validated_only(None)) == [t1, t2]

        assert list(Trial.objects.by_validated_only(False)) == [t1, t2]

        assert list(Trial.objects.by_validated_only(True)) == [t2]

    @pytest.mark.django_db
    def test_by_recruitment_status(self):
        t1 = TrialFactory(recruitment_status='RECRUITING')
        t2 = TrialFactory(recruitment_status='NOT_YET_RECRUITING')
        t3 = TrialFactory(recruitment_status='COMPLETED')
        t4 = TrialFactory(recruitment_status='TERMINATED')
        t5 = TrialFactory(recruitment_status='')

        assert Trial.objects.count() == 5

        # None or empty returns all
        assert list(Trial.objects.by_recruitment_status(None).order_by('id')) == [t1, t2, t3, t4, t5]
        assert list(Trial.objects.by_recruitment_status('').order_by('id')) == [t1, t2, t3, t4, t5]

        # ALL returns all
        assert list(Trial.objects.by_recruitment_status('ALL').order_by('id')) == [t1, t2, t3, t4, t5]
        assert list(Trial.objects.by_recruitment_status('all').order_by('id')) == [t1, t2, t3, t4, t5]

        # RECRUITING returns only recruiting trials
        assert list(Trial.objects.by_recruitment_status('RECRUITING').order_by('id')) == [t1]
        assert list(Trial.objects.by_recruitment_status('recruiting').order_by('id')) == [t1]
        assert list(Trial.objects.by_recruitment_status('Recruiting').order_by('id')) == [t1]

        # RECRUITING_AND_NOT_YET_RECRUITING returns both recruiting and not yet recruiting
        assert list(Trial.objects.by_recruitment_status('RECRUITING_AND_NOT_YET_RECRUITING').order_by('id')) == [t1, t2]
        assert list(Trial.objects.by_recruitment_status('recruiting_and_not_yet_recruiting').order_by('id')) == [t1, t2]
        assert list(Trial.objects.by_recruitment_status('Recruiting_And_Not_Yet_Recruiting').order_by('id')) == [t1, t2]

        # Other specific statuses — blank `recruitment_status` rows are NOT
        # matched by COMPLETED / TERMINATED filters. Trial metadata is a
        # data-quality signal, not an eligibility-unknown one — the
        # opposite of how patient-attribute filters treat blanks. (Pre-fix,
        # `eligible_for_str_value`'s `allow_blank=True` default widened
        # these filters to also include incompletely-ingested trials.)
        assert list(Trial.objects.by_recruitment_status('COMPLETED').order_by('id')) == [t3]
        assert list(Trial.objects.by_recruitment_status('TERMINATED').order_by('id')) == [t4]

    @pytest.mark.django_db
    def test_by_trial_type(self):
        trial_type1 = TrialTypeFactory(code='interventional', title='Interventional')
        trial_type2 = TrialTypeFactory(code='observational', title='Observational')

        t1 = TrialFactory(trial_type=trial_type1)
        t2 = TrialFactory(trial_type=trial_type1)
        t3 = TrialFactory(trial_type=trial_type2)
        t4 = TrialFactory(trial_type=None)

        assert Trial.objects.count() == 4

        # None or empty returns all
        assert list(Trial.objects.by_trial_type(None).order_by('id')) == [t1, t2, t3, t4]
        assert list(Trial.objects.by_trial_type('').order_by('id')) == [t1, t2, t3, t4]

        # Filter by trial type code (case-insensitive)
        assert list(Trial.objects.by_trial_type('interventional').order_by('id')) == [t1, t2]
        assert list(Trial.objects.by_trial_type('INTERVENTIONAL').order_by('id')) == [t1, t2]
        assert list(Trial.objects.by_trial_type('Interventional').order_by('id')) == [t1, t2]

        assert list(Trial.objects.by_trial_type('observational').order_by('id')) == [t3]
        assert list(Trial.objects.by_trial_type('OBSERVATIONAL').order_by('id')) == [t3]

        # Non-existent type returns empty
        assert list(Trial.objects.by_trial_type('nonexistent').order_by('id')) == []

    @pytest.mark.django_db
    def test_by_trial_purpose(self):
        treatment = TrialPurposeFactory(code='treatment', title='Treatment')
        prevention = TrialPurposeFactory(code='prevention', title='Prevention')

        t1 = TrialFactory(purpose=treatment)
        t2 = TrialFactory(purpose=treatment)
        t3 = TrialFactory(purpose=prevention)
        t4 = TrialFactory(purpose=None)

        # None / empty is a no-op (returns full set)
        assert list(Trial.objects.by_trial_purpose(None).order_by('id')) == [t1, t2, t3, t4]
        assert list(Trial.objects.by_trial_purpose('').order_by('id')) == [t1, t2, t3, t4]

        # Code-string accepted, case-insensitive (mirrors by_trial_type via eligible_for_relation)
        assert list(Trial.objects.by_trial_purpose('treatment').order_by('id')) == [t1, t2]
        assert list(Trial.objects.by_trial_purpose('TREATMENT').order_by('id')) == [t1, t2]

        assert list(Trial.objects.by_trial_purpose('prevention').order_by('id')) == [t3]

        # TrialPurpose instance is also accepted (uses .code)
        assert list(Trial.objects.by_trial_purpose(treatment).order_by('id')) == [t1, t2]
        assert list(Trial.objects.by_trial_purpose(prevention).order_by('id')) == [t3]

        # Unknown code → empty queryset
        assert list(Trial.objects.by_trial_purpose('nonexistent').order_by('id')) == []

    @pytest.mark.django_db
    def test_by_study_type(self):
        t1 = TrialFactory(study_type='INTERVENTIONAL')
        t2 = TrialFactory(study_type='INTERVENTIONAL')
        t3 = TrialFactory(study_type='OBSERVATIONAL')
        t4 = TrialFactory(study_type='EXPANDED_ACCESS')
        t5 = TrialFactory(study_type='')

        assert Trial.objects.count() == 5

        # None or empty returns all
        assert list(Trial.objects.by_study_type(None).order_by('id')) == [t1, t2, t3, t4, t5]
        assert list(Trial.objects.by_study_type('').order_by('id')) == [t1, t2, t3, t4, t5]
        assert list(Trial.objects.by_study_type('ALL').order_by('id')) == [t1, t2, t3, t4, t5]

        # INTERVENTIONAL only (case-insensitive)
        assert list(Trial.objects.by_study_type('INTERVENTIONAL').order_by('id')) == [t1, t2]
        assert list(Trial.objects.by_study_type('interventional').order_by('id')) == [t1, t2]

        # OBSERVATIONAL only (case-insensitive)
        assert list(Trial.objects.by_study_type('OBSERVATIONAL').order_by('id')) == [t3]
        assert list(Trial.objects.by_study_type('observational').order_by('id')) == [t3]

        # INTERVENTIONAL_AND_OBSERVATIONAL: both, excluding EXPANDED_ACCESS
        assert list(Trial.objects.by_study_type('INTERVENTIONAL_AND_OBSERVATIONAL').order_by('id')) == [t1, t2, t3]
        assert list(Trial.objects.by_study_type('interventional_and_observational').order_by('id')) == [t1, t2, t3]

        # Non-existent type returns empty
        assert list(Trial.objects.by_study_type('nonexistent').order_by('id')) == []

    @pytest.mark.django_db
    def test_by_phase(self):
        t1 = TrialFactory(phase_code_min=None)
        t2 = TrialFactory(phase_code_min=0)
        t3 = TrialFactory(phase_code_min=1)
        t4 = TrialFactory(phase_code_min=2)
        t5 = TrialFactory(phase_code_min=3)
        t6 = TrialFactory(phase_code_min=4)

        assert Trial.objects.count() == 6

        assert list(Trial.objects.by_phase(None).order_by('id')) == [t1, t2, t3, t4, t5, t6]
        assert list(Trial.objects.by_phase('').order_by('id')) == [t1, t2, t3, t4, t5, t6]
        assert list(Trial.objects.by_phase('EARLY_PHASE1').order_by('id')) == [t2, t3, t4, t5, t6]
        assert list(Trial.objects.by_phase('PHASE1').order_by('id')) == [t3, t4, t5, t6]
        assert list(Trial.objects.by_phase('PHASE2').order_by('id')) == [t4, t5, t6]
        assert list(Trial.objects.by_phase('PHASE3').order_by('id')) == [t5, t6]
        assert list(Trial.objects.by_phase('PHASE4').order_by('id')) == [t6]

    @pytest.mark.django_db
    def test_by_sponsor(self):
        t1 = TrialFactory(sponsor_name="MD Anderson")
        t2 = TrialFactory(sponsor_name="")
        t3 = TrialFactory(sponsor_name="other")

        assert Trial.objects.count() == 3

        assert list(Trial.objects.by_sponsor("")) == [t1, t2, t3]

        assert list(Trial.objects.by_sponsor("THE")) == [t3]

        assert list(Trial.objects.by_sponsor("anderson")) == [t1]

        assert list(Trial.objects.by_sponsor("Md")) == [t1]

        assert list(Trial.objects.by_sponsor("MD Anderson")) == [t1]

    @pytest.mark.django_db
    def test_by_first_enrolment_date(self):
        t1 = TrialFactory(first_enrolment_date=dt.datetime.now())
        t2 = TrialFactory(first_enrolment_date=dt.datetime.now() - dt.timedelta(days=800))
        t3 = TrialFactory(first_enrolment_date=None)

        assert Trial.objects.count() == 3

        patient_first_enrolment_date = None
        assert Trial.objects.by_first_enrolment_date(patient_first_enrolment_date).count() == 3

        patient_first_enrolment_date = 1
        assert list(Trial.objects.by_first_enrolment_date(patient_first_enrolment_date).order_by('id')) == [t1, t3]

        patient_first_enrolment_date = "1"
        assert list(Trial.objects.by_first_enrolment_date(patient_first_enrolment_date).order_by('id')) == [t1, t3]

        patient_first_enrolment_date = 3
        assert Trial.objects.by_first_enrolment_date(patient_first_enrolment_date).count() == 3

        patient_first_enrolment_date = "not a number"
        assert Trial.objects.by_first_enrolment_date(patient_first_enrolment_date).count() == 3

    @pytest.mark.django_db
    def test_by_location(self):
        country = CountryFactory(title='USA')
        state = StateFactory(title='CA', country=country)
        location1 = LocationFactory(city='San-Diego', title='San-Diego, CA, USA',
                                    state_id=state.id, country_id=country.id)
        location2 = LocationFactory(city='San-Francisco', title='San-Francisco, CA, USA',
                                    state_id=state.id, country_id=country.id)

        state2 = StateFactory(title='NV', country_id=country.id)
        location3 = LocationFactory(city='Las Vegas', title='Las Vegas, NV, USA',
                                    state_id=state2.id, country_id=country.id)

        country2 = CountryFactory(title='Germany')
        state3 = StateFactory(title='Bavaria', country_id=country2.id)
        location4 = LocationFactory(city='Munich', title='Munich, Bavaria, Germany',
                                    state_id=state3.id, country_id=country2.id)

        t1 = TrialFactory()
        t2 = TrialFactory()
        t2.locations.set([location1, location4])
        t3 = TrialFactory()
        t3.locations.set([location4])
        t4 = TrialFactory()
        t4.locations.set([location2])
        t5 = TrialFactory()
        t5.locations.set([location3])

        assert Trial.objects.count() == 5

        assert Trial.objects.by_location(country=None, state=None).count() == 5

        assert list(Trial.objects.by_location(country="usa", state=None).order_by('id')) == [t2, t4, t5]

        assert list(Trial.objects.by_location(country="usa", state="ny").order_by('id')) == [t2, t4, t5]

        assert list(Trial.objects.by_location(country="usa", state="ca").order_by('id')) == [t2, t4]

        assert list(Trial.objects.by_location(country="usa", state="nv").order_by('id')) == [t5]

        assert list(Trial.objects.by_location(country="Germany", state=None).order_by('id')) == [t2, t3]

        assert list(Trial.objects.by_location(country="GerMany", state="Bavaria").order_by('id')) == [t2, t3]

    @pytest.mark.django_db
    def test_by_location_no_duplicates(self):
        """Test that trials with multiple locations in the same state/country don't appear multiple times."""
        country = CountryFactory(title='USA')
        state = StateFactory(title='CA', country=country)
        location1 = LocationFactory(city='San-Diego', title='San-Diego, CA, USA',
                                    state_id=state.id, country_id=country.id)
        location2 = LocationFactory(city='Los-Angeles', title='Los-Angeles, CA, USA',
                                    state_id=state.id, country_id=country.id)
        location3 = LocationFactory(city='San-Francisco', title='San-Francisco, CA, USA',
                                    state_id=state.id, country_id=country.id)

        state2 = StateFactory(title='TX', country=country)
        location4 = LocationFactory(city='Houston', title='Houston, TX, USA',
                                    state_id=state2.id, country_id=country.id)
        location5 = LocationFactory(city='Dallas', title='Dallas, TX, USA',
                                    state_id=state2.id, country_id=country.id)

        # Trial with multiple locations in the same state (CA)
        t1 = TrialFactory()
        t1.locations.set([location1, location2, location3])

        # Trial with single location in CA
        t2 = TrialFactory()
        t2.locations.set([location1])

        # Trial with locations in multiple states (CA and TX)
        t3 = TrialFactory()
        t3.locations.set([location1, location4])

        # Trial with multiple locations in TX only
        t4 = TrialFactory()
        t4.locations.set([location4, location5])

        assert Trial.objects.count() == 4

        # Filter by state CA - t1 has 3 locations but should appear only once
        ca_trials = Trial.objects.by_location(country="usa", state="ca")
        assert ca_trials.count() == 3
        assert list(ca_trials.order_by('id')) == [t1, t2, t3]

        # Filter by state TX - t4 has 2 locations but should appear only once
        tx_trials = Trial.objects.by_location(country="usa", state="tx")
        assert tx_trials.count() == 2
        assert list(tx_trials.order_by('id')) == [t3, t4]

        # Filter by country USA - all trials have locations in USA, some with multiple
        usa_trials = Trial.objects.by_location(country="usa", state=None)
        assert usa_trials.count() == 4
        assert list(usa_trials.order_by('id')) == [t1, t2, t3, t4]

    @pytest.mark.django_db
    def test_by_distance(self):
        country = CountryFactory(title='USA')
        state = StateFactory(title='CA', country=country)
        location1 = LocationFactory(
            city='San-Diego', title='San-Diego, CA, USA', state_id=state.id, country_id=country.id,
            geo_point=Point(48.8322, 2.3561, srid=4326)
        )
        location2 = LocationFactory(
            city='San-Francisco', title='San-Francisco, CA, USA', state_id=state.id, country_id=country.id,
            geo_point=Point(45.7679, 4.8506, srid=4326)
        )

        state2 = StateFactory(title='NV', country_id=country.id)
        location3 = LocationFactory(
            city='Las Vegas', title='Las Vegas, NV, USA', state_id=state2.id, country_id=country.id,
            geo_point=Point(30.0, 15.0, srid=4326)
        )

        country2 = CountryFactory(title='Germany')
        state3 = StateFactory(title='Bavaria', country_id=country2.id)
        location4 = LocationFactory(
            city='Munich', title='Munich, Bavaria, Germany', state_id=state3.id, country_id=country2.id,
            geo_point=None
        )

        t1 = TrialFactory()
        t2 = TrialFactory()
        t2.locations.set([location1, location4])
        t3 = TrialFactory()
        t3.locations.set([location4])
        t4 = TrialFactory()
        t4.locations.set([location2])
        t5 = TrialFactory()
        t5.locations.set([location3])

        assert Trial.objects.count() == 5

        user_geo_point = Point(50.0, 1.0, srid=4326)

        def scoped_trials(scope, geo_point, distance, distance_units):
            if not geo_point or not distance or not distance_units:
                return scope

            max_distance = D(mi=distance) if distance_units == 'miles' else D(km=distance)
            return scope.with_distance_optimized(geo_point, max_distance).by_distance(geo_point, distance, distance_units)

        assert scoped_trials(Trial.objects.all(), None, None, None).count() == 5

        assert list(scoped_trials(Trial.objects.all(), user_geo_point, 10000, 'miles').order_by('distance')) == [t2, t4, t5]
        assert list(scoped_trials(Trial.objects.all(), user_geo_point, 10000, 'miles').order_by('-distance')) == [t5, t4, t2]
        assert list(scoped_trials(Trial.objects.all(), user_geo_point, 10000, 'kilometers').order_by('distance')) == [t2, t4, t5]
        assert list(scoped_trials(Trial.objects.all(), user_geo_point, 10000, 'kilometers').order_by('-distance')) == [t5, t4, t2]

        assert list(scoped_trials(Trial.objects.all(), user_geo_point, 400, 'miles').order_by('distance')) == [t2, t4]
        assert list(scoped_trials(Trial.objects.all(), user_geo_point, 400, 'miles').order_by('-distance')) == [t4, t2]
        assert list(scoped_trials(Trial.objects.all(), user_geo_point, 400, 'kilometers')) == [t2]

        distance = scoped_trials(Trial.objects.all(), user_geo_point, 400, 'kilometers').first().distance
        assert int(distance.km + 0.5) == 198
        assert int(distance.mi + 0.5) == 123

    @pytest.mark.django_db
    def test_filter_by_patient_info(self):
        t1 = TrialFactory(age_low_limit=0, age_high_limit=100, no_mental_health_disorder_required=True)
        t2 = TrialFactory(age_low_limit=0, age_high_limit=None, no_mental_health_disorder_required=True)
        t4 = TrialFactory(age_low_limit=18, age_high_limit=None, no_mental_health_disorder_required=True)
        t5 = TrialFactory(age_low_limit=None, age_high_limit=45, no_mental_health_disorder_required=True)
        t6 = TrialFactory(age_low_limit=None, age_high_limit=None, no_mental_health_disorder_required=True)
        t3 = TrialFactory(
            # PATIENT REQUIREMENTS
            age_low_limit=18,
            age_high_limit=100,
            gender='M',
            consent_capability_required=True,
            no_tobacco_use_required=False,
            no_substance_use_required=False,
            negative_pregnancy_test_result_required=True,
            no_pregnancy_or_lactation_required=False,
            contraceptive_use_requirement=True,
            no_geographic_exposure_risk_required=False,
            no_mental_health_disorder_required=False,
            no_concomitant_medication_required=False,
            caregiver_availability_required=True,

            # Viral Infection Status
            no_hiv_required=True,
            no_hepatitis_b_required=True,
            no_hepatitis_c_required=True,

            # Disease / Treatments
            disease='multiple myeloma',
            refractory_required=False,
            not_refractory_required=False,
            prior_therapy_lines=None,
            therapy_lines_count_min=1,
            therapy_lines_count_max=3,
            relapse_count_min=1,
            relapse_count_max=3,
            remission_duration_min=2,
            washout_period_duration=28,  # days, 4 weeks
            no_other_active_malignancies_required=False,

            # Performance Status
            karnofsky_performance_score_min=50,
            karnofsky_performance_score_max=100,
            ecog_performance_status_min=1,
            ecog_performance_status_max=2,

            # General Health
            heart_rate_min=80,
            heart_rate_max=100,
            systolic_blood_pressure_min=60,
            systolic_blood_pressure_max=90,
            diastolic_blood_pressure_min=100,
            diastolic_blood_pressure_max=120,
            weight_min=65,
            weight_max=90,
            bmi_min=18,
            bmi_max=25,

            # Hematologic and Biochemical
            hemoglobin_level_min=14.2,
            hemoglobin_level_max=17.5,
            platelet_count_min=150,
            platelet_count_max=400,
            red_blood_cell_count_min=4.7,
            red_blood_cell_count_max=6.1,
            white_blood_cell_count_min=4.5,
            white_blood_cell_count_max=11.0,
            absolute_neutrophile_count_min=2500,
            absolute_neutrophile_count_max=6000,
            serum_creatinine_level_abs_min=0.6,
            serum_creatinine_level_abs_max=1.1,
            serum_creatinine_level_uln_min=1.0,
            serum_creatinine_level_uln_max=2.5,
            creatinine_clearance_rate_min=97,
            creatinine_clearance_rate_max=137,
            estimated_glomerular_filtration_rate_min=85,
            estimated_glomerular_filtration_rate_max=150,
            liver_enzyme_level_ast_abs_min=8,
            liver_enzyme_level_ast_abs_max=33,
            liver_enzyme_level_ast_uln_min=1.0,
            liver_enzyme_level_ast_uln_max=2.0,
            liver_enzyme_level_alt_abs_min=29,
            liver_enzyme_level_alt_abs_max=33,
            liver_enzyme_level_alt_uln_min=1.0,
            liver_enzyme_level_alt_uln_max=2.0,
            liver_enzyme_level_alp_abs_min=80,
            liver_enzyme_level_alp_abs_max=150,
            liver_enzyme_level_alp_uln_min=1.0,
            liver_enzyme_level_alp_uln_max=2.0,
            albumin_min=0.1,
            albumin_max=10.2,
            serum_bilirubin_total_level_abs_min=0.1,
            serum_bilirubin_total_level_abs_max=1.2,
            serum_bilirubin_total_level_uln_min=1.0,
            serum_bilirubin_total_level_uln_max=2.5,
            serum_bilirubin_direct_level_abs_min=0.03,
            serum_bilirubin_direct_level_abs_max=0.3,
            serum_bilirubin_direct_level_uln_min=1.0,
            serum_bilirubin_direct_level_uln_max=2.5,

            # Bone Health and Organ Function
            bone_imaging_result_required=True,
            ejection_fraction_min=50,
            ejection_fraction_max=100,
            pulmonary_function_test_result_required=True,

            # Lab Values
            serum_monoclonal_protein_level_min=30,
            serum_monoclonal_protein_level_max=100,
            urine_monoclonal_protein_level_min=5,
            urine_monoclonal_protein_level_max=30,
            serum_calcium_level_min=8.5,
            serum_calcium_level_max=10.5,
            lactate_dehydrogenase_level_min=140,
            lactate_dehydrogenase_level_max=280,
            ldh_units=233,

            # Comorbidities
            pre_existing_conditions_excluded=["Systemic DLBCL"],
            no_active_infection_required=True,

            # Genetic Markers and Transplants
            cytogenic_markers_required=['t(4;14)'],
            cytogenic_markers_all=['t(4;14)', '-5'],
            molecular_markers_required=['KRAS'],
            molecular_markers_excluded=['MYC'],
            stem_cell_transplant_history_required=False,
            day_since_stem_cell_transplant_required=60,
            stem_cell_transplant_history_excluded=['recent SCT'],

            # Prognostic Indices and Scoring Systems
            meets_gelf=True,
            flipi_score_min=1,
            flipi_score_max=3,
            tumor_grade_min=20,
            tumor_grade_max=40,
        )

        assert Trial.objects.count() == 6

        patient_info = None
        assert Trial.objects.filter_by_patient_info(patient_info)[0].count() == 6

        patient_info = PatientInfo(patient_age=None)
        assert Trial.objects.filter_by_patient_info(patient_info)[0].count() == 6

        patient_info = PatientInfo(patient_age=2)
        assert Trial.objects.filter_by_patient_info(patient_info)[0].count() == 4
        assert set(Trial.objects.filter_by_patient_info(patient_info)[0]) == {t1, t2, t5, t6}

        patient_info = PatientInfo(
            patient_age=20,
            gender='M',
            weight=70,
            height=170,
            systolic_blood_pressure=90,
            diastolic_blood_pressure=110,
            disease='multiple myeloma',
            stage='II',
            karnofsky_performance_score=70,
            no_other_active_malignancies=True,
            prior_therapy="One line",
            relapse_count=2,
            hemoglobin_level=15,
            absolute_neutrophile_count=3000,
            platelet_count=200,
            white_blood_cell_count=6,
            serum_creatinine_level=0.8,
            creatinine_clearance_rate=110,
            liver_enzyme_levels_ast=24,
            liver_enzyme_levels_alt=31,
            liver_enzyme_levels_alp=100,
            albumin_level=1.1,
            serum_bilirubin_level_total=0.4,
            serum_bilirubin_level_direct=0.2,
            monoclonal_protein_serum=45,
            monoclonal_protein_urine=14,
            lactate_dehydrogenase_level=200,
            serum_calcium_level=9.3,
            pulmonary_function_test_result=False,
            bone_imaging_result=False,
            ejection_fraction=60,
            consent_capability=True,
            no_mental_health_disorder_status=False,
            no_concomitant_medication_status=False,
            no_active_infection_status=True,
            no_tobacco_use_status=True,
            no_substance_use_status=True,
            no_geographic_exposure_risk=True,
            contraceptive_use=False,
            no_pregnancy_or_lactation_status=False,
            pregnancy_test_result=False,
            ecog_performance_status=2,
            flipi_score=0,
            flipi_score_options=", age, stage",
            tumor_grade=20,
            plasma_cell_leukemia=True,
            progression="active",
            treatment_refractory_status="notRefractory",
            molecular_markers="KRAS",
            stem_cell_transplant_history=["Autologous"],
            # first_line_date=timezone.now() - dt.timedelta(days=110)  # too complex to support it on updates
        )
        normalize_patient_info(patient_info)
        categories = PreExistingConditionCategoryFactory.create_batch(3)
        patient_info._pre_existing_condition_categories = categories

        assert Trial.objects.filter_by_patient_info(patient_info)[0].count() == 1
        assert Trial.objects.filter_by_patient_info(patient_info)[0][0] == t3

        assert Trial.objects.filter_by_patient_info(patient_info, add_traces=True)[0].count() == 1

        # print("\n\n>>>>Trial.objects.filter_by_patient_info(patient_info, add_traces=True)[1]", Trial.objects.filter_by_patient_info(patient_info, add_traces=True)[1])
        assert Trial.objects.filter_by_patient_info(patient_info, add_traces=True)[1] == [{'attr': 'patient_info.patient_age', 'val': 20, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.gender', 'val': 'M', 'records': 6, 'dropped': 0}, {'attr': 'patient_info.weight', 'val': 70, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.bmi', 'val': 24.221453287197235, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.systolic_blood_pressure', 'val': 90, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.diastolic_blood_pressure', 'val': 110, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.disease', 'val': 'multiple myeloma', 'records': 6, 'dropped': 0}, {'attr': 'patient_info.stage', 'val': 'II', 'records': 6, 'dropped': 0}, {'attr': 'patient_info.karnofsky_performance_score', 'val': 70, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.ecog_performance_status', 'val': 2, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.no_other_active_malignancies', 'val': True, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.pre_existing_condition_categories', 'val': ['pre_existing_condition_category_0', 'pre_existing_condition_category_1', 'pre_existing_condition_category_2'], 'records': 6, 'dropped': 0}, {'attr': 'patient_info.molecular_markers', 'val': 'KRAS', 'records': 6, 'dropped': 0}, {'attr': 'patient_info.plasma_cell_leukemia', 'val': True, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.progression', 'val': 'active', 'records': 6, 'dropped': 0}, {'attr': 'patient_info.measurable_disease_imwg', 'val': True, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.prior_therapy', 'val': 'One line', 'records': 6, 'dropped': 0}, {'attr': 'patient_info.stem_cell_transplant_history', 'val': ['Autologous'], 'records': 6, 'dropped': 0}, {'attr': 'patient_info.relapse_count', 'val': 2, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.absolute_neutrophile_count', 'val': 3000, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.platelet_count', 'val': 200, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.white_blood_cell_count', 'val': 6, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.serum_calcium_level', 'val': 9.3, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.creatinine_clearance_rate', 'val': 110, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.serum_creatinine_level', 'val': 0.8, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.hemoglobin_level', 'val': 15, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.meets_crab', 'val': False, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.estimated_glomerular_filtration_rate', 'val': 129.93, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.liver_enzyme_levels_ast', 'val': 24, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.liver_enzyme_levels_alt', 'val': 31, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.liver_enzyme_levels_alp', 'val': 100, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.albumin_level', 'val': 1.1, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.serum_bilirubin_level_total', 'val': 0.4, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.serum_bilirubin_level_direct', 'val': 0.2, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.monoclonal_protein_serum', 'val': 45, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.monoclonal_protein_urine', 'val': 14, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.lactate_dehydrogenase_level', 'val': 200, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.ejection_fraction', 'val': 60, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.no_hiv_status', 'val': True, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.no_hepatitis_b_status', 'val': True, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.no_hepatitis_c_status', 'val': True, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.consent_capability', 'val': True, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.no_pregnancy_or_lactation_status', 'val': True, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.pregnancy_test_result', 'val': True, 'records': 6, 'dropped': 0}, {'attr': 'patient_info.no_mental_health_disorder_status', 'val': False, 'records': 1, 'dropped': 5}, {'attr': 'patient_info.no_concomitant_medication_status', 'val': False, 'records': 1, 'dropped': 0}, {'attr': 'patient_info.no_tobacco_use_status', 'val': True, 'records': 1, 'dropped': 0}, {'attr': 'patient_info.no_substance_use_status', 'val': True, 'records': 1, 'dropped': 0}, {'attr': 'patient_info.no_geographic_exposure_risk', 'val': True, 'records': 1, 'dropped': 0}, {'attr': 'patient_info.no_active_infection_status', 'val': True, 'records': 1, 'dropped': 0}, {'attr': 'patient_info.renal_adequacy_status', 'val': False, 'records': 1, 'dropped': 0}]

        # search with ULNs

        Trial.objects.filter(id=t3.id).update(
            serum_creatinine_level_uln_min=None,
            serum_creatinine_level_uln_max=None,
            liver_enzyme_level_ast_uln_min=None,
            liver_enzyme_level_ast_uln_max=None,
            liver_enzyme_level_alt_abs_min=None,
            liver_enzyme_level_alt_abs_max=None,
            liver_enzyme_level_alp_abs_min=None,
            liver_enzyme_level_alp_abs_max=None,
            liver_enzyme_level_alp_uln_min=None,
            liver_enzyme_level_alp_uln_max=None,
            serum_bilirubin_total_level_uln_min=None,
            serum_bilirubin_total_level_uln_max=None,
            serum_bilirubin_direct_level_uln_min=None,
            serum_bilirubin_direct_level_uln_max=None,
            liver_enzyme_level_alt_uln_min=1.5,
            liver_enzyme_level_alt_uln_max=2.0
        )
        t3.refresh_from_db()

        assert t3.liver_enzyme_level_alt_abs_min is None
        assert t3.liver_enzyme_level_alt_abs_max is None

        patient_info.ethnicity = 'Caucasian/European'
        patient_info.liver_enzyme_levels_alt = 75

        assert Trial.objects.filter_by_patient_info(patient_info)[0].count() == 1

    @pytest.mark.django_db
    def test_filter_by_patient_info_relapse_count_zero(self):
        """Regression for #51 / CB #4186: relapse_count=0 must be treated as a
        real value, not blank, so trials requiring relapse_count_min >= 1 are
        excluded for never-relapsed patients."""
        t_no_req = TrialFactory(
            relapse_count_min=None, relapse_count_max=None, disease='multiple myeloma',
        )
        t_min_1 = TrialFactory(
            relapse_count_min=1, relapse_count_max=None, disease='multiple myeloma',
        )
        t_min_0 = TrialFactory(
            relapse_count_min=0, relapse_count_max=None, disease='multiple myeloma',
        )

        patient_zero = PatientInfo(
            disease='multiple myeloma', relapse_count=0, prior_therapy='None',
        )
        result_ids = set(
            Trial.objects.filter_by_patient_info(patient_zero)[0].values_list('id', flat=True)
        )
        assert t_no_req.id in result_ids
        assert t_min_0.id in result_ids
        assert t_min_1.id not in result_ids, (
            'Trial requiring relapse_count_min=1 must be excluded when '
            'patient has relapse_count=0 (never relapsed).'
        )

        # Patient with relapse_count=None (truly missing) still matches all
        # three trials — unchanged behavior.
        patient_blank = PatientInfo(
            disease='multiple myeloma', relapse_count=None, prior_therapy='None',
        )
        blank_ids = set(
            Trial.objects.filter_by_patient_info(patient_blank)[0].values_list('id', flat=True)
        )
        assert {t_no_req.id, t_min_0.id, t_min_1.id}.issubset(blank_ids)

    # weights used throughout goodness score tests
    _BENEFIT_W, _BURDEN_W, _RISK_W, _DIST_W = 50, 25, 15, 10

    @classmethod
    def _check_goodness_score(cls, trial: Trial, is_home_location: bool, expected: float, pi: PatientInfo) -> None:
        """
        Helper function for goodness score tests.
        """
        assert math.isclose(trial.goodness_score, expected, rel_tol=1e-8), \
            f"Expected {expected} but got {trial.goodness_score} for trial {trial.id}"
        w_sum = cls._BENEFIT_W + cls._BURDEN_W + cls._RISK_W + cls._DIST_W
        calculated = (
                cls._BENEFIT_W * (trial.benefit_score or 0) / 20.0
                + cls._BURDEN_W * _complement(trial.patient_burden_score, 20)
                + cls._RISK_W * _complement(trial.risk_score, 20)
                + cls._DIST_W * int(is_home_location) * (1 - trial.get_distance_penalty(pi) / 20.0)
        ) * 100 / w_sum
        calculated = int(calculated + 0.5)
        assert math.isclose(calculated, trial.goodness_score, rel_tol=1e-8), \
            f"Calculated score does not match expected score for trial {trial.id}: {calculated} != {trial.goodness_score}"
        calculated = trial.get_goodness_score(
            pi,
            benefit_weight=cls._BENEFIT_W,
            patient_burden_weight=cls._BURDEN_W,
            risk_weight=cls._RISK_W,
            distance_penalty_weight=cls._DIST_W,
        )
        assert math.isclose(calculated, trial.goodness_score, rel_tol=1e-8), \
            f"Trial.get_goodness_score does not match expected score for trial {trial.id}: {calculated} != {trial.goodness_score}"

    @pytest.mark.django_db
    def test_goodness_score(self):
        """
        Test the calculation of Trial.objects.with_goodness_score
        """
        # Locations
        country = CountryFactory(title='USA')
        state = StateFactory(title='CA', country=country)
        home_location = LocationFactory(
            city='San-Diego', title='San-Diego, CA, USA', state_id=state.id, country_id=country.id,
            geo_point=Point(47.3679, 4.8506, srid=4326)
        )
        close_location = LocationFactory(
            city='Santa-Cruz', title='Santa-Cruz, CA, USA', state_id=state.id, country_id=country.id,
            geo_point=Point(47.3679, 3.3061, srid=4326)
        )
        remote_location = LocationFactory(
            city='San-Francisco', title='San-Francisco, CA, USA', state_id=state.id, country_id=country.id,
            geo_point=Point(40.7679, 4.8506, srid=4326)
        )

        # Patient
        patient_info = PatientInfo(
            longitude=47.3679,
            latitude=4.8506,
            geo_point=Point(47.3679, 4.8506, srid=4326),
        )
        # weights are class constants: benefit=50, burden=25, risk=15, dist=10

        # Trials
        TrialFactory.create(
            patient_burden_score=None,
            risk_score=None,
            benefit_score=None,
        )
        TrialFactory.create(
            patient_burden_score=15,
            risk_score=None,
            benefit_score=None,
        )
        TrialFactory.create(
            patient_burden_score=None,
            risk_score=13,
            benefit_score=None,
        )
        TrialFactory.create(
            patient_burden_score=None,
            risk_score=None,
            benefit_score=13,
        )
        TrialFactory.create(
            patient_burden_score=15,
            risk_score=13,
            benefit_score=20,
        )
        t = TrialFactory.create(
            patient_burden_score=15,
            risk_score=13,
            benefit_score=20,
        )
        LocationTrial.objects.create(trial=t, location=home_location)
        t = TrialFactory.create(
            patient_burden_score=15,
            risk_score=13,
            benefit_score=20,
        )
        LocationTrial.objects.create(trial=t, location=remote_location)
        t = TrialFactory.create(
            patient_burden_score=0,
            risk_score=0,
            benefit_score=20,
        )
        LocationTrial.objects.create(trial=t, location=home_location)
        t = TrialFactory.create(
            patient_burden_score=15,
            risk_score=13,
            benefit_score=20,
        )
        LocationTrial.objects.create(trial=t, location=close_location)
        assert Trial.objects.count() == 9

        # Checking goodness score calculation
        max_distance = None

        trials = Trial.objects.with_goodness_score_optimized(
            benefit_weight=self._BENEFIT_W,
            patient_burden_weight=self._BURDEN_W,
            risk_weight=self._RISK_W,
            distance_penalty_weight=self._DIST_W,
            geo_point=patient_info.geo_point,
        ).order_by("id")

        self._check_goodness_score(trials[0], False, 0, patient_info)
        self._check_goodness_score(trials[1], False, 6, patient_info)
        self._check_goodness_score(trials[2], False, 5, patient_info)
        self._check_goodness_score(trials[3], False, 33, patient_info)
        self._check_goodness_score(trials[4], False, 62, patient_info)
        self._check_goodness_score(trials[5], True, 72, patient_info)
        self._check_goodness_score(trials[6], False, 62, patient_info)
        self._check_goodness_score(trials[7], True, 100, patient_info)
        self._check_goodness_score(trials[8], True, 66, patient_info)

    @pytest.mark.django_db
    def test_goodness_score_uses_geo_point_without_prior_distance_annotation(self):
        """
        Regression test: with_goodness_score_optimized must compute distance
        via its own inline subquery when geo_point is supplied, independent of
        whether with_distance_optimized was called first.

        Before the fix, without a pre-existing 'distance' annotation the method
        fell back to distance_expr = 0.0 (max distance score), so all trials
        received the same distance contribution regardless of location.
        """
        country = CountryFactory(title='USA')
        state = StateFactory(title='CA', country=country)

        home_location = LocationFactory(
            city='Home', state_id=state.id, country_id=country.id,
            geo_point=Point(0.0, 0.0, srid=4326),
        )
        far_location = LocationFactory(
            city='Far', state_id=state.id, country_id=country.id,
            geo_point=Point(50.0, 0.0, srid=4326),  # far away
        )

        geo_point = Point(0.0, 0.0, srid=4326)

        # Two identical trials, differing only in location distance
        t_near = TrialFactory(benefit_score=10, patient_burden_score=10, risk_score=10)
        LocationTrial.objects.create(trial=t_near, location=home_location)

        t_far = TrialFactory(benefit_score=10, patient_burden_score=10, risk_score=10)
        LocationTrial.objects.create(trial=t_far, location=far_location)

        # Call with_goodness_score_optimized WITHOUT calling with_distance_optimized first
        trials = Trial.objects.filter(id__in=[t_near.id, t_far.id]).with_goodness_score_optimized(
            benefit_weight=25.0,
            patient_burden_weight=25.0,
            risk_weight=25.0,
            distance_penalty_weight=25.0,
            geo_point=geo_point,
        )
        scores = {t.id: t.goodness_score for t in trials}

        # The near trial must score strictly higher than the far trial
        assert scores[t_near.id] > scores[t_far.id], (
            f'Expected near trial ({scores[t_near.id]}) > far trial ({scores[t_far.id]}); '
            f'distance was not factored into the goodness score'
        )

    @pytest.mark.django_db
    def test_eligible_for_researcher_email(self):
        t1 = TrialFactory(researchers_emails=[])
        t2 = TrialFactory(researchers_emails=['val1', 'val2'])
        t3 = TrialFactory(researchers_emails=['val1'])
        t4 = TrialFactory(researchers_emails=['val2'])
        t5 = TrialFactory(researchers_emails=['val3'])

        assert Trial.objects.count() == 5

        assert Trial.objects.eligible_for_researcher_email(None).count() == 0

        assert Trial.objects.eligible_for_researcher_email('foo').count() == 0

        assert list(Trial.objects.eligible_for_researcher_email('val1').order_by('id')) == [t2, t3]
        assert list(Trial.objects.eligible_for_researcher_email('val2').order_by('id')) == [t2, t4]
        assert list(Trial.objects.eligible_for_researcher_email('val3').order_by('id')) == [t5]

    @pytest.mark.django_db
    def test_blank_list_attrs_not_in_traces(self):
        """
        supportive_therapies=[] and later_therapies=[] are blank (JSONField default).
        filter_by_patient_info must skip them and not emit a trace entry for either.
        """
        TrialFactory()
        patient_info = PatientInfo(
            disease='multiple myeloma',
            prior_therapy='One line',
            supportive_therapies=[],
            later_therapies=[],
        )
        normalize_patient_info(patient_info)

        _, traces = Trial.objects.filter_by_patient_info(patient_info, add_traces=True)
        trace_attrs = {t['attr'] for t in traces}

        assert 'patient_info.supportive_therapies' not in trace_attrs
        assert 'patient_info.later_therapies' not in trace_attrs

    @pytest.mark.django_db
    def test_estrogen_receptor_status_hierarchy(self):
        """
        er_plus_with_hi_exp and er_plus_with_low_exp are subtypes of er_plus.
        A BC trial requiring er_plus must include patients with either subtype,
        and must exclude patients with er_minus.
        """
        t_er_plus = TrialFactory(disease='breast cancer', estrogen_receptor_statuses_required=['er_plus'])
        t_no_req  = TrialFactory(disease='breast cancer', estrogen_receptor_statuses_required=[])

        assert Trial.objects.count() == 2

        # Subtype er_plus_with_hi_exp → parent er_plus → should match
        patient_hi = PatientInfo(disease='breast cancer', estrogen_receptor_status='er_plus_with_hi_exp')
        result_hi = set(Trial.objects.filter_by_patient_info(patient_hi)[0])
        assert t_er_plus in result_hi
        assert t_no_req in result_hi

        # Subtype er_plus_with_low_exp → parent er_plus → should match
        patient_low = PatientInfo(disease='breast cancer', estrogen_receptor_status='er_plus_with_low_exp')
        result_low = set(Trial.objects.filter_by_patient_info(patient_low)[0])
        assert t_er_plus in result_low
        assert t_no_req in result_low

        # er_minus has no parent mapping → should NOT match er_plus requirement
        patient_minus = PatientInfo(disease='breast cancer', estrogen_receptor_status='er_minus')
        result_minus = set(Trial.objects.filter_by_patient_info(patient_minus)[0])
        assert t_er_plus not in result_minus
        assert t_no_req in result_minus

    @pytest.mark.django_db
    def test_progesterone_receptor_status_hierarchy(self):
        """
        pr_plus_with_hi_exp and pr_plus_with_low_exp are subtypes of pr_plus.
        """
        t_pr_plus = TrialFactory(disease='breast cancer', progesterone_receptor_statuses_required=['pr_plus'])
        t_no_req  = TrialFactory(disease='breast cancer', progesterone_receptor_statuses_required=[])

        patient_hi = PatientInfo(disease='breast cancer', progesterone_receptor_status='pr_plus_with_hi_exp')
        result = set(Trial.objects.filter_by_patient_info(patient_hi)[0])
        assert t_pr_plus in result

        patient_minus = PatientInfo(disease='breast cancer', progesterone_receptor_status='pr_minus')
        result = set(Trial.objects.filter_by_patient_info(patient_minus)[0])
        assert t_pr_plus not in result
        assert t_no_req in result

    @pytest.mark.django_db
    def test_filtered_trials_none_patient_info_regular_search(self):
        """filtered_trials must not raise when patient_info is None (regular search path)."""
        from trials.services.study_preferences import StudyPreferences
        TrialFactory()
        qs, traces = Trial.objects.filtered_trials(
            search_options={},
            study_info=StudyPreferences(),
            patient_info=None,
            add_traces=False,
            search_type=None,
        )
        # Should return a queryset without raising AttributeError on geo_point
        assert qs.count() >= 0

    @pytest.mark.django_db
    def test_filtered_trials_none_patient_info_admin_search(self):
        """filtered_trials must not raise when patient_info is None (admin/all search path)."""
        from trials.services.study_preferences import StudyPreferences
        TrialFactory()
        qs, traces = Trial.objects.filtered_trials(
            search_options={},
            study_info=StudyPreferences(),
            patient_info=None,
            add_traces=False,
            search_type='all',
        )
        assert qs.count() >= 0

