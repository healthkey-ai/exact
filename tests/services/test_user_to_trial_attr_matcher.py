import pytest

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.normalize import normalize_patient_info
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

    @pytest.mark.django_db
    def test_progression_empty_string_is_unknown(self):
        """Regression for #52 / CB #4306: the UI represents "Unknown"
        progression as '' (see ValueOptions.progressions). The matcher must
        treat '' the same as None, returning 'unknown' instead of falling
        through to 'not_matched' against a trial requiring active disease.
        """
        trial = TrialFactory(disease_progression_active_required=True)
        pi = PatientInfo(disease='multiple myeloma')
        matcher = UserToTrialAttrMatcher(trial, pi)

        # None → unknown (control, unchanged behavior)
        pi.progression = None
        assert matcher.attr_match_status('progression') == 'unknown'

        # Empty string → unknown (the fix path)
        pi.progression = ''
        assert matcher.attr_match_status('progression') == 'unknown'

        # 'active' patient → matched (sanity check)
        pi.progression = 'active'
        assert matcher.attr_match_status('progression') == 'matched'

        # 'smoldering' patient against active-required trial → not_matched
        pi.progression = 'smoldering'
        assert matcher.attr_match_status('progression') == 'not_matched'

    @pytest.mark.django_db
    def test_measurable_disease_imwg_unknown_for_empty_labs(self):
        """Regression for #54 / CB #4143-#4156: a patient with no IMWG-relevant
        lab values must show as Potential ('unknown'), not Ineligible
        ('not_matched'), against a trial requiring measurable_disease_imwg.

        Even when labs ARE present but the patient doesn't qualify (real False),
        the matcher should still return 'unknown' because measurable_disease_imwg
        is a derived/computed field the user cannot directly answer — surfacing
        the trial as Potential is the desired UX. The under_user_control flag
        in configs.py is what enforces this.
        """
        # Patient with all IMWG lab inputs blank → measurable_disease_imwg=None.
        pi = PatientInfo(
            disease='multiple myeloma',
            monoclonal_protein_serum=None,
            monoclonal_protein_urine=None,
            kappa_flc=None,
            lambda_flc=None,
        )
        normalize_patient_info(pi)
        assert pi.measurable_disease_imwg is None

        # Trial doesn't require → matched regardless of patient value.
        trial_no_req = TrialFactory(measurable_disease_imwg_required=None)
        assert UserToTrialAttrMatcher(trial_no_req, pi).attr_match_status('measurable_disease_imwg') == 'matched'

        # Trial requires + empty labs → 'unknown' (NOT 'not_matched').
        trial_requires = TrialFactory(measurable_disease_imwg_required=True)
        assert UserToTrialAttrMatcher(trial_requires, pi).attr_match_status('measurable_disease_imwg') == 'unknown'

        # Patient has qualifying labs (serum M-protein >= 0.5) → 'matched'.
        pi.monoclonal_protein_serum = 5
        normalize_patient_info(pi)
        assert pi.measurable_disease_imwg is True
        assert UserToTrialAttrMatcher(trial_requires, pi).attr_match_status('measurable_disease_imwg') == 'matched'

        # Patient has labs but doesn't qualify (real False) → still 'unknown'
        # because under_user_control prefers Potential over hard-reject on a
        # derived value the user couldn't have answered themselves.
        pi.monoclonal_protein_serum = 0.1  # below 0.5 g/dL threshold
        normalize_patient_info(pi)
        assert pi.measurable_disease_imwg is False
        assert UserToTrialAttrMatcher(trial_requires, pi).attr_match_status('measurable_disease_imwg') == 'unknown'

    @pytest.mark.django_db
    def test_meets_slim_unknown_for_empty_labs(self):
        """Regression for #54 / CB #4143-#4156: same UX rule for meets_slim.

        The derivation (PatientInfoAttributes.meets_slim) already returns None
        for all-blank inputs, but without under_user_control in the config the
        matcher coerced None -> False and returned 'not_matched'. The fix is
        the under_user_control flag added when the meets_slim entry was
        uncommented in configs.py.
        """
        pi = PatientInfo(
            disease='multiple myeloma',
            clonal_plasma_cells=None,
            kappa_flc=None,
            lambda_flc=None,
            bone_lesions='',
        )
        normalize_patient_info(pi)
        assert pi.meets_slim is None

        # Trial doesn't require → matched.
        trial_no_req = TrialFactory(meets_slim=None)
        assert UserToTrialAttrMatcher(trial_no_req, pi).attr_match_status('meets_slim') == 'matched'

        # Trial requires + empty labs → 'unknown' (NOT 'not_matched').
        trial_requires = TrialFactory(meets_slim=True)
        assert UserToTrialAttrMatcher(trial_requires, pi).attr_match_status('meets_slim') == 'unknown'

        # Patient meets SLiM via the S component (>=60% clonal plasma cells).
        pi.clonal_plasma_cells = 65
        normalize_patient_info(pi)
        assert pi.meets_slim is True
        assert UserToTrialAttrMatcher(trial_requires, pi).attr_match_status('meets_slim') == 'matched'

    @pytest.mark.django_db
    def test_peripheral_neuropathy_grade_zero_matches(self):
        """Regression for #53 / CB #4307: Grade 0 is a real clinical value,
        not blank. The configs.py entry must carry allow_blank_values=True
        so is_attr_blank() does not coerce 0 to 'unknown' against trials
        with peripheral_neuropathy_grade_max=0.
        """
        trial_no_limit = TrialFactory(disease='multiple myeloma')
        trial_max_zero = TrialFactory(disease='multiple myeloma', peripheral_neuropathy_grade_max=0)
        trial_max_two = TrialFactory(disease='multiple myeloma', peripheral_neuropathy_grade_max=2)

        pi = PatientInfo(disease='multiple myeloma')

        # Blank patient: no-limit trial matches; max=0 trial is unknown.
        pi.peripheral_neuropathy_grade = None
        assert UserToTrialAttrMatcher(trial_no_limit, pi).attr_match_status('peripheral_neuropathy_grade') == 'matched'
        assert UserToTrialAttrMatcher(trial_max_zero, pi).attr_match_status('peripheral_neuropathy_grade') == 'unknown'

        # Grade 0 patient: matches every trial (this is the fix path — used
        # to be 'unknown' against max=0 because 0 was treated as blank).
        pi.peripheral_neuropathy_grade = 0
        assert UserToTrialAttrMatcher(trial_no_limit, pi).attr_match_status('peripheral_neuropathy_grade') == 'matched'
        assert UserToTrialAttrMatcher(trial_max_zero, pi).attr_match_status('peripheral_neuropathy_grade') == 'matched'
        assert UserToTrialAttrMatcher(trial_max_two, pi).attr_match_status('peripheral_neuropathy_grade') == 'matched'

        # Grade 3 patient: excluded by any max< 3 trial.
        pi.peripheral_neuropathy_grade = 3
        assert UserToTrialAttrMatcher(trial_max_zero, pi).attr_match_status('peripheral_neuropathy_grade') == 'not_matched'
        assert UserToTrialAttrMatcher(trial_max_two, pi).attr_match_status('peripheral_neuropathy_grade') == 'not_matched'
