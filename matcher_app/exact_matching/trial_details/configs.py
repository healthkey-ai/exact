from exact_matching.enums import PriorTherapyLines
from exact_matching.attribute_names import AttributeNames
from exact_matching.therapies_mapper import *

PHASE_CODE_MAPPING = ['EARLY_PHASE1', 'PHASE1', 'PHASE2', 'PHASE3', 'PHASE4']

ATTR_FALSE_VALUE_IS_BLANK = [
    'noOtherActiveMalignanciesRequired',
    'noMentalHealthDisorderRequired',
    'noConcomitantMedicationRequired',
    'noTobaccoUseRequired',
    'noSubstanceUseRequired',
    'noGeographicExposureRiskRequired',
    'noPregnancyOrLactationRequired',
    'noActiveInfectionRequired',
    'pulmonaryFunctionTestResultRequired',
    'boneImagingResultRequired',
    'consentCapabilityRequired',
    'caregiverAvailabilityRequired',
    'contraceptiveUseRequirement',
    'negativePregnancyTestResultRequired',
    'noHivRequired',
    'noHepatitisBRequired',
    'noHepatitisCRequired',
    'noPlasmaCellLeukemiaRequired',
    'plasmaCellLeukemiaRequired',
    'renalAdequacyRequired',
    'hepaticAdequacyRequired',
    'haematologicalAdequacyRequired',
    'abnormalKappaLambdaRatio',
    'kappaLambdaAbnormalRequired',
    'meetsCRAB',
    'meetsSLIM',
    'meetsLugano',
    'meetsGELF',
    'pulmonaryFunctionTestResultRequired',
    'boneImagingResultRequired',
    'noGeographicExposureRiskRequired',
    'boneOnlyMetastasisRequired',
    'measurableDiseaseByRecistRequired',
    'measurableDiseaseImwgRequired',
    'meetsMeasOrBoneRequired',
    'tnbcStatus',
    'refractoryRequired',
    'notRefractoryRequired',
    'tp53DisruptionRequired',
    'tp53DisruptionExcluded',
    'btkInhibitorRefractoryRequired',
    'btkInhibitorRefractoryExcluded',
    'bcl2InhibitorRefractoryRequired',
    'bcl2InhibitorRefractoryExcluded',
    'measurableDiseaseIwcllRequired',
    'hepatomegalyRequired',
    'autoimmuneCytopeniasRefractoryToSteroidsRequired',
    'lymphadenopathyRequired',
    'splenomegalyRequired',
    'boneMarrowInvolvementRequired',
]

ATTR_SKIP_FOR_MALE = [
    'negativePregnancyTestResultRequired',
    'noPregnancyOrLactationRequired'
]

ATTR_LABEL_MAPPING = {
    'kappaFLC': 'Kappa FLC',
    'lambdaFLC': 'Lambda FLC',
    'meetsCRAB': 'Meets CRAB Criteria',
    'meetsSLIM': 'Meets SLiM Criteria',
    'meetsLugano': 'Meets Lugano Criteria',
    'meetsGELF': 'Meets GELF Criteria',
    'lymphocyteDoublingTimeMin': 'Lymphocyte Doubling Time (LDT) Minimum',
    'lymphocyteDoublingTimeMax': 'Lymphocyte Doubling Time (LDT) Maximum',
    'measurableDiseaseIwcllRequired': 'Measurable Disease (iwCLL Criteria)',
    'autoimmuneCytopeniasRefractoryToSteroidsRequired': 'Autoimmune Cytopenias Refractory to Steroids',
    'largestLymphNodeSizeMin': 'Largest Lymph Node Size Minimum (cm)',
    'spleenSizeMin': 'Spleen Size Minimum (cm below costal margin)',
    'diseaseActivitiesRequired': 'Disease Activity',
    'btkInhibitorRefractoryRequired': 'BTK Inhibitor Refractory',
    'btkInhibitorRefractoryExcluded': 'BTK Inhibitor Refractory Excluded',
    'bcl2InhibitorRefractoryRequired': 'BCL2 Inhibitor Refractory',
    'bcl2InhibitorRefractoryExcluded': 'BCL2 Inhibitor Refractory Excluded',
    'absoluteLymphocyteCountMin': 'Absolute Lymphocyte Count (ALC) Minimum',
    'absoluteLymphocyteCountMax': 'Absolute Lymphocyte Count (ALC) Maximum',
    'qtcfValueMax': 'QTcF Value Maximum',
    'serumBeta2MicroglobulinLevelMin': 'Serum β2-microglobulin Level Minimum',
    'serumBeta2MicroglobulinLevelMax': 'Serum β2-microglobulin Level Maximum',
    'clonalBoneMarrowBLymphocytesMin': 'Clonal Bone Marrow B-Lymphocytes (%) Minimum',
    'clonalBoneMarrowBLymphocytesMax': 'Clonal Bone Marrow B-Lymphocytes (%) Maximum',
    'clonalBLymphocyteCountMin': 'Clonal B-Lymphocyte Count Minimum',
    'clonalBLymphocyteCountMax': 'Clonal B-Lymphocyte Count Maximum',
    'tp53DisruptionRequired': 'TP53 Disruption Required',
    'tp53DisruptionExcluded': 'TP53 Disruption Excluded',
    'hepatomegalyRequired': 'Hepatomegaly Required',
    'lymphadenopathyRequired': 'Lymphadenopathy Required',
    'splenomegalyRequired': 'Splenomegaly Required',
    'boneMarrowInvolvementRequired': 'Bone Marrow Involvement Required',
}

ATTR_DEPS_MAPPING = {
    # Computed in PatientInfo.save() via PatientInfoAttributes
    'bmi': ['weight', 'height'],
    'meetsCRAB': ['serumCalciumLevel', 'creatinineClearanceRate', 'serumCreatinineLevel', 'hemoglobinLevel', 'boneLesions'],
    'meetsSLIM': ['clonalPlasmaCells', 'kappaFLC', 'lambdaFLC', 'boneLesions'],
    'progression': ['meetsCRAB', 'meetsSLIM'],
    'stemCellTransplantHistory': ['priorTherapy', 'laterTherapies', 'laterTherapy', 'secondLineTherapy', 'firstLineTherapy'],
    'estimatedGlomerularFiltrationRate': ['serumCreatinineLevel', 'patientAge', 'gender'],
    'renalAdequacyStatus': ['estimatedGlomerularFiltrationRate', 'creatinineClearanceRate'],
    'hepaticAdequacyStatus': ['liverEnzymeLevelsAst', 'liverEnzymeLevelsAlt', 'serumBilirubinLevelTotal', 'ethnicity', 'gender'],
    'haematologicalAdequacyStatus': ['absoluteNeutrophileCount', 'plateletCount', 'hemoglobinLevel'],
    'hrStatus': ['estrogenReceptorStatus', 'progesteroneReceptorStatus'],
    'tnbcStatus': ['estrogenReceptorStatus', 'progesteroneReceptorStatus', 'her2Status'],
    'tp53Disruption': ['cytogenicMarkers', 'molecularMarkers'],
    'treatmentRefractoryStatus': ['priorTherapy', 'firstLineOutcome', 'secondLineOutcome', 'laterOutcome'],

    # Computed in pre_save signal
    'flipiScore': ['flipiScoreOptions'],
    'measurableDiseaseImwg': ['monoclonalProteinSerum', 'monoclonalProteinUrine', 'kappaFLC', 'lambdaFLC'],
    'lastTreatment': ['laterDate', 'secondLineDate', 'firstLineDate'],
    'geoPoint': ['country', 'postalCode', 'longitude', 'latitude'],

    # @property on PatientInfo (not stored, computed on access)
    'abnormalKappaLambdaRatio': ['kappaFLC', 'lambdaFLC'],
    'meetsMeasOrBoneStatus': ['measurableDiseaseByRecistStatus', 'boneOnlyMetastasisStatus'],
}

ATTR_TYPE_MAPPING = {
    'plannedTherapiesRequired': 'multiselect',
    'plannedTherapiesExcluded': 'multiselect',
    'plannedTherapies': 'multiselect',
    'languagesSkills': 'multiselect',
    'languagesSkillsRequired': 'multiselect',
    'gender': 'select',
    'ethnicityRequired': 'multiselect',
    'disease': 'select',
    'tumorGrade': 'string',
    'tumorGradeMin': 'string',
    'tumorGradeMax': 'string',
    'cytogenicMarkersRequired': 'multiselect',
    'cytogenicMarkersExcluded': 'multiselect',
    'molecularMarkersRequired': 'multiselect',
    'molecularMarkersExcluded': 'multiselect',
    'therapiesRequired': 'multiselect',
    'therapiesExcluded': 'multiselect',
    'therapyComponentsRequired': 'multiselect',
    'therapyComponentsExcluded': 'multiselect',
    'therapyTypesRequired': 'multiselect',
    'therapyTypesExcluded': 'multiselect',
    'preExistingConditionCategories': 'multiselect',
    'preExistingConditionsExcluded': 'multiselect',
    'stemCellTransplantHistoryExcluded': 'multiselect',
    'stages': 'multiselect',
    'histologicTypesRequired': 'multiselect',
    'estrogenReceptorStatusesRequired': 'multiselect',
    'progesteroneReceptorStatusesRequired': 'multiselect',
    'her2StatusesRequired': 'multiselect',
    'hrdStatusesRequired': 'multiselect',
    'hrStatusesRequired': 'multiselect',
    'tumorStagesRequired': 'multiselect',
    'tumorStagesExcluded': 'multiselect',
    'nodesStagesRequired': 'multiselect',
    'nodesStagesExcluded': 'multiselect',
    'distantMetastasisStagesRequired': 'multiselect',
    'distantMetastasisStagesExcluded': 'multiselect',
    'stagingModalitiesRequired': 'multiselect',
    'binetStagesRequired': 'multiselect',
    'proteinExpressionsRequired': 'multiselect',
    'proteinExpressionsExcluded': 'multiselect',
    'richterTransformationsRequired': 'multiselect',
    'richterTransformationsExcluded': 'multiselect',
    'tumorBurdensRequired': 'multiselect',
    'diseaseActivitiesRequired': 'multiselect',

    'mutationGenesRequired': 'multiselect',
    'mutationVariantsRequired': 'multiselect',
    'mutationOriginsRequired': 'multiselect',
    'mutationInterpretationsRequired': 'multiselect',

    'ldtumorGradeMin': 'select',
    'ldtumorGradeMax': 'select',

    'ustage': 'select',
    'uflipiScoreOptions': 'multiselect',
    'upriorTherapy': 'select',
    'uecogPerformanceStatus': 'select',
    'ukarnofskyPerformanceScore': 'select',
    'utumorGrade': 'select',
    'utumorGradeMin': 'select',
    'utumorGradeMax': 'select',
    'ufirstLineTherapy': 'select',
    'usecondLineTherapy': 'select',
    'ulaterTherapy': 'select',
    'uprogression': 'select',
    'utreatmentRefractoryStatus': 'select',
    'ustemCellTransplantHistory': 'select',
    'ulastTreatment': 'string',
    'upreExistingConditionCategories': 'multiselect',
    'ulanguagesSkills': 'multiselect',
    'ustagingModalities': 'multiselect',
    'uplannedTherapies': 'multiselect',
}


def get_treatment_attrs(patient_info):
    prior_therapy = patient_info.prior_therapy

    if prior_therapy is None or str(prior_therapy) == 'None':
        return ['prior_therapy']
    elif prior_therapy == 'One line':
        return [
            'prior_therapy',
            'first_line_therapy', 'first_line_date', 'first_line_outcome',
            'supportive_therapies'
        ]
    elif prior_therapy == 'Two lines':
        return [
            'prior_therapy',
            'first_line_therapy', 'first_line_date', 'first_line_outcome',
            'second_line_therapy', 'second_line_date', 'second_line_outcome',
            'supportive_therapies'
        ]
    else:
        return [
            'prior_therapy',
            'first_line_therapy', 'first_line_date', 'first_line_outcome',
            'second_line_therapy', 'second_line_date', 'second_line_outcome',
            'later_therapy', 'later_therapies', 'later_date', 'later_outcome',
            'supportive_therapies'
        ]


SUBFORM_ATTRS_MAPPING = {
    'mutation_genes_required': ['genetic_mutations'],
    'mutation_variants_required': ['genetic_mutations'],
    'mutation_origins_required': ['genetic_mutations'],
    'mutation_interpretations_required': ['genetic_mutations'],
    'bmi': ['weight', 'height'],
    'bmi_min': ['weight', 'height'],
    'bmi_max': ['weight', 'height'],
    'washout_period_duration': get_treatment_attrs,
    'therapies_required': get_treatment_attrs,
    'therapies_excluded': get_treatment_attrs,
    'therapy_types_required': get_treatment_attrs,
    'therapy_types_excluded': get_treatment_attrs,
    'therapy_components_required': get_treatment_attrs,
    'therapy_components_excluded': get_treatment_attrs,
    'serum_creatinine_level_uln_min': ['serum_creatinine_level', 'gender', 'ethnicity'],
    'serum_creatinine_level_uln_max': ['serum_creatinine_level', 'gender', 'ethnicity'],
    'liver_enzyme_level_ast_uln_min': ['liver_enzyme_levels_ast', 'gender', 'ethnicity'],
    'liver_enzyme_level_ast_uln_max': ['liver_enzyme_levels_ast', 'gender', 'ethnicity'],
    'liver_enzyme_level_alt_uln_min': ['liver_enzyme_levels_alt', 'gender', 'ethnicity'],
    'liver_enzyme_level_alt_uln_max': ['liver_enzyme_levels_alt', 'gender', 'ethnicity'],
    'liver_enzyme_level_alp_uln_min': ['liver_enzyme_levels_alp', 'gender', 'ethnicity', 'patient_age'],
    'liver_enzyme_level_alp_uln_max': ['liver_enzyme_levels_alp', 'gender', 'ethnicity', 'patient_age'],
    'serum_bilirubin_total_level_uln_min': ['serum_bilirubin_level_total', 'ethnicity'],
    'serum_bilirubin_total_level_uln_max': ['serum_bilirubin_level_total', 'ethnicity'],
    'serum_bilirubin_direct_level_uln_min': ['serum_bilirubin_level_direct', 'ethnicity'],
    'serum_bilirubin_direct_level_uln_max': ['serum_bilirubin_level_direct', 'ethnicity'],
    'abnormal_kappa_lambda_ratio': ['kappa_flc', 'lambda_flc'],
    'kappa_lambda_abnormal_required': ['kappa_flc', 'lambda_flc'],
    'meets_meas_or_bone_status': ['measurable_disease_by_recist_status', 'bone_only_metastasis_status'],
    'meets_meas_or_bone_required': ['measurable_disease_by_recist_status', 'bone_only_metastasis_status'],
    'meets_crab': ['serum_calcium_level', 'creatinine_clearance_rate', 'serum_creatinine_level', 'hemoglobin_level', 'bone_lesions'],
    'meets_slim': ['clonal_plasma_cells', 'kappa_flc', 'lambda_flc', 'bone_lesions'],
    'hr_statuses_required': ['estrogen_receptor_status', 'progesterone_receptor_status'],
    'tnbc_status': ['estrogen_receptor_status', 'progesterone_receptor_status', 'her2_status'],
    'measurable_disease_imwg': ['monoclonal_protein_serum', 'monoclonal_protein_urine', 'kappa_flc', 'lambda_flc'],
}

ATTR_OPTIONS_MAPPING = {
}

ATTR_NAME_TO_SKIP = [
    'id',
    'has_stages',
    'prior_therapy_text',
    'no_prior_therapy_text',
    'cytogenic_markers_all',
    'created_at',
    'updated_at'
]

GROUP_NAMES = {
    'disease': 'Disease',
    'treatment': 'Treatment',
    'blood': 'Blood',
    'labs': 'Labs',
    'behavior': 'Behavior',
    'other': 'Other',
    'admin': 'Admin',
}

ATTR_GROUP_MAPPING = {
    # 'trialId': 'general',
    'studyId': 'general',
    'register': 'general',
    'briefTitle': 'general',
    'officialTitle': 'general',
    'briefSummary': 'general',
    'enrollmentCount': 'general',
    'participationCriteria': 'general',
    'locationsName': 'general',
    'interventionTreatments': 'general',
    'sponsorName': 'general',
    'researchers': 'general',
    'contactEmail': 'general',
    'link': 'general',
    'distance': 'general',
    'submittedDate': 'general',
    'postedDate': 'general',
    'lastUpdateDate': 'general',
    'firstEnrolmentDate': 'general',
    'targetSampleSize': 'general',
    'recruitmentStatus': 'general',
    'studyType': 'general',
    'trialType': 'general',
    'studyDesign': 'general',
    'phases': 'general',
    'patientBurdenScore': ['general', 'admin'],
    'riskScore': ['general', 'admin'],
    'benefitScore': ['general', 'admin'],
    'goodnessScore': 'general',
    'distancePenalty': 'general',

    'isValidated': 'ignore',
    'isLabeled': 'ignore',

    'matchScore': ['general'],
    'disease': ['general', 'disease'],

    # == Disease group ==
    # + Patient Age
    # + Gender
    # + Weight
    # + Height (BMI)
    # + Ethnicity
    # + Blood Pressure (SBP / DBP)
    # - Location
    # + Disease
    # + Stage
    # + Karnofsky Performance Score
    # + ECOG Performance Status
    # + No Other Active Malignancies
    # + Peripheral Neuropathy Grade
    # === MM ===
    # + Cytogenic Markers
    # + Molecular Markers
    # + Plasma Cell Leukemia
    # + Progression
    # === FL ===
    # + GELF Criteria Status
    # + FLIPI Score: 3
    # + Tumor Grade

    'ageMin': 'disease',
    'ageMax': 'disease',
    'gender': 'disease',
    'ethnicityRequired': 'disease',
    'weightMin': 'disease',
    'weightMax': 'disease',
    'bmiMin': 'disease',
    'bmiMax': 'disease',
    'systolicBloodPressureMin': 'disease',
    'systolicBloodPressureMax': 'disease',
    'diastolicBloodPressureMin': 'disease',
    'diastolicBloodPressureMax': 'disease',
    # 'disease': ['disease', 'general'],
    'stages': 'disease',
    'karnofskyPerformanceScoreMin': 'disease',
    'karnofskyPerformanceScoreMax': 'disease',
    'ecogPerformanceStatusMin': 'disease',
    'ecogPerformanceStatusMax': 'disease',
    'noOtherActiveMalignanciesRequired': 'disease',
    'peripheralNeuropathyGradeMin': 'disease',
    'peripheralNeuropathyGradeMax': 'disease',
    'noPlasmaCellLeukemiaRequired': 'disease',
    'plasmaCellLeukemiaRequired': 'disease',
    'preExistingConditionsExcluded': 'disease',
    'noActiveInfectionRequired': 'disease',
    'tumorStagesRequired': 'disease',
    'tumorStagesExcluded': 'disease',
    'nodesStagesRequired': 'disease',
    'nodesStagesExcluded': 'disease',
    'distantMetastasisStagesRequired': 'disease',
    'distantMetastasisStagesExcluded': 'disease',
    'stagingModalitiesRequired': 'disease',
    'molecularMarkersRequired': 'disease',
    'molecularMarkersExcluded': 'disease',
    'diseaseProgressionActiveRequired': 'disease',
    'diseaseProgressionSmolderingRequired': 'disease',
    'measurableDiseaseImwgRequired': 'disease',
    'meetsLugano': 'disease',
    'meetsGELF': 'disease',
    'flipiScoreMin': 'disease',
    'flipiScoreMax': 'disease',
    'tumorGradeMin': 'disease',
    'tumorGradeMax': 'disease',

    'menopausalStatus': 'disease',
    'toxicityGradeMax': 'disease',

    'histologicTypesRequired': 'disease',
    'biopsyGradeMin': 'disease',
    'biopsyGradeMax': 'disease',
    'measurableDiseaseByRecistRequired': 'disease',
    'boneOnlyMetastasisRequired': 'disease',
    'meetsMeasOrBoneRequired': 'disease',
    'estrogenReceptorStatusesRequired': 'disease',
    'progesteroneReceptorStatusesRequired': 'disease',
    'her2StatusesRequired': 'disease',
    'hrdStatusesRequired': 'disease',
    'hrStatusesRequired': 'disease',
    'tnbcStatus': 'disease',

    'cytogenicMarkersRequired': 'disease',
    'cytogenicMarkersExcluded': 'disease',

    'mutationGenesRequired': 'disease',
    'mutationVariantsRequired': 'disease',
    'mutationOriginsRequired': 'disease',
    'mutationInterpretationsRequired': 'disease',

    'pdL1TumorCelsMin': 'disease',
    'pdL1TumorCelsMax': 'disease',
    'pdL1Assay': 'disease',
    'pdL1IcPercentageMin': 'disease',
    'pdL1IcPercentageMax': 'disease',
    'pdL1CombinedPositiveScoreMin': 'disease',
    'pdL1CombinedPositiveScoreMax': 'disease',

    'ki67ProliferationIndexMin': 'disease',
    'ki67ProliferationIndexMax': 'disease',

    'lymphocyteDoublingTimeMin': 'disease',
    'lymphocyteDoublingTimeMax': 'disease',
    'binetStagesRequired': 'disease',
    'diseaseActivitiesRequired': 'disease',
    'proteinExpressionsRequired': 'disease',
    'proteinExpressionsExcluded': 'disease',
    'tp53DisruptionRequired': 'disease',
    'tp53DisruptionExcluded': 'disease',
    'richterTransformationsRequired': 'disease',
    'richterTransformationsExcluded': 'disease',
    'measurableDiseaseIwcllRequired': 'disease',
    'tumorBurdensRequired': 'disease',
    'lymphadenopathyRequired': 'disease',
    'largestLymphNodeSizeMin': 'disease',
    'splenomegalyRequired': 'disease',
    'spleenSizeMin': 'disease',
    'hepatomegalyRequired': 'disease',
    'autoimmuneCytopeniasRefractoryToSteroidsRequired': 'disease',

    # == Treatment group ==
    # + Prior Therapy
    # - First Line Therapy
    # - Outcome
    # - Later Therapy
    # - Outcome
    # - Last Treatment
    # + Relapse Count
    # + Refractory Status

    'therapyLinesCountMin': 'treatment',
    'therapyLinesCountMax': 'treatment',
    'washoutPeriodDuration': 'treatment',
    'remissionDurationMin': 'treatment',
    'relapseCountMin': 'treatment',
    'relapseCountMax': 'treatment',
    'refractoryRequired': 'treatment',
    'notRefractoryRequired': 'treatment',

    'therapiesRequired': 'treatment',
    'therapiesExcluded': 'treatment',
    'therapyTypesRequired': 'treatment',
    'therapyTypesExcluded': 'treatment',
    'therapyComponentsRequired': 'treatment',
    'therapyComponentsExcluded': 'treatment',
    'plannedTherapiesRequired': 'treatment',
    'plannedTherapiesExcluded': 'treatment',
    'stemCellTransplantHistoryRequired': 'treatment',
    'daySinceStemCellTransplantRequired': 'treatment',
    'stemCellTransplantHistoryExcluded': 'treatment',
    'btkInhibitorRefractoryRequired': 'treatment',
    'btkInhibitorRefractoryExcluded': 'treatment',
    'bcl2InhibitorRefractoryRequired': 'treatment',
    'bcl2InhibitorRefractoryExcluded': 'treatment',

    # == Blood group ==
    # + ANC
    # + Platelet
    # + White Blood Cells
    # + Serum Calcium Level
    # + Creatinine Clearance Rate
    # + Serum Creatinine
    # + Hemoglobin
    # + Bone Lesions
    # + Meets CRAB
    # + Glomerular Filtration Rate
    # + Liver Enzyme AST
    # + Liver Enzyme ALT
    # + Serum Bilirubin Total
    # + Serum Bilirubin Direct
    # + Clonal Bone Marrow Plasma Cells Percentage
    # + Kappa FLC
    # + Lambda FLC
    # + Meets SLIM

    'absoluteNeutrophileCountMin': 'blood',
    'absoluteNeutrophileCountMax': 'blood',
    'plateletCountMin': 'blood',
    'plateletCountMax': 'blood',
    'whiteBloodCellCountMin': 'blood',
    'whiteBloodCellCountMax': 'blood',
    'redBloodCellCountMin': 'blood',
    'redBloodCellCountMax': 'blood',
    'serumCalciumLevelMin': 'blood',
    'serumCalciumLevelMax': 'blood',
    'creatinineClearanceRateMin': 'blood',
    'creatinineClearanceRateMax': 'blood',
    'serumCreatinineLevelAbsMin': 'blood',
    'serumCreatinineLevelAbsMax': 'blood',
    'serumCreatinineLevelUlnMin': 'blood',
    'serumCreatinineLevelUlnMax': 'blood',
    'hemoglobinLevelMin': 'blood',
    'hemoglobinLevelMax': 'blood',
    'boneLesionsMin': 'blood',
    'estimatedGlomerularFiltrationRateMin': 'blood',
    'estimatedGlomerularFiltrationRateMax': 'blood',
    'renalAdequacyRequired': 'blood',
    'hepaticAdequacyRequired': 'blood',
    'haematologicalAdequacyRequired': 'blood',
    'liverEnzymeLevelAstAbsMin': 'blood',
    'liverEnzymeLevelAstAbsMax': 'blood',
    'liverEnzymeLevelAstUlnMin': 'blood',
    'liverEnzymeLevelAstUlnMax': 'blood',
    'liverEnzymeLevelAltAbsMin': 'blood',
    'liverEnzymeLevelAltAbsMax': 'blood',
    'liverEnzymeLevelAltUlnMin': 'blood',
    'liverEnzymeLevelAltUlnMax': 'blood',
    'liverEnzymeLevelAlpAbsMin': 'blood',
    'liverEnzymeLevelAlpAbsMax': 'blood',
    'liverEnzymeLevelAlpUlnMin': 'blood',
    'liverEnzymeLevelAlpUlnMax': 'blood',
    'albuminMin': 'blood',
    'albuminMax': 'blood',
    'serumBilirubinTotalLevelAbsMin': 'blood',
    'serumBilirubinTotalLevelAbsMax': 'blood',
    'serumBilirubinTotalLevelUlnMin': 'blood',
    'serumBilirubinTotalLevelUlnMax': 'blood',
    'serumBilirubinDirectLevelAbsMin': 'blood',
    'serumBilirubinDirectLevelAbsMax': 'blood',
    'serumBilirubinDirectLevelUlnMin': 'blood',
    'serumBilirubinDirectLevelUlnMax': 'blood',
    'kappaFLC': 'blood',
    'lambdaFLC': 'blood',
    'kappaLambdaAbnormalRequired': 'blood',
    'meetsCRAB': 'blood',
    'meetsSLIM': 'blood',
    'absoluteLymphocyteCountMin': 'blood',
    'absoluteLymphocyteCountMax': 'blood',

    # == Labs group ==
    # + Monoclonal Protein Serum
    # + Monoclonal Protein Urine
    # + Lactate Dehydrogenase Level
    # + Pulmonary Function Test
    # + Bone Imaging Result
    # + Clonal Plasma Cells
    # + Ejection Fraction

    'serumMonoclonalProteinLevelMin': 'labs',
    'serumMonoclonalProteinLevelMax': 'labs',
    'urineMonoclonalProteinLevelMin': 'labs',
    'urineMonoclonalProteinLevelMax': 'labs',
    'lactateDehydrogenaseLevelMin': 'labs',
    'lactateDehydrogenaseLevelMax': 'labs',
    'ldhUnits': 'labs',
    'pulmonaryFunctionTestResultRequired': 'labs',
    'boneImagingResultRequired': 'labs',
    'clonalPlasmaCellsMin': 'labs',
    'clonalPlasmaCellsMax': 'labs',
    'ejectionFractionMin': 'labs',
    'ejectionFractionMax': 'labs',
    'noHivRequired': 'labs',
    'noHepatitisBRequired': 'labs',
    'noHepatitisCRequired': 'labs',
    'qtcfValueMax': 'labs',
    'serumBeta2MicroglobulinLevelMin': 'labs',
    'serumBeta2MicroglobulinLevelMax': 'labs',
    'clonalBoneMarrowBLymphocytesMin': 'labs',
    'clonalBoneMarrowBLymphocytesMax': 'labs',
    'clonalBLymphocyteCountMin': 'labs',
    'clonalBLymphocyteCountMax': 'labs',
    'boneMarrowInvolvementRequired': 'labs',

    # == Behavior group ==
    # + I have ability to consent
    # + I have availability of a caregiver
    # + I am using contraceptive
    # + I am not pregnant or lactating
    # + I have a pregnancy test result
    # + I have no mental health disorders
    # + I'm not taking concomitant medication
    # + I'm not using tobacco
    # + I'm not using non-prescription drugs recreationally
    # + I have no geographic (occupational, environmental, infectious disease) exposure risk

    'consentCapabilityRequired': 'behavior',
    'noTobaccoUseRequired': 'behavior',
    'noSubstanceUseRequired': 'behavior',
    'negativePregnancyTestResultRequired': 'behavior',
    'noPregnancyOrLactationRequired': 'behavior',
    'contraceptiveUseRequirement': 'behavior',
    'noGeographicExposureRiskRequired': 'behavior',
    'noMentalHealthDisorderRequired': 'behavior',
    'noConcomitantMedicationRequired': 'behavior',
    'caregiverAvailabilityRequired': 'behavior',
    'languagesSkillsRequired': 'behavior',

    # == Other/Unknown group ==

    # 'heartRateMin': 'other',
    # 'heartRateMax': 'other',

    # 'noConcomitantIllnessRequired': 'other',

    # == Admin group ==
    'laySummary': ['general', 'admin'],

    'validatedAt': 'ignore',
    'validatedBy': 'ignore',
    'labeledAt': 'ignore',
    'labeledBy': 'ignore',
    'location': 'ignore',
    'interventionTreatmentsText': 'ignore',
    'phaseCodeMin': 'ignore',
    'conditionCodeIcd10': 'ignore',
    'conditionCodeSnomedCt': 'ignore',
}

ATTR_NAME_ALWAYS_INCLUDED = [AttributeNames.get_by_camel_case(k) for k, v in ATTR_GROUP_MAPPING.items() if
                             'general' in v] + ['disease', 'goodness_score']


def options_from_enum_class(cls):
    data = {x[0]: x[1] for x in cls.choices}
    return options_from_enum_dict(data)


def options_from_enum_dict(data: dict):
    return data


def patient_info_attr_units_for(attr, patient_info):
    from trials.services.patient_info.patient_info import PatientInfo
    from trials.models import WeightUnits, HeightUnits, PlateletCountUnits, \
        SerumCalciumUnits, SerumCreatinineUnits, HemoglobinUnits, SerumBilirubinUnits, AlbuminUnits

    if attr == 'weight':
        return {
            'options': options_from_enum_class(WeightUnits),
            'default': PatientInfo._meta.get_field('weight_units').default,
            'uvalue': patient_info.weight_units,
        }
    elif attr == 'height':
        return {
            'options': options_from_enum_class(HeightUnits),
            'default': PatientInfo._meta.get_field('height_units').default,
            'uvalue': patient_info.height_units,
        }
    elif attr == 'absolute_neutrophile_count':
        return {
            'options': options_from_enum_class(PlateletCountUnits),
            'default': PatientInfo._meta.get_field('absolute_neutrophile_count_units').default,
            'uvalue': patient_info.absolute_neutrophile_count_units,
        }
    elif attr == 'platelet_count':
        return {
            'options': options_from_enum_class(PlateletCountUnits),
            'default': PatientInfo._meta.get_field('platelet_count_units').default,
            'uvalue': patient_info.platelet_count_units,
        }
    elif attr == 'white_blood_cell_count':
        return {
            'options': options_from_enum_class(PlateletCountUnits),
            'default': PatientInfo._meta.get_field('white_blood_cell_count_units').default,
            'uvalue': patient_info.white_blood_cell_count_units,
        }
    elif attr == 'red_blood_cell_count':
        return {
            'options': options_from_enum_class(PlateletCountUnits),
            'default': PatientInfo._meta.get_field('red_blood_cell_count_units').default,
            'uvalue': patient_info.red_blood_cell_count_units,
        }
    elif attr == 'serum_calcium_level':
        return {
            'options': options_from_enum_class(SerumCalciumUnits),
            'default': PatientInfo._meta.get_field('serum_calcium_level_units').default,
            'uvalue': patient_info.serum_calcium_level_units,
        }
    elif attr == 'creatinine_clearance_rate':
        return {
            'options': options_from_enum_dict({'ml/min': 'mL/min'}),
            'default': 'ml/min',
            'uvalue': 'ml/min',
        }
    elif attr == 'serum_creatinine_level':
        return {
            'options': options_from_enum_class(SerumCreatinineUnits),
            'default': PatientInfo._meta.get_field('serum_creatinine_level_units').default,
            'uvalue': patient_info.serum_creatinine_level_units,
        }
    elif attr == 'hemoglobin_level':
        return {
            'options': options_from_enum_class(HemoglobinUnits),
            'default': PatientInfo._meta.get_field('hemoglobin_level_units').default,
            'uvalue': patient_info.hemoglobin_level_units,
        }
    elif attr == 'estimated_glomerular_filtration_rate':
        return {
            'options': options_from_enum_dict({'ml/minute/1.73m2': 'mL/minute/1.73m^2'}),
            'default': 'ml/minute/1.73m2',
            'uvalue': 'ml/minute/1.73m2',
        }
    elif attr == 'liver_enzyme_levels_ast':
        return {
            'options': options_from_enum_dict({'u/l': 'U/L'}),
            'default': 'u/l',
            'uvalue': 'u/l',
        }
    elif attr == 'liver_enzyme_levels_alt':
        return {
            'options': options_from_enum_dict({'u/l': 'U/L'}),
            'default': 'u/l',
            'uvalue': 'u/l',
        }
    elif attr == 'liver_enzyme_levels_alp':
        return {
            'options': options_from_enum_dict({'u/l': 'U/L'}),
            'default': 'u/l',
            'uvalue': 'u/l',
        }
    elif attr == 'serum_bilirubin_level_total':
        return {
            'options': options_from_enum_class(SerumBilirubinUnits),
            'default': PatientInfo._meta.get_field('serum_bilirubin_level_total_units').default,
            'uvalue': patient_info.serum_bilirubin_level_total_units,
        }
    elif attr == 'serum_bilirubin_level_direct':
        return {
            'options': options_from_enum_class(SerumBilirubinUnits),
            'default': PatientInfo._meta.get_field('serum_bilirubin_level_direct_units').default,
            'uvalue': patient_info.serum_bilirubin_level_direct_units,
        }
    elif attr == 'albumin_level':
        return {
            'options': options_from_enum_class(AlbuminUnits),
            'default': PatientInfo._meta.get_field('albumin_level_units').default,
            'uvalue': patient_info.albumin_level_units,
        }
    elif attr == 'monoclonal_protein_serum':
        return {
            'options': options_from_enum_dict({'g/dl': 'g/dL'}),
            'default': 'g/dl',
            'uvalue': 'g/dl',
        }
    elif attr == 'monoclonal_protein_urine':
        return {
            'options': options_from_enum_dict({'mg/24h': 'mg/24h'}),
            'default': 'mg/24h',
            'uvalue': 'mg/24h',
        }
    elif attr == 'lactate_dehydrogenase_level':
        return {
            'options': options_from_enum_dict({'u/l': 'U/L'}),
            'default': 'u/l',
            'uvalue': 'u/l',
        }
    elif attr == 'clonal_plasma_cells':
        return {
            'options': options_from_enum_dict({'%': '%'}),
            'default': '%',
            'uvalue': '%',
        }
    elif attr == 'ejection_fraction':
        return {
            'options': options_from_enum_dict({'%': '%'}),
            'default': '%',
            'uvalue': '%',
        }
    elif attr == 'kappa_flc':
        return {
            'options': options_from_enum_dict({'mg/l': 'mg/L'}),
            'default': 'mg/l',
            'uvalue': 'mg/l',
        }
    elif attr == 'lambda_flc':
        return {
            'options': options_from_enum_dict({'mg/l': 'mg/L'}),
            'default': 'mg/l',
            'uvalue': 'mg/l',
        }
    elif attr == 'largest_lymph_node_size':
        return {
            'options': options_from_enum_dict({'cm': 'cm'}),
            'default': 'cm',
            'uvalue': 'cm',
        }
    elif attr == 'spleen_size':
        return {
            'options': options_from_enum_dict({'cm': 'cm'}),
            'default': 'cm',
            'uvalue': 'cm',
        }
    elif attr == 'absolute_lymphocyte_count':
        return {
            'options': options_from_enum_dict({'cells/ul': 'cells/UL'}),
            'default': 'cells/ul',
            'uvalue': 'cells/ul',
        }
    elif attr == 'qtcf_value':
        return {
            'options': options_from_enum_dict({'ms': 'ms'}),
            'default': 'ms',
            'uvalue': 'ms',
        }
    elif attr == 'serum_beta2_microglobulin_level':
        return {
            'options': options_from_enum_dict({'mg/l': 'mg/L'}),
            'default': 'mg/l',
            'uvalue': 'mg/l',
        }
    elif attr == 'clonal_bone_marrow_b_lymphocytes':
        return {
            'options': options_from_enum_dict({'%': '%'}),
            'default': '%',
            'uvalue': '%',
        }
    elif attr == 'clonal_b_lymphocyte_count':
        return {
            'options': options_from_enum_dict({'cells/ul': 'cells/µL'}),
            'default': 'cells/ul',
            'uvalue': 'cells/ul',
        }


def trial_attr_units_for(attr):
    from trials.services.patient_info.patient_info import PatientInfo
    from trials.models import WeightUnits, PlateletCountUnits, \
        SerumCalciumUnits, SerumCreatinineUnits, HemoglobinUnits, SerumBilirubinUnits, AlbuminUnits

    if attr in ('weight_min', 'weight_max'):
        return options_from_enum_class(WeightUnits).get(PatientInfo._meta.get_field('weight_units').default)
    elif attr in ('hemoglobin_level_min', 'hemoglobin_level_max'):
        return options_from_enum_class(HemoglobinUnits).get(PatientInfo._meta.get_field('hemoglobin_level_units').default)
    elif attr in ('absolute_neutrophile_count_min', 'absolute_neutrophile_count_max'):
        return options_from_enum_class(PlateletCountUnits).get(PatientInfo._meta.get_field('absolute_neutrophile_count_units').default)
    elif attr in ('platelet_count_min', 'platelet_count_max'):
        return options_from_enum_class(PlateletCountUnits).get(PatientInfo._meta.get_field('platelet_count_units').default)
    elif attr in ('white_blood_cell_count_min', 'white_blood_cell_count_max'):
        return options_from_enum_class(PlateletCountUnits).get(PatientInfo._meta.get_field('white_blood_cell_count_units').default)
    elif attr in ('red_blood_cell_count_min', 'red_blood_cell_count_max'):
        return options_from_enum_class(PlateletCountUnits).get(PatientInfo._meta.get_field('red_blood_cell_count_units').default)
    elif attr in ('serum_creatinine_level_abs_min', 'serum_creatinine_level_abs_max'):
        return options_from_enum_class(SerumCreatinineUnits).get(PatientInfo._meta.get_field('serum_creatinine_level_units').default)
    elif attr in ('creatinine_clearance_rate_min', 'creatinine_clearance_rate_max'):
        return 'mL/min'
    elif attr in ('estimated_glomerular_filtration_rate_min', 'estimated_glomerular_filtration_rate_max'):
        return 'mL/minute/1.73m^2'
    elif attr in ('liver_enzyme_level_ast_abs_min', 'liver_enzyme_level_ast_abs_max'):
        return 'U/L'
    elif attr in ('liver_enzyme_level_alt_abs_min', 'liver_enzyme_level_alt_abs_max'):
        return 'U/L'
    elif attr in ('liver_enzyme_level_alp_abs_min', 'liver_enzyme_level_alp_abs_max'):
        return 'U/L'
    elif attr in ('albumin_min', 'albumin_max'):
        return options_from_enum_class(AlbuminUnits).get(PatientInfo._meta.get_field('albumin_level_units').default)
    elif attr in ('serum_bilirubin_total_level_abs_min', 'serum_bilirubin_total_level_abs_max'):
        return options_from_enum_class(SerumBilirubinUnits).get(PatientInfo._meta.get_field('serum_bilirubin_level_total_units').default)
    elif attr in ('serum_bilirubin_direct_level_abs_min', 'serum_bilirubin_direct_level_abs_max'):
        return options_from_enum_class(SerumBilirubinUnits).get(PatientInfo._meta.get_field('serum_bilirubin_level_direct_units').default)
    elif attr in ('ejection_fraction_min', 'ejection_fraction_max'):
        return '%'
    elif attr in ('clonal_plasma_cells_min', 'clonal_plasma_cells_max'):
        return '%'
    elif attr in ('serum_monoclonal_protein_level_min', 'serum_monoclonal_protein_level_max'):
        return 'g/dL'
    elif attr in ('urine_monoclonal_protein_level_min', 'urine_monoclonal_protein_level_max'):
        return 'g/dL'
    elif attr in ('serum_calcium_level_min', 'serum_calcium_level_max'):
        return options_from_enum_class(SerumCalciumUnits).get(PatientInfo._meta.get_field('serum_calcium_level_units').default)
    elif attr in ('lactate_dehydrogenase_level_min', 'lactate_dehydrogenase_level_max'):
        return 'U/L'
    elif attr in ('largest_lymph_node_size_min',):
        return 'cm'
    elif attr in ('spleen_size_min',):
        return 'cm'
    elif attr in ('absolute_lymphocyte_count_min', 'absolute_lymphocyte_count_max'):
        return 'cells/UL'
    elif attr in ('qtcf_value_max',):
        return 'ms'
    elif attr in ('serum_beta2_microglobulin_level_min', 'serum_beta2_microglobulin_level_max'):
        return 'mg/L'
    elif attr in ('clonal_bone_marrow_b_lymphocytes_min', 'clonal_bone_marrow_b_lymphocytes_max'):
        return '%'
    elif attr in ('clonal_b_lymphocyte_count_min', 'clonal_b_lymphocyte_count_max'):
        return 'cells/µL'
