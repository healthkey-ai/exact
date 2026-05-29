import pytest

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from tests.factories import TrialFactory


class TestUserToTrialAttrMatcher:
    @pytest.mark.parametrize('disease, expected_code', [
        ('multiple myeloma', 'MM'),
        ('follicular lymphoma', 'FL'),
        ('breast cancer', 'BC'),
        ('chronic lymphocytic leukemia', 'CLL'),
        ('mantle cell lymphoma', 'MCL'),
        ('Mantle Cell Lymphoma', 'MCL'),  # casing tolerance
        ('something else', None),
    ])
    @pytest.mark.django_db
    def test_disease_code_normalization(self, disease, expected_code):
        trial = TrialFactory(disease=disease)
        patient_info = PatientInfo(disease=disease)

        service = UserToTrialAttrMatcher(trial, patient_info)
        assert service.disease_code == expected_code

    @pytest.mark.django_db
    def test_potential_attrs_to_check(self):
        trial = TrialFactory()
        patient_info = PatientInfo(disease='multiple myeloma')

        service = UserToTrialAttrMatcher(trial, patient_info)
        assert service.trial_match_status() == 'eligible'

        trial.age_low_limit = 18
        trial.save()
        assert service.trial_match_status() == 'potential'

        patient_info.patient_age = 16
        assert service.trial_match_status() == 'not_eligible'

    @pytest.mark.django_db
    def test_therapy_related_things_mismatch_status(self):
        trial = TrialFactory()
        patient_info = PatientInfo(disease='multiple myeloma')

        service = UserToTrialAttrMatcher(trial, patient_info)
        assert service.therapy_related_things_mismatch_status() == 'unknown'

        patient_info.prior_therapy = 'None'
        assert service.therapy_related_things_mismatch_status() == 'not_matched'

        patient_info.prior_therapy = 'More than two lines of therapy'
        assert service.therapy_related_things_mismatch_status() == 'unknown'

    @pytest.mark.django_db
    def test_therapy_related_things_match_status(self):
        patient_info = PatientInfo(disease='multiple myeloma', prior_therapy='None')

        trial1 = TrialFactory(therapies_required=['vrd'])
        trial2 = TrialFactory(therapies_excluded=['vrd'])

        assert UserToTrialAttrMatcher(trial1, patient_info).therapy_related_things_match_status() == {
            'therapiesRequired': {'status': 'not_matched', 'values': []},
            'therapiesExcluded': {'status': 'matched', 'values': []},
            'therapyTypesRequired': {'status': 'matched', 'values': []},
            'therapyTypesExcluded': {'status': 'matched', 'values': []},
            'therapyComponentsRequired': {'status': 'matched', 'values': []},
            'therapyComponentsExcluded': {'status': 'matched', 'values': []}
        }

        assert UserToTrialAttrMatcher(trial2, patient_info).therapy_related_things_match_status() == {
            'therapiesRequired': {'status': 'matched', 'values': []},
            'therapiesExcluded': {'status': 'matched', 'values': []},
            'therapyTypesRequired': {'status': 'matched', 'values': []},
            'therapyTypesExcluded': {'status': 'matched', 'values': []},
            'therapyComponentsRequired': {'status': 'matched', 'values': []},
            'therapyComponentsExcluded': {'status': 'matched', 'values': []}
        }

        patient_info.prior_therapy = 'One line'
        patient_info.first_line_therapy = 'dara_vrd'

        assert UserToTrialAttrMatcher(trial1, patient_info).therapy_related_things_match_status() == {
            'therapiesRequired': {'status': 'not_matched', 'values': ['Dara-VRd']},
            'therapiesExcluded': {'status': 'matched', 'values': ['Dara-VRd']},
            'therapyTypesRequired': {'status': 'matched', 'values': ['Corticosteroid', 'Immunomodulatory Drug (IMiD)', 'Monoclonal Antibody (Anti-CD38)', 'Proteasome Inhibitor', 'Treatment for High-Risk Smoldering Multiple Myeloma']},
            'therapyTypesExcluded': {'status': 'matched', 'values': ['Corticosteroid', 'Immunomodulatory Drug (IMiD)', 'Monoclonal Antibody (Anti-CD38)', 'Proteasome Inhibitor', 'Treatment for High-Risk Smoldering Multiple Myeloma']},
            'therapyComponentsRequired': {'status': 'matched', 'values': ['Bortezomib', 'Daratumumab', 'Dexamethasone', 'Lenalidomide']},
            'therapyComponentsExcluded': {'status': 'matched', 'values': ['Bortezomib', 'Daratumumab', 'Dexamethasone', 'Lenalidomide']}
        }

        assert UserToTrialAttrMatcher(trial2, patient_info).therapy_related_things_match_status() == {
            'therapiesRequired': {'status': 'matched', 'values': ['Dara-VRd']},
            'therapiesExcluded': {'status': 'matched', 'values': ['Dara-VRd']},
            'therapyTypesRequired': {'status': 'matched', 'values': ['Corticosteroid', 'Immunomodulatory Drug (IMiD)', 'Monoclonal Antibody (Anti-CD38)', 'Proteasome Inhibitor', 'Treatment for High-Risk Smoldering Multiple Myeloma']},
            'therapyTypesExcluded': {'status': 'matched', 'values': ['Corticosteroid', 'Immunomodulatory Drug (IMiD)', 'Monoclonal Antibody (Anti-CD38)', 'Proteasome Inhibitor', 'Treatment for High-Risk Smoldering Multiple Myeloma']},
            'therapyComponentsRequired': {'status': 'matched', 'values': ['Bortezomib', 'Daratumumab', 'Dexamethasone', 'Lenalidomide']},
            'therapyComponentsExcluded': {'status': 'matched', 'values': ['Bortezomib', 'Daratumumab', 'Dexamethasone', 'Lenalidomide']}
        }

        patient_info.first_line_therapy = 'vrd'

        assert UserToTrialAttrMatcher(trial1, patient_info).therapy_related_things_match_status() == {
            'therapiesRequired': {'status': 'matched', 'values': ['**VRd**']},
            'therapiesExcluded': {'status': 'matched', 'values': ['VRd']},
            'therapyTypesRequired': {'status': 'matched', 'values': ['Corticosteroid', 'Immunomodulatory Drug (IMiD)', 'Proteasome Inhibitor', 'Treatment for High-Risk Smoldering Multiple Myeloma']},
            'therapyTypesExcluded': {'status': 'matched', 'values': ['Corticosteroid', 'Immunomodulatory Drug (IMiD)', 'Proteasome Inhibitor', 'Treatment for High-Risk Smoldering Multiple Myeloma']},
            'therapyComponentsRequired': {'status': 'matched', 'values': ['Bortezomib', 'Dexamethasone', 'Lenalidomide']},
            'therapyComponentsExcluded': {'status': 'matched', 'values': ['Bortezomib', 'Dexamethasone', 'Lenalidomide']}
        }

        assert UserToTrialAttrMatcher(trial2, patient_info).therapy_related_things_match_status() == {
            'therapiesRequired': {'status': 'matched', 'values': ['VRd']},
            'therapiesExcluded': {'status': 'not_matched', 'values': ['**VRd**']},
            'therapyTypesRequired': {'status': 'matched', 'values': ['Corticosteroid', 'Immunomodulatory Drug (IMiD)', 'Proteasome Inhibitor', 'Treatment for High-Risk Smoldering Multiple Myeloma']},
            'therapyTypesExcluded': {'status': 'matched', 'values': ['Corticosteroid', 'Immunomodulatory Drug (IMiD)', 'Proteasome Inhibitor', 'Treatment for High-Risk Smoldering Multiple Myeloma']},
            'therapyComponentsRequired': {'status': 'matched', 'values': ['Bortezomib', 'Dexamethasone', 'Lenalidomide']},
            'therapyComponentsExcluded': {'status': 'matched', 'values': ['Bortezomib', 'Dexamethasone', 'Lenalidomide']}
        }

    @pytest.mark.django_db
    def test_receptor_status_hierarchy(self):
        """
        er_plus_with_hi_exp / er_plus_with_low_exp are subtypes of er_plus.
        The matcher must return 'eligible' for a BC trial requiring er_plus when
        the patient has one of those subtypes, and 'not_eligible' for er_minus.
        Same logic applies to PR (pr_plus) and HR (hr_plus).
        """
        # --- ER ---
        trial_er = TrialFactory(disease='breast cancer', estrogen_receptor_statuses_required=['er_plus'])

        assert UserToTrialAttrMatcher(trial_er, PatientInfo(
            disease='breast cancer', estrogen_receptor_status='er_plus_with_hi_exp'
        )).trial_match_status() == 'eligible'

        assert UserToTrialAttrMatcher(trial_er, PatientInfo(
            disease='breast cancer', estrogen_receptor_status='er_plus_with_low_exp'
        )).trial_match_status() == 'eligible'

        assert UserToTrialAttrMatcher(trial_er, PatientInfo(
            disease='breast cancer', estrogen_receptor_status='er_minus'
        )).trial_match_status() == 'not_eligible'

        # --- PR ---
        trial_pr = TrialFactory(disease='breast cancer', progesterone_receptor_statuses_required=['pr_plus'])

        assert UserToTrialAttrMatcher(trial_pr, PatientInfo(
            disease='breast cancer', progesterone_receptor_status='pr_plus_with_hi_exp'
        )).trial_match_status() == 'eligible'

        assert UserToTrialAttrMatcher(trial_pr, PatientInfo(
            disease='breast cancer', progesterone_receptor_status='pr_minus'
        )).trial_match_status() == 'not_eligible'

        # --- HR ---
        trial_hr = TrialFactory(disease='breast cancer', hr_statuses_required=['hr_plus'])

        assert UserToTrialAttrMatcher(trial_hr, PatientInfo(
            disease='breast cancer', hr_status='hr_plus_with_hi_exp'
        )).trial_match_status() == 'eligible'

        assert UserToTrialAttrMatcher(trial_hr, PatientInfo(
            disease='breast cancer', hr_status='hr_minus'
        )).trial_match_status() == 'not_eligible'

    @pytest.mark.django_db
    def test_treatment_refractory_status_unknown_when_falsy(self):
        """
        treatment_refractory_status should return 'unknown' for any falsy patient value
        (None, empty string), not only for None.  Matches CB's `if not value` logic.
        """
        # Trial that requires NOT refractory
        trial = TrialFactory(not_refractory_required=True)
        pi = PatientInfo(disease='multiple myeloma')

        matcher = UserToTrialAttrMatcher(trial, pi)

        # None → unknown
        pi.treatment_refractory_status = None
        assert matcher.attr_match_status('treatment_refractory_status') == 'unknown'

        # Empty string → unknown (this was the bug: `is None` missed '')
        pi.treatment_refractory_status = ''
        assert matcher.attr_match_status('treatment_refractory_status') == 'unknown'

        # A refractory patient → not_matched (the trial wants not-refractory)
        pi.treatment_refractory_status = 'primaryRefractory'
        assert matcher.attr_match_status('treatment_refractory_status') == 'not_matched'

        # A not-refractory patient → matched
        pi.treatment_refractory_status = 'notRefractory'
        assert matcher.attr_match_status('treatment_refractory_status') == 'matched'

    @pytest.mark.django_db
    def test_treatment_refractory_status_trial_has_no_requirement(self):
        """When trial doesn't require either refractory status → always matched."""
        trial = TrialFactory(not_refractory_required=False, refractory_required=False)
        pi = PatientInfo(disease='multiple myeloma')
        matcher = UserToTrialAttrMatcher(trial, pi)

        for value in (None, '', 'notRefractory', 'primaryRefractory'):
            pi.treatment_refractory_status = value
            assert matcher.attr_match_status('treatment_refractory_status') == 'matched', \
                f'Expected matched for value={value!r}'
