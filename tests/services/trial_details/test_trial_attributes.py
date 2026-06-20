import pytest

from tests.factories import *
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.trial_details.trial_attributes import TrialAttributes


class TestTrialAttributes:
    @pytest.mark.django_db
    def test_details(self):
        patient_info = PatientInfo(disease='multiple myeloma')
        trial = TrialFactory(hemoglobin_level_min=5, hemoglobin_level_max=15, stem_cell_transplant_history_excluded=['postASCT', 'preASCT'], therapies_required=['vrd'])

        res = TrialAttributes(trial, patient_info).details()
        # print("\n\n>>>>>set(res.keys())", set(res.keys()))
        assert set(res.keys()) == {'clonalPlasmaCellsMax', 'register', 'studyId', 'plateletCountMax', 'liverEnzymeLevelAltAbsMin', 'serumBilirubinDirectLevelAbsMin', 'serumCalciumLevelMax', 'consentCapabilityRequired', 'recruitmentStatus', 'noPregnancyOrLactationRequired', 'contactEmail', 'trialType', 'karnofskyPerformanceScoreMin', 'liverEnzymeLevelAlpUlnMax', 'bmiMax', 'noHepatitisBRequired', 'noActiveInfectionRequired', 'preExistingConditionsExcluded', 'distancePenalty', 'goodnessScore', 'postedDate', 'lactateDehydrogenaseLevelMax', 'liverEnzymeLevelAstAbsMax', 'noOtherActiveMalignanciesRequired', 'ageMax', 'briefSummary', 'disease', 'molecularMarkersExcluded', 'serumBilirubinDirectLevelUlnMin', 'patientBurdenScore', 'plannedTherapiesRequired', 'noSubstanceUseRequired', 'briefTitle', 'kappaLambdaAbnormalRequired', 'whiteBloodCellCountMin', 'serumCreatinineLevelAbsMin', 'estimatedGlomerularFiltrationRateMin', 'liverEnzymeLevelAlpAbsMin', 'riskScore', 'ageMin', 'locationsName', 'washoutPeriodDuration', 'contraceptiveUseRequirement', 'karnofskyPerformanceScoreMax', 'gender', 'languagesSkillsRequired', 'targetSampleSize', 'creatinineClearanceRateMax', 'serumBilirubinTotalLevelUlnMin', 'noMentalHealthDisorderRequired', 'officialTitle', 'toxicityGradeMax', 'systolicBloodPressureMax', 'albuminMax', 'noGeographicExposureRiskRequired', 'noTobaccoUseRequired', 'serumCalciumLevelMin', 'liverEnzymeLevelAstUlnMax', 'serumMonoclonalProteinLevelMax', 'phases', 'serumCreatinineLevelUlnMin', 'molecularMarkersRequired', 'serumBilirubinTotalLevelAbsMax', 'cytogenicMarkersExcluded', 'submittedDate', 'plannedTherapiesExcluded', 'plateletCountMin', 'serumBilirubinTotalLevelUlnMax', 'ecogPerformanceStatusMax', 'interventionTreatments', 'studyDesign', 'benefitScore', 'creatinineClearanceRateMin', 'ecogPerformanceStatusMin', 'relapseCountMin', 'meetsSLIM', 'negativePregnancyTestResultRequired', 'estimatedGlomerularFiltrationRateMax', 'clonalPlasmaCellsMin', 'peripheralNeuropathyGradeMax', 'link', 'therapyLinesCountMax', 'liverEnzymeLevelAlpAbsMax', 'serumBilirubinTotalLevelAbsMin', 'diastolicBloodPressureMax', 'liverEnzymeLevelAltAbsMax', 'stemCellTransplantHistoryExcluded', 'laySummary', 'ethnicityRequired', 'stemCellTransplantHistoryRequired', 'hemoglobinLevelMax', 'stages', 'noHivRequired', 'weightMin', 'urineMonoclonalProteinLevelMax', 'serumBilirubinDirectLevelUlnMax', 'liverEnzymeLevelAltUlnMin', 'therapyLinesCountMin', 'absoluteNeutrophileCountMin', 'diastolicBloodPressureMin', 'systolicBloodPressureMin', 'enrollmentCount', 'firstEnrolmentDate', 'liverEnzymeLevelAltUlnMax', 'ejectionFractionMin', 'therapiesRequired', 'liverEnzymeLevelAstUlnMin', 'supportiveTherapiesExcluded', 'supportiveTherapiesRequired', 'noHepatitisCRequired', 'absoluteNeutrophileCountMax', 'serumCreatinineLevelUlnMax', 'meetsCRAB', 'researchers', 'serumBilirubinDirectLevelAbsMax', 'redBloodCellCountMin', 'whiteBloodCellCountMax', 'bmiMin', 'participationCriteria', 'liverEnzymeLevelAlpUlnMin', 'pulmonaryFunctionTestResultRequired', 'serumCreatinineLevelAbsMax', 'serumMonoclonalProteinLevelMin', 'renalAdequacyRequired', 'hepaticAdequacyRequired', 'haematologicalAdequacyRequired', 'boneImagingResultRequired', 'peripheralNeuropathyGradeMin', 'refractoryRequired', 'distance', 'studyType', 'concomitantMedicationsExcluded', 'relapseCountMax', 'albuminMin', 'hemoglobinLevelMin', 'lastUpdateDate', 'measurableDiseaseImwgRequired', 'plasmaCellLeukemiaRequired', 'redBloodCellCountMax', 'diseaseProgressionActiveRequired', 'ejectionFractionMax', 'noConcomitantMedicationRequired', 'lactateDehydrogenaseLevelMin', 'matchScore', 'urineMonoclonalProteinLevelMin', 'weightMax', 'liverEnzymeLevelAstAbsMin', 'cytogenicMarkersRequired', 'sponsorName', 'caregiverAvailabilityRequired'}


    @pytest.mark.django_db
    def test_therapies(self, patient_info):
        trial = TrialFactory(therapies_required=['vrd'])

        patient_info.disease='multiple myeloma'
        patient_info.first_line_therapy = 'vrd'
        patient_info.second_line_therapy = 'idelalisib'

        res = TrialAttributes(trial, patient_info=patient_info).therapies(subform_attrs={})
        assert set(res.keys()) == {'therapiesRequired'}

    @pytest.mark.django_db
    def test_get_user_details(self, db):
        patient_info = PatientInfo(disease='multiple myeloma', plasma_cell_leukemia=False, prior_therapy=None, stem_cell_transplant_history=[])
        trial = TrialFactory()

        res = TrialAttributes(trial=trial, patient_info=patient_info).get_user_details(subform_attrs={})
        # print("\n\n>>>>>res", res)
        assert res == {'age_low_limit': {'ureadonly': False, 'ufield': 'patient_age', 'uvalue': None}, 'age_high_limit': {'ureadonly': False, 'ufield': 'patient_age', 'uvalue': None}, 'gender': {'ureadonly': False, 'ufield': 'gender', 'uvalue': None, 'search_type': 'str_value'}, 'ethnicity_required': {'ureadonly': False, 'ufield': 'ethnicity', 'uvalue': None}, 'languages_skills_required': {'ureadonly': False, 'ufield': 'languages_skills', 'uvalue': None}, 'weight_min': {'ureadonly': False, 'ufield': 'weight', 'uvalue': None}, 'weight_max': {'ureadonly': False, 'ufield': 'weight', 'uvalue': None}, 'bmi_min': {'ureadonly': True, 'ufield': 'bmi', 'uvalue': None}, 'bmi_max': {'ureadonly': True, 'ufield': 'bmi', 'uvalue': None}, 'systolic_blood_pressure_min': {'ureadonly': False, 'ufield': 'systolic_blood_pressure', 'uvalue': None}, 'systolic_blood_pressure_max': {'ureadonly': False, 'ufield': 'systolic_blood_pressure', 'uvalue': None}, 'diastolic_blood_pressure_min': {'ureadonly': False, 'ufield': 'diastolic_blood_pressure', 'uvalue': None}, 'diastolic_blood_pressure_max': {'ureadonly': False, 'ufield': 'diastolic_blood_pressure', 'uvalue': None}, 'disease': {'ureadonly': False, 'ufield': 'disease', 'uvalue': 'multiple myeloma'}, 'stages': {'ureadonly': False, 'ufield': 'stage', 'uvalue': None}, 'karnofsky_performance_score_min': {'ureadonly': False, 'ufield': 'karnofsky_performance_score', 'uvalue': 100}, 'karnofsky_performance_score_max': {'ureadonly': False, 'ufield': 'karnofsky_performance_score', 'uvalue': 100}, 'ecog_performance_status_min': {'ureadonly': False, 'ufield': 'ecog_performance_status', 'uvalue': None}, 'ecog_performance_status_max': {'ureadonly': False, 'ufield': 'ecog_performance_status', 'uvalue': None}, 'no_other_active_malignancies_required': {'ureadonly': False, 'ufield': 'no_other_active_malignancies', 'uvalue': True}, 'pre_existing_conditions_excluded': {'ureadonly': False, 'ufield': 'pre_existing_condition_categories', 'uvalue': []}, 'peripheral_neuropathy_grade_min': {'ureadonly': False, 'ufield': 'peripheral_neuropathy_grade', 'uvalue': None}, 'peripheral_neuropathy_grade_max': {'ureadonly': False, 'ufield': 'peripheral_neuropathy_grade', 'uvalue': None}, 'cytogenic_markers_required': {'ureadonly': False, 'ufield': 'cytogenic_markers', 'uvalue': None}, 'cytogenic_markers_excluded': {'ureadonly': False, 'ufield': 'cytogenic_markers', 'uvalue': None}, 'molecular_markers_required': {'ureadonly': False, 'ufield': 'molecular_markers', 'uvalue': None}, 'molecular_markers_excluded': {'ureadonly': False, 'ufield': 'molecular_markers', 'uvalue': None}, 'plasma_cell_leukemia_required': {'ureadonly': False, 'ufield': 'plasma_cell_leukemia', 'uvalue': False}, 'disease_progression_active_required': {'ureadonly': False, 'ufield': 'progression', 'uvalue': None}, 'measurable_disease_imwg_required': {'ureadonly': True, 'ufield': 'measurable_disease_imwg', 'uvalue': None}, 'toxicity_grade_max': {'ureadonly': False, 'ufield': 'toxicity_grade', 'uvalue': None}, 'planned_therapies_required': {'ureadonly': False, 'ufield': 'planned_therapies', 'uvalue': None}, 'planned_therapies_excluded': {'ureadonly': False, 'ufield': 'planned_therapies', 'uvalue': None}, 'supportive_therapies_required': {'ureadonly': False, 'ufield': 'supportive_therapies', 'uvalue': []}, 'supportive_therapies_excluded': {'ureadonly': False, 'ufield': 'supportive_therapies', 'uvalue': []}, 'therapy_lines_count_min': {'ureadonly': False, 'ufield': 'prior_therapy', 'uvalue': None}, 'therapy_lines_count_max': {'ureadonly': False, 'ufield': 'prior_therapy', 'uvalue': None}, 'therapies_required': {'ureadonly': False, 'ufield': 'later_therapies', 'uvalue': []}, 'washout_period_duration': {'ureadonly': False, 'ufield': 'last_treatment', 'uvalue': None}, 'concomitant_medications_excluded': {'ureadonly': False, 'ufield': 'concomitant_medications', 'uvalue': None}, 'stem_cell_transplant_history_required': {'ureadonly': False, 'ufield': 'stem_cell_transplant_history', 'uvalue': False}, 'stem_cell_transplant_history_excluded': {'ureadonly': False, 'ufield': 'stem_cell_transplant_history', 'uvalue': []}, 'relapse_count_min': {'ureadonly': False, 'ufield': 'relapse_count', 'uvalue': None}, 'relapse_count_max': {'ureadonly': False, 'ufield': 'relapse_count', 'uvalue': None}, 'refractory_required': {'ureadonly': False, 'ufield': 'treatment_refractory_status', 'uvalue': None}, 'absolute_neutrophile_count_min': {'ureadonly': False, 'ufield': 'absolute_neutrophile_count', 'uvalue': None}, 'absolute_neutrophile_count_max': {'ureadonly': False, 'ufield': 'absolute_neutrophile_count', 'uvalue': None}, 'platelet_count_min': {'ureadonly': False, 'ufield': 'platelet_count', 'uvalue': None}, 'platelet_count_max': {'ureadonly': False, 'ufield': 'platelet_count', 'uvalue': None}, 'white_blood_cell_count_min': {'ureadonly': False, 'ufield': 'white_blood_cell_count', 'uvalue': None}, 'white_blood_cell_count_max': {'ureadonly': False, 'ufield': 'white_blood_cell_count', 'uvalue': None}, 'red_blood_cell_count_min': {'ureadonly': False, 'ufield': 'red_blood_cell_count', 'uvalue': None}, 'red_blood_cell_count_max': {'ureadonly': False, 'ufield': 'red_blood_cell_count', 'uvalue': None}, 'serum_calcium_level_min': {'ureadonly': False, 'ufield': 'serum_calcium_level', 'uvalue': None}, 'serum_calcium_level_max': {'ureadonly': False, 'ufield': 'serum_calcium_level', 'uvalue': None}, 'creatinine_clearance_rate_min': {'ureadonly': False, 'ufield': 'creatinine_clearance_rate', 'uvalue': None}, 'creatinine_clearance_rate_max': {'ureadonly': False, 'ufield': 'creatinine_clearance_rate', 'uvalue': None}, 'serum_creatinine_level_abs_min': {'ureadonly': False, 'ufield': 'serum_creatinine_level', 'uvalue': None}, 'serum_creatinine_level_abs_max': {'ureadonly': False, 'ufield': 'serum_creatinine_level', 'uvalue': None}, 'serum_creatinine_level_uln_min': {'ureadonly': True, 'ufield': 'serum_creatinine_level', 'uvalue': None}, 'serum_creatinine_level_uln_max': {'ureadonly': True, 'ufield': 'serum_creatinine_level', 'uvalue': None}, 'hemoglobin_level_min': {'ureadonly': False, 'ufield': 'hemoglobin_level', 'uvalue': None}, 'hemoglobin_level_max': {'ureadonly': False, 'ufield': 'hemoglobin_level', 'uvalue': None}, 'meets_crab': {'ureadonly': True, 'ufield': 'meets_crab', 'uvalue': None}, 'estimated_glomerular_filtration_rate_min': {'ureadonly': False, 'ufield': 'estimated_glomerular_filtration_rate', 'uvalue': None}, 'estimated_glomerular_filtration_rate_max': {'ureadonly': False, 'ufield': 'estimated_glomerular_filtration_rate', 'uvalue': None}, 'liver_enzyme_level_ast_abs_min': {'ureadonly': False, 'ufield': 'liver_enzyme_levels_ast', 'uvalue': None}, 'liver_enzyme_level_ast_abs_max': {'ureadonly': False, 'ufield': 'liver_enzyme_levels_ast', 'uvalue': None}, 'liver_enzyme_level_ast_uln_min': {'ureadonly': True, 'ufield': 'liver_enzyme_levels_ast', 'uvalue': None}, 'liver_enzyme_level_ast_uln_max': {'ureadonly': True, 'ufield': 'liver_enzyme_levels_ast', 'uvalue': None}, 'liver_enzyme_level_alt_abs_min': {'ureadonly': False, 'ufield': 'liver_enzyme_levels_alt', 'uvalue': None}, 'liver_enzyme_level_alt_abs_max': {'ureadonly': False, 'ufield': 'liver_enzyme_levels_alt', 'uvalue': None}, 'liver_enzyme_level_alt_uln_min': {'ureadonly': True, 'ufield': 'liver_enzyme_levels_alt', 'uvalue': None}, 'liver_enzyme_level_alt_uln_max': {'ureadonly': True, 'ufield': 'liver_enzyme_levels_alt', 'uvalue': None}, 'liver_enzyme_level_alp_abs_min': {'ureadonly': False, 'ufield': 'liver_enzyme_levels_alp', 'uvalue': None}, 'liver_enzyme_level_alp_abs_max': {'ureadonly': False, 'ufield': 'liver_enzyme_levels_alp', 'uvalue': None}, 'liver_enzyme_level_alp_uln_min': {'ureadonly': True, 'ufield': 'liver_enzyme_levels_alp', 'uvalue': None}, 'liver_enzyme_level_alp_uln_max': {'ureadonly': True, 'ufield': 'liver_enzyme_levels_alp', 'uvalue': None}, 'albumin_min': {'ureadonly': False, 'ufield': 'albumin_level', 'uvalue': None}, 'albumin_max': {'ureadonly': False, 'ufield': 'albumin_level', 'uvalue': None}, 'serum_bilirubin_total_level_abs_min': {'ureadonly': False, 'ufield': 'serum_bilirubin_level_total', 'uvalue': None}, 'serum_bilirubin_total_level_abs_max': {'ureadonly': False, 'ufield': 'serum_bilirubin_level_total', 'uvalue': None}, 'serum_bilirubin_total_level_uln_min': {'ureadonly': True, 'ufield': 'serum_bilirubin_level_total', 'uvalue': None}, 'serum_bilirubin_total_level_uln_max': {'ureadonly': True, 'ufield': 'serum_bilirubin_level_total', 'uvalue': None}, 'serum_bilirubin_direct_level_abs_min': {'ureadonly': False, 'ufield': 'serum_bilirubin_level_direct', 'uvalue': None}, 'serum_bilirubin_direct_level_abs_max': {'ureadonly': False, 'ufield': 'serum_bilirubin_level_direct', 'uvalue': None}, 'serum_bilirubin_direct_level_uln_min': {'ureadonly': True, 'ufield': 'serum_bilirubin_level_direct', 'uvalue': None}, 'serum_bilirubin_direct_level_uln_max': {'ureadonly': True, 'ufield': 'serum_bilirubin_level_direct', 'uvalue': None}, 'kappa_lambda_abnormal_required': {'ureadonly': True, 'ufield': 'abnormal_kappa_lambda_ratio', 'uvalue': None}, 'meets_slim': {'ureadonly': True, 'ufield': 'meets_slim', 'uvalue': None}, 'serum_monoclonal_protein_level_min': {'ureadonly': False, 'ufield': 'monoclonal_protein_serum', 'uvalue': None}, 'serum_monoclonal_protein_level_max': {'ureadonly': False, 'ufield': 'monoclonal_protein_serum', 'uvalue': None}, 'urine_monoclonal_protein_level_min': {'ureadonly': False, 'ufield': 'monoclonal_protein_urine', 'uvalue': None}, 'urine_monoclonal_protein_level_max': {'ureadonly': False, 'ufield': 'monoclonal_protein_urine', 'uvalue': None}, 'lactate_dehydrogenase_level_min': {'ureadonly': False, 'ufield': 'lactate_dehydrogenase_level', 'uvalue': None}, 'lactate_dehydrogenase_level_max': {'ureadonly': False, 'ufield': 'lactate_dehydrogenase_level', 'uvalue': None}, 'pulmonary_function_test_result_required': {'ureadonly': False, 'ufield': 'pulmonary_function_test_result', 'uvalue': False}, 'bone_imaging_result_required': {'ureadonly': False, 'ufield': 'bone_imaging_result', 'uvalue': False}, 'clonal_plasma_cells_min': {'ureadonly': False, 'ufield': 'clonal_plasma_cells', 'uvalue': None}, 'clonal_plasma_cells_max': {'ureadonly': False, 'ufield': 'clonal_plasma_cells', 'uvalue': None}, 'ejection_fraction_min': {'ureadonly': False, 'ufield': 'ejection_fraction', 'uvalue': None}, 'ejection_fraction_max': {'ureadonly': False, 'ufield': 'ejection_fraction', 'uvalue': None}, 'no_hiv_required': {'ureadonly': False, 'ufield': 'no_hiv_status', 'uvalue': True}, 'no_hepatitis_b_required': {'ureadonly': False, 'ufield': 'no_hepatitis_b_status', 'uvalue': True}, 'no_hepatitis_c_required': {'ureadonly': False, 'ufield': 'no_hepatitis_c_status', 'uvalue': True}, 'consent_capability_required': {'ureadonly': False, 'ufield': 'consent_capability', 'uvalue': True}, 'caregiver_availability_required': {'ureadonly': False, 'ufield': 'caregiver_availability_status', 'uvalue': False}, 'contraceptive_use_requirement': {'ureadonly': False, 'ufield': 'contraceptive_use', 'uvalue': False}, 'no_pregnancy_or_lactation_required': {'ureadonly': False, 'ufield': 'no_pregnancy_or_lactation_status', 'uvalue': True}, 'negative_pregnancy_test_result_required': {'ureadonly': False, 'ufield': 'pregnancy_test_result', 'uvalue': False}, 'no_mental_health_disorder_required': {'ureadonly': False, 'ufield': 'no_mental_health_disorder_status', 'uvalue': True}, 'no_concomitant_medication_required': {'ureadonly': False, 'ufield': 'no_concomitant_medication_status', 'uvalue': True}, 'no_tobacco_use_required': {'ureadonly': False, 'ufield': 'no_tobacco_use_status', 'uvalue': True}, 'no_substance_use_required': {'ureadonly': False, 'ufield': 'no_substance_use_status', 'uvalue': True}, 'no_geographic_exposure_risk_required': {'ureadonly': False, 'ufield': 'no_geographic_exposure_risk', 'uvalue': True}, 'no_active_infection_required': {'ureadonly': False, 'ufield': 'no_active_infection_status', 'uvalue': True}, 'renal_adequacy_required': {'ureadonly': True, 'ufield': 'renal_adequacy_status', 'uvalue': False}, 'hepatic_adequacy_required': {'ureadonly': True, 'ufield': 'hepatic_adequacy_status', 'uvalue': False}, 'haematological_adequacy_required': {'ureadonly': True, 'ufield': 'haematological_adequacy_status', 'uvalue': False}}

    @pytest.mark.django_db
    def test_is_blank(self, patient_info):
        trial = TrialFactory()

        patient_info.gender = 'M'

        assert TrialAttributes(trial=trial, patient_info=patient_info).is_blank('stages', [], None) is True
        assert TrialAttributes(trial=trial, patient_info=patient_info).is_blank('negativePregnancyTestResultRequired', True, None) is True
        assert TrialAttributes(trial=trial, patient_info=patient_info).is_blank('noPregnancyOrLactationRequired', True, None) is True

        patient_info.gender = ''

        assert TrialAttributes(trial=trial, patient_info=patient_info).is_blank('negativePregnancyTestResultRequired', True, None) is False
        assert TrialAttributes(trial=trial, patient_info=patient_info).is_blank('noPregnancyOrLactationRequired', True, None) is False


class TestMclTrialAttributes:
    """Verify the MCL disease branch in __init__ aliases therapy / stage option
    lists to the MCL-specific keys, mirroring the CLL pattern (#73).
    """

    @pytest.mark.django_db
    def test_mcl_trial_aliases_therapy_options_to_mcl_keys(self):
        patient_info = PatientInfo(disease='mantle cell lymphoma')
        trial = TrialFactory(disease='mantle cell lymphoma')

        attrs = TrialAttributes(trial, patient_info=patient_info)

        # Each canonical key the UI consumes for trial detail rendering must
        # point at the MCL-specific options list, not the union or any other
        # disease.
        for canonical, mcl_key in [
            ('firstLineTherapy', 'therapiesFirstLineMcl'),
            ('secondLineTherapy', 'therapiesSecondLineMcl'),
            ('laterTherapy', 'therapiesLaterLineMcl'),
            ('supportiveTherapiesRequired', 'supportiveTherapiesMcl'),
            ('supportiveTherapiesExcluded', 'supportiveTherapiesMcl'),
            ('therapiesRequired', 'therapiesMcl'),
            ('therapiesExcluded', 'therapiesMcl'),
            ('plannedTherapies', 'plannedTherapiesMcl'),
            ('plannedTherapiesRequired', 'plannedTherapiesMcl'),
            ('plannedTherapiesExcluded', 'plannedTherapiesMcl'),
            ('stage', 'stagesMcl'),
            ('stages', 'stagesMcl'),
        ]:
            assert attrs.all_options[canonical] is attrs.all_options[mcl_key], \
                f'{canonical} should point at {mcl_key} for an MCL trial'

    @pytest.mark.django_db
    def test_non_mcl_trial_does_not_get_mcl_aliases(self):
        # A CLL trial must continue to alias the canonical keys to CLL options,
        # not MCL.
        patient_info = PatientInfo(disease='chronic lymphocytic leukemia')
        trial = TrialFactory(disease='chronic lymphocytic leukemia')

        attrs = TrialAttributes(trial, patient_info=patient_info)

        assert attrs.all_options['firstLineTherapy'] is attrs.all_options['therapiesFirstLineCll']
        assert attrs.all_options['stages'] is attrs.all_options['stagesCll']
        # CLL options must NOT be wired to MCL options. Identity check, not
        # equality: both keys may resolve to a structurally identical
        # `Unknown/Other`-only placeholder in the test DB (no MCL or CLL
        # therapy-round seed data), so dict equality is meaningless.
        assert attrs.all_options['therapiesFirstLineCll'] is not attrs.all_options['therapiesFirstLineMcl']

    @pytest.mark.django_db
    def test_mcl_patient_outcome_aliases_to_mcl_subset(self):
        """Regression for #83 second leg: MCL patients must see Cheson/Lugano
        4-value outcome options on firstLineOutcome / secondLineOutcome /
        laterOutcome. The patient disease drives _therapy_outcome_options_key.
        Without 'mantle cell lymphoma' in _DISEASE_TO_OUTCOME_KEY, MCL fell
        through to the union 'therapyOutcome' (7-value IMWG enum), leaking
        sCR / VGPR / MRD codes that don't apply clinically.
        """
        patient_info = PatientInfo(disease='mantle cell lymphoma')
        trial = TrialFactory(disease='mantle cell lymphoma')

        attrs = TrialAttributes(trial, patient_info=patient_info)

        # Each outcome key must alias to therapyOutcomeMcl, NOT the union.
        for canonical in ('firstLineOutcome', 'secondLineOutcome', 'laterOutcome'):
            assert attrs.all_options[canonical] is attrs.all_options['therapyOutcomeMcl'], \
                f'{canonical} should point at therapyOutcomeMcl for an MCL patient'
            assert attrs.all_options[canonical] is not attrs.all_options['therapyOutcome'], \
                f'{canonical} must not leak the union 7-value enum for MCL'

        # Sanity: the MCL outcome set is the 4-value Cheson/Lugano subset.
        codes = {opt['value'] for opt in attrs.all_options['firstLineOutcome']['options']}
        assert codes == {'CR', 'PR', 'SD', 'PD'}
