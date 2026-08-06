from exact_matching.patient_info.convertors.alp_uln_calculator import AlpUlnCalculator
from exact_matching.patient_info.convertors.alt_uln_calculator import AltUlnCalculator
from exact_matching.patient_info.convertors.ast_uln_calculator import AstUlnCalculator
from exact_matching.patient_info.convertors.base_convertor import BaseConvertor
from exact_matching.patient_info.convertors.bili_d_uln_calculator import BiliDUlnCalculator
from exact_matching.patient_info.convertors.bili_t_uln_calculator import BiliTUlnCalculator
from exact_matching.patient_info.convertors.bilirubin_convertor import BilirubinConvertor
from exact_matching.patient_info.convertors.scr_uln_calculator import ScrUlnCalculator
from exact_matching.patient_info.convertors.serum_calcium_convertor import SerumCalciumConvertor
from exact_matching.patient_info.convertors.serum_creatinine_convertor import SerumCreatinineConvertor
from exact_matching.receptor_hierarchy import expand_uvalue as _receptor_uvalue

THERAPY_LINES_ATTRS = ["firstLineTherapy", "secondLineTherapy", "laterTherapy", "laterTherapies"]
THERAPY_LINES_ATTRS_UNDERSCORED = ["first_line_therapy", "second_line_therapy", "later_therapy", "later_therapies"]

THERAPIES_ATTRS = ["therapiesRequired", "therapiesExcluded", "therapyTypesRequired", "therapyTypesExcluded", "therapyComponentsRequired", "therapyComponentsExcluded"]
THERAPIES_ATTRS_UNDERSCORED = ["therapies_required", "therapies_excluded", "therapy_types_required", "therapy_types_excluded", "therapy_components_required", "therapy_components_excluded"]
TRIAL_GENETIC_MUTATIONS_ATTRS_UNDERSCORED = ["mutation_genes_required", "mutation_variants_required", "mutation_origins_required", "mutation_interpretations_required"]

ATTR_MAPPING_TYPE_COMPUTED = "computed"

# "priorSCT": "prior SCT",
# "priorAutologousSCT": "prior autologous SCT",
# "priorAllogeneicSCT": "prior allogeneic SCT",
# "recentSCT": "recent SCT",
# "recentAutologousSCT": "recent autologous SCT",
# "recentAllogeneicSCT": "recent allogeneic SCT",
# "relapsedPostSCT": "relapsed post-SCT",
# "relapsedPostAutologousSCT": "relapsed post-autologous SCT",
# "relapsedPostAllogeneicSCT": "relapsed post-allogeneic SCT",
# "completedTandemSCT": "completed tandem SCT",
# "neverReceivedSCT": "never received SCT",
# "preAutologousSCT": "pre-autologous SCT",
# "preAllogeneicSCT": "pre-allogeneic SCT",
#  VS
#  {'value': '', 'label': 'None'},
#  {'value': 'completedASCT', 'label': 'Completed ASCT'},
#  {'value': 'eligibleForASCT', 'label': 'Eligible for ASCT'},
#  {'value': 'ineligibleForASCT', 'label': 'Ineligible for ASCT'},
#  {'value': 'completedAllogeneicSCT', 'label': 'Completed Allogeneic SCT'},
#  {'value': 'preASCT', 'label': 'Pre-ASCT'},
#  {'value': 'postASCT', 'label': 'Post-ASCT'},
#  {'value': 'neverReceivedSCT', 'label': 'Never Received SCT'},
#  {'value': 'sctIneligible', 'label': 'SCT-Ineligible'},
#  {'value': 'relapsedPostASCT', 'label': 'Relapsed Post-ASCT'},
#  {'value': 'relapsedPostAllogeneicSCT', 'label': 'Relapsed Post-Allogeneic SCT'},
#  {'value': 'completedTandemSCT', 'label': 'Completed Tandem SCT'}

SCT_HISTORY_EXCLUDED_MAPPING = {
    'completedASCT': ['priorAutologousSCT'],
    'completedAllogeneicSCT': ['priorAllogeneicSCT']
}


def sct_value_is_none(value):
    """True iff `value` represents "no SCT history", tolerant to shape.

    `stem_cell_transplant_history` is stored as a JSONField but two
    canonical "no SCT" shapes coexist:
      - bare string `'None'`  (signal cleanup at trials/signals.py:105)
      - one-element list `['None']`  (user picked "None" from the
        multiselect on the form)

    Defensive against whitespace + casing drift on either shape so that
    `[' None ']`, `['none']`, `'NONE'`, etc. all match — preventing the
    silent require-SCT match regression class (#4333) from returning
    under casing/whitespace variants (#4340).

    Python `None` is rejected: it means "patient hasn't answered"
    (Unknown), which is semantically distinct from the explicit "None"
    answer.
    """
    if value is None:
        return False
    if isinstance(value, (list, tuple)):
        return len(value) == 1 and str(value[0]).strip().lower() == 'none'
    return str(value).strip().lower() == 'none'


TRIAL_ATTRS_JSON_AS_A_LIST = (
    "planned_therapies_required",
    "planned_therapies_excluded",
    "therapies_required",
    "therapies_excluded",
    "therapy_types_required",
    "therapy_types_excluded",
    "therapy_components_required",
    "therapy_components_excluded",
    "supportive_therapies_required",
    "supportive_therapies_excluded",
    "pre_existing_conditions_excluded",
    "stem_cell_transplant_history_excluded",
    "ethnicity_required",
    "cytogenic_markers_required",
    "cytogenic_markers_excluded",
    "molecular_markers_required",
    "molecular_markers_excluded",
    "histologic_types_required",
    "estrogen_receptor_statuses_required",
    "progesterone_receptor_statuses_required",
    "her2_statuses_required",
    "hrd_statuses_required",
    "hr_statuses_required",
    "tumor_stages_required",
    "tumor_stages_excluded",
    "nodes_stages_required",
    "nodes_stages_excluded",
    "distant_metastasis_stages_required",
    "distant_metastasis_stages_excluded",
    "staging_modalities_required",
    "mutation_genes_required",
    "mutation_variants_required",
    "mutation_origins_required",
    "mutation_interpretations_required",
    "concomitant_medications_excluded",
    "languages_skills_required",
    "stages",
    "binet_stages_required",
    "protein_expressions_required",
    "protein_expressions_excluded",
    "richter_transformations_required",
    "richter_transformations_excluded",
    "tumor_burdens_required",
    "disease_activities_required",
    "morphologic_variants_required",
    "morphologic_variants_excluded",
    "disease_behaviors_required",
    "disease_subtypes_required",
    "extranodal_sites_required",
    "bulky_disease_criteria_required",
    "mipi_risks_required",
    "mipi_c_risks_required",
    "high_risk_mcl_criteria_required",
    "high_risk_mcl_criteria_excluded",
    "high_risk_mcl_criteria_sufficient_any",
)

USER_TO_TRIAL_ATTRS_MAPPING = {

    # -----------
    # Disease tab
    # -----------

    "patient_age": {
        "type": "min_max_value",
        "searchable": True,
        "attr": "age",
        "attr_min": "age_low_limit",
        "attr_max": "age_high_limit",
    },
    "gender": {
        "type": "str_value",
        "searchable": True,
        "attr": "gender",
    },
    "ethnicity": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "attr": "ethnicity_required",
        "uvalue_function": {
            "ethnicity_required":
                lambda patient_info: patient_info.ethnicity,
        }
    },
    "languages_skills": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "attr": "languages_skills_required",
        "uvalue_function": {
            "languages_skills_required":
                lambda patient_info: patient_info.languages_skills,
        }
    },
    "weight": {
        "type": "min_max_value",
        "searchable": True,
        "attr": "weight",
        "units_convertor": BaseConvertor,
        "default_unit": "kg",
        "user_input_units_attr": "weight_units"
    },
    "bmi": {
        "type": "min_max_value",
        "searchable": True,
        "is_computed_value": True,
        "computed_value_type": "float",
        "attrs_to_compute": ("weight", "height"),
        "attr": "bmi",
    },
    "systolic_blood_pressure": {
        "type": "min_max_value",
        "searchable": True,
        "attr": "systolic_blood_pressure",
    },
    "diastolic_blood_pressure": {
        "type": "min_max_value",
        "searchable": True,
        "attr": "diastolic_blood_pressure",
    },

    "disease": {
        "type": "value",
        "custom_search": True,
        "searchable": True,
        "attr": "disease",
    },
    "stage": {
        "type": "value",
        "custom_search": True,
        "searchable": True,
        "attr": "stages",
    },
    "karnofsky_performance_score": {
        "type": "min_max_value",
        "searchable": True,
        "allow_blank_values": True,
        "attr": "karnofsky_performance_score",
    },
    "ecog_performance_status": {
        "type": "min_max_value",
        "searchable": True,
        "allow_blank_values": True,
        "attr": "ecog_performance_status",
    },
    "no_other_active_malignancies": {
        "type": "bool_restriction",
        "searchable": True,
        "attr": "no_other_active_malignancies_required",
    },
    "pre_existing_condition_categories": {
        "type": "value",
        "custom_search": True,
        "searchable": True,
        "attr": "pre_existing_conditions_excluded",
    },
    "peripheral_neuropathy_grade": {
        "type": "min_max_value",
        "searchable": True,
        "allow_blank_values": True,
        "attr": "peripheral_neuropathy_grade",
    },

    # MM-specific START
    "cytogenic_markers": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": ["MM", "CLL", "MCL"],
        "attr": ["cytogenic_markers_required", "cytogenic_markers_excluded"],
        "uvalue_function": {
            "cytogenic_markers_required":
                lambda patient_info: patient_info.cytogenic_markers,
            "cytogenic_markers_excluded":
                lambda patient_info: patient_info.cytogenic_markers,
        }
    },
    "molecular_markers": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": ["MM", "CLL", "MCL"],
        "attr": ["molecular_markers_required", "molecular_markers_excluded"],
        "uvalue_function": {
            "molecular_markers_required":
                lambda patient_info: patient_info.molecular_markers,
            "molecular_markers_excluded":
                lambda patient_info: patient_info.molecular_markers,
        }
    },
    "plasma_cell_leukemia": {
        "type": "value",
        "disease": "MM",
        "custom_search": True,
        "searchable": True,
        "attr": ["plasma_cell_leukemia_required", "no_plasma_cell_leukemia_required"],
    },
    "progression": {
        "type": "value",
        "disease": "MM",
        "custom_search": True,
        "searchable": True,
        "attr": ["disease_progression_active_required", "disease_progression_smoldering_required"],
    },
    "measurable_disease_imwg": {
        "type": "bool_restriction",
        "disease": "MM",
        "searchable": True,
        "is_computed_value": True,
        "under_user_control": True,
        "attr": "measurable_disease_imwg_required",
    },
    # MM-specific END

    # FL-specific START
    # gelfCriteriaStatus
    "flipi_score_options": {
        "type": "min_max_value",
        "custom_search": True,
        "searchable": True,
        "disease": "FL",
        "attr_min": "flipi_score_min",
        "attr_max": "flipi_score_max",
        "attr": ["flipi_score_min", "flipi_score_max"],
    },
    "tumor_grade": {
        "type": "min_max_value",
        "custom_search": True,
        "searchable": True,
        "disease": "FL",
        "attr_min": "tumor_grade_min",
        "attr_max": "tumor_grade_max",
        "attr": ["tumor_grade_min", "tumor_grade_max"],
    },
    # FL-specific END

    # CLL-specific START
    "binet_stage": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "CLL",
        "attr": ["binet_stages_required"],
        "uvalue_function": {
            "binet_stages_required":
                lambda patient_info: patient_info.binet_stage,
        }
    },
    "protein_expressions": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": ["CLL", "MCL"],
        "attr": ["protein_expressions_required", "protein_expressions_excluded"],
        "uvalue_function": {
            "protein_expressions_required":
                lambda patient_info: patient_info.protein_expressions,
            "protein_expressions_excluded":
                lambda patient_info: patient_info.protein_expressions,
        }
    },
    "richter_transformation": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "CLL",
        "attr": ["richter_transformations_required", "richter_transformations_excluded"],
        "uvalue_function": {
            "richter_transformations_required":
                lambda patient_info: patient_info.richter_transformation,
            "richter_transformations_excluded":
                lambda patient_info: patient_info.richter_transformation,
        }
    },
    "tumor_burden": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "CLL",
        "attr": ["tumor_burdens_required"],
        "uvalue_function": {
            "tumor_burdens_required":
                lambda patient_info: patient_info.tumor_burden,
        }
    },
    "lymphocyte_doubling_time": {
        "type": "min_max_value",
        "searchable": True,
        "disease": "CLL",
        "attr": "lymphocyte_doubling_time",
    },
    "tp53_disruption": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": ["CLL", "MCL"],
        "attr": ["tp53_disruption_required", "tp53_disruption_excluded"],
        "uvalue_function": {
            "tp53_disruption_required": lambda patient_info: patient_info.tp53_disruption,
            "tp53_disruption_excluded": lambda patient_info: patient_info.tp53_disruption,
        }
    },
    "measurable_disease_iwcll": {
        "type": "bool_restriction",
        "disease": "CLL",
        "searchable": True,
        "attr": "measurable_disease_iwcll_required",
    },
    "hepatomegaly": {
        "type": "bool_restriction",
        "disease": ["CLL", "MCL"],
        "searchable": True,
        "attr": "hepatomegaly_required",
    },
    "autoimmune_cytopenias_refractory_to_steroids": {
        "type": "bool_restriction",
        "disease": "CLL",
        "searchable": True,
        "attr": "autoimmune_cytopenias_refractory_to_steroids_required",
    },
    "lymphadenopathy": {
        "type": "bool_restriction",
        "disease": ["CLL", "MCL"],
        "searchable": True,
        "attr": "lymphadenopathy_required",
    },
    "largest_lymph_node_size": {
        "type": "min_value",
        "disease": ["CLL", "MCL"],
        "searchable": True,
        "attr": "largest_lymph_node_size",
    },
    "splenomegaly": {
        "type": "bool_restriction",
        "disease": ["CLL", "MCL"],
        "searchable": True,
        "attr": "splenomegaly_required",
    },
    "spleen_size": {
        "type": "min_value",
        "disease": ["CLL", "MCL"],
        "searchable": True,
        "attr": "spleen_size",
    },
    "disease_activity": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "CLL",
        "attr": ["disease_activities_required"],
        "uvalue_function": {
            "disease_activities_required":
                lambda patient_info: patient_info.disease_activity,
        }
    },
    "btk_inhibitor_refractory": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "CLL",
        "attr": ["btk_inhibitor_refractory_required", "btk_inhibitor_refractory_excluded"],
        "uvalue_function": {
            "btk_inhibitor_refractory_required": lambda patient_info: patient_info.btk_inhibitor_refractory,
            "btk_inhibitor_refractory_excluded": lambda patient_info: patient_info.btk_inhibitor_refractory,
        }
    },
    "bcl2_inhibitor_refractory": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "CLL",
        "attr": ["bcl2_inhibitor_refractory_required", "bcl2_inhibitor_refractory_excluded"],
        "uvalue_function": {
            "bcl2_inhibitor_refractory_required": lambda patient_info: patient_info.bcl2_inhibitor_refractory,
            "bcl2_inhibitor_refractory_excluded": lambda patient_info: patient_info.bcl2_inhibitor_refractory,
        }
    },
    "absolute_lymphocyte_count": {
        "type": "min_max_value",
        "disease": "CLL",
        "searchable": True,
        "attr": "absolute_lymphocyte_count",
    },
    "qtcf_value": {
        # QTcF applies to all diseases, not just CLL (CB #4145, SME-confirmed).
        "type": "max_value",
        "searchable": True,
        "attr": "qtcf_value",
    },
    "serum_beta2_microglobulin_level": {
        "type": "min_max_value",
        "disease": "CLL",
        "searchable": True,
        "attr": "serum_beta2_microglobulin_level",
    },
    "clonal_bone_marrow_b_lymphocytes": {
        "type": "min_max_value",
        "disease": "CLL",
        "searchable": True,
        "attr": "clonal_bone_marrow_b_lymphocytes",
    },
    "clonal_b_lymphocyte_count": {
        "type": "min_max_value",
        "disease": "CLL",
        "searchable": True,
        "attr": "clonal_b_lymphocyte_count",
    },
    "bone_marrow_involvement": {
        "type": "bool_restriction",
        "disease": "CLL",
        "searchable": True,
        "attr": "bone_marrow_involvement_required",
    },
    # CLL-specific END

    # MCL-specific START
    "morphologic_variant": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "MCL",
        "attr": ["morphologic_variants_required", "morphologic_variants_excluded"],
        "uvalue_function": {
            "morphologic_variants_required":
                lambda patient_info: patient_info.morphologic_variant,
            "morphologic_variants_excluded":
                lambda patient_info: patient_info.morphologic_variant,
        }
    },
    "largest_lesion_size": {
        "type": "min_max_value",
        # No custom_search — routed through the type-based min_max_value
        # branch in filter_by_patient_info like every other min_max_value
        # config (weight, patient_age, etc.). #94 fix.
        "searchable": True,
        "disease": "MCL",
        "attr_min": "largest_lesion_size_min",
        "attr_max": "largest_lesion_size_max",
        "attr": ["largest_lesion_size_min", "largest_lesion_size_max"],
    },
    "p53_ihc": {
        "type": "min_max_value",
        "searchable": True,
        "disease": "MCL",
        "attr_min": "p53_ihc_min",
        "attr_max": "p53_ihc_max",
        "attr": ["p53_ihc_min", "p53_ihc_max"],
    },
    "disease_behavior": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "MCL",
        "attr": ["disease_behaviors_required"],
        "uvalue_function": {
            "disease_behaviors_required":
                lambda patient_info: patient_info.disease_behavior,
        }
    },
    "disease_subtype": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "MCL",
        "attr": ["disease_subtypes_required"],
        "uvalue_function": {
            "disease_subtypes_required":
                lambda patient_info: patient_info.disease_subtype,
        }
    },
    "extranodal_sites": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "MCL",
        "attr": ["extranodal_sites_required"],
        "uvalue_function": {
            "extranodal_sites_required":
                lambda patient_info: patient_info.extranodal_sites,
        }
    },
    "mipi_risk": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "MCL",
        "attr": ["mipi_risks_required"],
        "uvalue_function": {
            "mipi_risks_required":
                lambda patient_info: patient_info.mipi_risk,
        }
    },
    "mipi_c_risk": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "MCL",
        "attr": ["mipi_c_risks_required"],
        "uvalue_function": {
            "mipi_c_risks_required":
                lambda patient_info: patient_info.mipi_c_risk,
        }
    },
    "bulky_disease_criteria": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "MCL",
        "attr": ["bulky_disease_criteria_required"],
        "uvalue_function": {
            "bulky_disease_criteria_required":
                lambda patient_info: patient_info.bulky_disease_criteria,
        }
    },
    # Authoring compound/"tiered" rules (e.g. Jain classification, NCT05861050):
    # ">=1 primary high-risk feature; secondary gene mutations only count if a
    # primary is also present" is expressed with the existing schema as
    # required=[primary features] + min_count=1. Secondary genes are omitted —
    # they can never qualify a patient alone, and once any primary is present
    # min_count=1 is already met. A full tiered/only_if_tier engine is only
    # needed for min_count>=2 compound trials (none yet); deferred (CB #4405).
    "high_risk_mcl_criteria": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "is_computed_value": True,
        "computed_value_type": "str",
        "disease": "MCL",
        "attr": [
            "high_risk_mcl_criteria_required",
            "high_risk_mcl_criteria_excluded",
            "high_risk_mcl_criteria_sufficient_any",
        ],
        "criteria_count_match": True,
        "criteria_required_attr": "high_risk_mcl_criteria_required",
        "criteria_excluded_attr": "high_risk_mcl_criteria_excluded",
        "criteria_sufficient_any_attr": "high_risk_mcl_criteria_sufficient_any",
        "criteria_min_count_attr": "high_risk_mcl_criteria_min_count",
        "criteria_derived": lambda patient_info: patient_info.high_risk_mcl_criteria,
        "criteria_unknown_codes": lambda pia, required: pia.high_risk_mcl_criteria_unknown_codes(required),
        "criteria_all_unknown_codes": lambda pia: pia.high_risk_mcl_criteria_all_unknown_codes(),
        "uvalue_function": {
            "high_risk_mcl_criteria_required": lambda patient_info: patient_info.high_risk_mcl_criteria,
            "high_risk_mcl_criteria_excluded": lambda patient_info: patient_info.high_risk_mcl_criteria,
            "high_risk_mcl_criteria_sufficient_any": lambda patient_info: patient_info.high_risk_mcl_criteria,
        }
    },
    # MCL-specific END

    # BC-specific START
    "menopausal_status": {
        "type": "str_value",
        "searchable": True,
        "disease": "BC",
        "attr": "menopausal_status",
    },
    # metastatic_status removed (#4121) — redundant with stage / distant_metastasis_stage.
    "bone_only_metastasis_status": {
        "type": "bool_restriction",
        "searchable": True,
        "disease": "BC",
        "attr": "bone_only_metastasis_required",
    },
    "histologic_type": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "BC",
        "attr": ["histologic_types_required"],
        "uvalue_function": {
            "histologic_types_required":
                lambda patient_info: patient_info.histologic_type,
        }
    },
    "biopsy_grade": {
        "type": "min_max_value",
        "searchable": True,
        "disease": "BC",
        "attr": "biopsy_grade",
    },
    "measurable_disease_by_recist_status": {
        "type": "bool_restriction",
        "searchable": True,
        "disease": "BC",
        "attr": "measurable_disease_by_recist_required",
    },
    "meets_meas_or_bone_status": {
        "type": "bool_restriction",
        "searchable": True,
        "is_computed_value": True,
        "computed_value_type": "bool",
        "attrs_to_compute": ("measurable_disease_by_recist_status", "bone_only_metastasis_status"),
        "disease": "BC",
        "under_user_control": True,
        "attr": "meets_meas_or_bone_required",
    },
    "estrogen_receptor_status": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "BC",
        "attr": ["estrogen_receptor_statuses_required"],
        "uvalue_function": {
            "estrogen_receptor_statuses_required":
                lambda patient_info: _receptor_uvalue(patient_info.estrogen_receptor_status, 'er'),
        }
    },
    "progesterone_receptor_status": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "BC",
        "attr": ["progesterone_receptor_statuses_required"],
        "uvalue_function": {
            "progesterone_receptor_statuses_required":
                lambda patient_info: _receptor_uvalue(patient_info.progesterone_receptor_status, 'pr'),
        }
    },
    "her2_status": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "BC",
        "attr": ["her2_statuses_required"],
        "uvalue_function": {
            "her2_statuses_required":
                lambda patient_info: patient_info.her2_status,
        }
    },
    "hrd_status": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "BC",
        "attr": ["hrd_statuses_required"],
        "uvalue_function": {
            "hrd_statuses_required":
                lambda patient_info: patient_info.hrd_status,
        }
    },
    "hr_status": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "BC",
        "attr": ["hr_statuses_required"],
        "uvalue_function": {
            "hr_statuses_required":
                lambda patient_info: _receptor_uvalue(patient_info.hr_status, 'hr'),
        }
    },
    "tnbc_status": {
        "type": "bool_restriction",
        "searchable": True,
        "disease": "BC",
        "attr": "tnbc_status",
    },
    "tumor_stage": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "BC",
        "attr": ["tumor_stages_required", "tumor_stages_excluded"],
        "uvalue_function": {
            "tumor_stages_required":
                lambda patient_info: patient_info.tumor_stage,
            "tumor_stages_excluded":
                lambda patient_info: patient_info.tumor_stage,
        }
    },
    "nodes_stage": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "BC",
        "attr": ["nodes_stages_required", "nodes_stages_excluded"],
        "uvalue_function": {
            "nodes_stages_required":
                lambda patient_info: patient_info.nodes_stage,
            "nodes_stages_excluded":
                lambda patient_info: patient_info.nodes_stage,
        }
    },
    "distant_metastasis_stage": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "BC",
        "attr": ["distant_metastasis_stages_required", "distant_metastasis_stages_excluded"],
        "uvalue_function": {
            "distant_metastasis_stages_required":
                lambda patient_info: patient_info.distant_metastasis_stage,
            "distant_metastasis_stages_excluded":
                lambda patient_info: patient_info.distant_metastasis_stage,
        }
    },
    "staging_modalities": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "disease": "BC",
        "attr": ["staging_modalities_required"],
        "uvalue_function": {
            "staging_modalities_required":
                lambda patient_info: patient_info.staging_modalities,
        }
    },
    "toxicity_grade": {
        "type": "max_value",
        "searchable": True,
        "disease": ["MM", "BC", "CLL"],
        "allow_blank_values": True,
        "attr": "toxicity_grade",
    },
    "planned_therapies": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "attr": ["planned_therapies_required", "planned_therapies_excluded"],
        "uvalue_function": {
            "planned_therapies_required":
                lambda patient_info: patient_info.planned_therapies,
            "planned_therapies_excluded":
                lambda patient_info: patient_info.planned_therapies,
        }
    },
    "supportive_therapies": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "attr": ["supportive_therapies_required", "supportive_therapies_excluded"],
        "uvalue_function": {
            "supportive_therapies_required":
                lambda patient_info: patient_info.supportive_therapies,
            "supportive_therapies_excluded":
                lambda patient_info: patient_info.supportive_therapies,
        }
    },
    "genetic_mutations": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "searchable": True,
        "disease": "BC",
        "attr": TRIAL_GENETIC_MUTATIONS_ATTRS_UNDERSCORED,
        "uvalue_function": {
            "mutation_genes_required":
                lambda patient_info: patient_info.mutation_genes,
            "mutation_variants_required":
                lambda patient_info: patient_info.mutation_variants,
            "mutation_origins_required":
                lambda patient_info: patient_info.mutation_origins,
            "mutation_interpretations_required":
                lambda patient_info: patient_info.mutation_interpretations,
        }
    },
    "pd_l1_tumor_cels": {
        "type": "min_max_value",
        "searchable": True,
        "disease": "BC",
        "attr": "pd_l1_tumor_cels",
    },
    "pd_l1_assay": {
        "type": "str_value",
        "searchable": True,
        "disease": "BC",
        "attr": "pd_l1_assay",
    },
    "pd_l1_ic_percentage": {
        "type": "min_max_value",
        "searchable": True,
        "disease": "BC",
        "attr": "pd_l1_ic_percentage",
    },
    "pd_l1_combined_positive_score": {
        "type": "min_max_value",
        "searchable": True,
        "disease": "BC",
        "attr": "pd_l1_combined_positive_score",
    },
    "ki67_proliferation_index": {
        "type": "min_max_value",
        "searchable": True,
        "disease": ["BC", "MCL"],
        "attr": "ki67_proliferation_index",
    },
    # BC-specific END

    # -------------
    # Treatment tab
    # -------------

    "prior_therapy": {
        "type": "min_max_value",
        "custom_search": True,
        "searchable": True,
        "attr_min": "therapy_lines_count_min",
        "attr_max": "therapy_lines_count_max",
        "attr": ['therapy_lines_count_min', 'therapy_lines_count_max'],
    },

    "first_line_therapy": {
        "type": "value",
        "custom_search": True,
        "searchable": True,
        "attr": THERAPIES_ATTRS_UNDERSCORED
    },
    "second_line_therapy": {
        "type": "value",
        "custom_search": True,
        "searchable": True,
        "skip_in_counts": True,
        "attr": THERAPIES_ATTRS_UNDERSCORED
    },
    "later_therapy": {
        "type": "value",
        "custom_search": True,
        "searchable": True,
        "skip_in_counts": True,
        "attr": THERAPIES_ATTRS_UNDERSCORED
    },
    "later_therapies": {
        "type": "value",
        # "type": ATTR_MAPPING_TYPE_COMPUTED,
        "custom_search": True,
        "searchable": True,
        "skip_in_counts": True,
        "attr": THERAPIES_ATTRS_UNDERSCORED,
        # "uvalue_function": {
        # }
        #     "supportive_therapies_required":
        #         lambda patient_info: patient_info.supportive_therapies,
        #     "supportive_therapies_excluded":
        #         lambda patient_info: patient_info.supportive_therapies,
        # }
    },
    "last_treatment": {
        "type": "value",
        "custom_search": True,
        "searchable": True,
        "attr": "washout_period_duration",
    },
    "concomitant_medications": {
        "type": "value",
        "custom_search": True,
        "searchable": True,
        "attr": ["concomitant_medications_excluded", "concomitant_medications_washout_period_duration"],
    },
    "stem_cell_transplant_history": {
        "type": ATTR_MAPPING_TYPE_COMPUTED,
        "attr": ["stem_cell_transplant_history_required", "stem_cell_transplant_history_excluded"],
        "custom_search": True,
        "disease": ["MM", "FL"],
        "uvalue_function": {
            "stem_cell_transplant_history_required":
                lambda patient_info: bool(patient_info.stem_cell_transplant_history),
            "stem_cell_transplant_history_excluded":
                lambda patient_info: patient_info.stem_cell_transplant_history,
        }
    },
    "relapse_count": {
        "type": "min_max_value",
        "searchable": True,
        "allow_blank_values": True,
        "attr": "relapse_count",
    },
    "treatment_refractory_status": {
        "type": "value",
        "custom_search": True,
        "searchable": True,
        "attr": ["refractory_required", "not_refractory_required"],
    },

    # ---------
    # Blood tab
    # ---------

    # Bone Lesions?

    "absolute_neutrophile_count": {
        "type": "min_max_value",
        "searchable": True,
        "attr": "absolute_neutrophile_count",
        "units_convertor": BaseConvertor,
        "default_unit": "cells/UL",
        "user_input_units_attr": "absolute_neutrophile_count_units",
    },
    "platelet_count": {
        "type": "min_max_value",
        "searchable": True,
        "attr": "platelet_count",
        "units_convertor": BaseConvertor,
        "default_unit": "cells/UL",
        "user_input_units_attr": "platelet_count_units",
    },
    "white_blood_cell_count": {
        "type": "min_max_value",
        "searchable": True,
        "attr": "white_blood_cell_count",
        "units_convertor": BaseConvertor,
        "default_unit": "cells/L",
        "user_input_units_attr": "white_blood_cell_count_units",
    },
    "red_blood_cell_count": {
        "type": "min_max_value",
        "searchable": True,
        "attr": "red_blood_cell_count",
        "units_convertor": BaseConvertor,
        "default_unit": "cells/L",
        "user_input_units_attr": "red_blood_cell_count_units",
    },
    "serum_calcium_level": {
        "type": "min_max_value",
        "searchable": True,
        "attr": "serum_calcium_level",
        "units_convertor": SerumCalciumConvertor,
        "default_unit": "mg/dL",
        "user_input_units_attr": "serum_calcium_level_units",
    },
    "creatinine_clearance_rate": {
        "type": "min_max_value",
        "searchable": True,
        "default_unit": "ml/min",
        "attr": "creatinine_clearance_rate",
    },
    "serum_creatinine_level": {
        "type": "min_max_value",
        "searchable": True,
        "attr": "serum_creatinine_level_abs",
        "units_convertor": SerumCreatinineConvertor,
        "default_unit": "mg/dL",
        "user_input_units_attr": "serum_creatinine_level_units",
        "uln_calculator": ScrUlnCalculator,
        "uln_attr_min": "serum_creatinine_level_uln_min",
        "uln_attr_max": "serum_creatinine_level_uln_max",
    },
    "hemoglobin_level": {
        "type": "min_max_value",
        "searchable": True,
        "attr": "hemoglobin_level",
        "units_convertor": BaseConvertor,
        "default_unit": "g/dL",
        "user_input_units_attr": "hemoglobin_level_units",
    },
    "meets_crab": {
        "type": "bool_restriction",
        "searchable": True,
        "is_computed_value": True,
        # "under_user_control": True,
        "attr": "meets_crab",
    },

    "estimated_glomerular_filtration_rate": {
        "type": "min_max_value",
        "searchable": True,
        "default_unit": "mL/minute/1.73m^2",
        "attr": "estimated_glomerular_filtration_rate",
    },
    "liver_enzyme_levels_ast": {
        "type": "min_max_value",
        "searchable": True,
        "attr": "liver_enzyme_level_ast_abs",
        "default_unit": "U/L",
        "uln_calculator": AstUlnCalculator,
        "uln_attr_min": "liver_enzyme_level_ast_uln_min",
        "uln_attr_max": "liver_enzyme_level_ast_uln_max",
    },
    "liver_enzyme_levels_alt": {
        "type": "min_max_value",
        "searchable": True,
        "default_unit": "U/L",
        "attr": "liver_enzyme_level_alt_abs",
        "uln_calculator": AltUlnCalculator,
        "uln_attr_min": "liver_enzyme_level_alt_uln_min",
        "uln_attr_max": "liver_enzyme_level_alt_uln_max",
    },
    "liver_enzyme_levels_alp": {
        "type": "min_max_value",
        "searchable": True,
        "default_unit": "U/L",
        "attr": "liver_enzyme_level_alp_abs",
        "uln_calculator": AlpUlnCalculator,
        "uln_attr_min": "liver_enzyme_level_alp_uln_min",
        "uln_attr_max": "liver_enzyme_level_alp_uln_max",
    },
    "albumin_level": {
        "type": "min_max_value",
        "searchable": True,
        "attr": "albumin",
    },
    "serum_bilirubin_level_total": {
        "type": "min_max_value",
        "searchable": True,
        "attr": "serum_bilirubin_total_level_abs",
        "units_convertor": BilirubinConvertor,
        "default_unit": "mg/dL",
        "user_input_units_attr": "serum_bilirubin_level_total_units",
        "uln_calculator": BiliTUlnCalculator,
        "uln_attr_min": "serum_bilirubin_total_level_uln_min",
        "uln_attr_max": "serum_bilirubin_total_level_uln_max",
    },
    "serum_bilirubin_level_direct": {
        "type": "min_max_value",
        "searchable": True,
        "attr": "serum_bilirubin_direct_level_abs",
        "units_convertor": BilirubinConvertor,
        "default_unit": "mg/dL",
        "user_input_units_attr": "serum_bilirubin_level_direct_units",
        "uln_calculator": BiliDUlnCalculator,
        "uln_attr_min": "serum_bilirubin_direct_level_uln_min",
        "uln_attr_max": "serum_bilirubin_direct_level_uln_max",
    },
    "abnormal_kappa_lambda_ratio": {
        "type": "bool_restriction",
        "searchable": True,
        "is_computed_value": True,
        "computed_value_type": "bool",
        "attrs_to_compute": ("kappa_flc", "lambda_flc"),
        "under_user_control": True,
        "attr": "kappa_lambda_abnormal_required",
    },
    "meets_slim": {
        "type": "bool_restriction",
        "searchable": True,
        "is_computed_value": True,
        "under_user_control": True,
        "attr": "meets_slim",
    },
    "meets_lugano": {
        "type": "bool_restriction",
        "searchable": True,
        "is_computed_value": True,
        "under_user_control": True,
        "disease": ["FL", "MCL"],
        "attr": "meets_lugano",
    },
    "meets_gelf": {
        "type": "bool_restriction",
        "searchable": True,
        "is_computed_value": True,
        "under_user_control": True,
        "disease": "FL",
        "attr": "meets_gelf",
    },

    # --------
    # Labs tab
    # --------

    "monoclonal_protein_serum": {
        "type": "min_max_value",
        "searchable": True,
        "default_unit": "g/dL",
        "attr": "serum_monoclonal_protein_level",
    },
    "monoclonal_protein_urine": {
        "type": "min_max_value",
        "searchable": True,
        "default_unit": "mg/24h",
        "attr": "urine_monoclonal_protein_level",
    },
    "lactate_dehydrogenase_level": {
        "type": "min_max_value",
        "searchable": True,
        "default_unit": "U/L",
        "attr": "lactate_dehydrogenase_level",
    },
    "pulmonary_function_test_result": {
        "type": "bool_restriction",
        "searchable": True,
        "under_user_control": True,
        "attr": "pulmonary_function_test_result_required",
    },
    "bone_imaging_result": {
        "type": "bool_restriction",
        "searchable": True,
        "under_user_control": True,
        "attr": "bone_imaging_result_required",
    },
    "clonal_plasma_cells": {
        "type": "min_max_value",
        "searchable": True,
        "attr": "clonal_plasma_cells",
    },
    "ejection_fraction": {
        "type": "min_max_value",
        "searchable": True,
        "attr": "ejection_fraction",
    },
    "no_hiv_status": {
        "type": "bool_restriction",
        "searchable": True,
        "attr": "no_hiv_required",
    },
    "no_hepatitis_b_status": {
        "type": "bool_restriction",
        "searchable": True,
        "attr": "no_hepatitis_b_required",
    },
    "no_hepatitis_c_status": {
        "type": "bool_restriction",
        "searchable": True,
        "attr": "no_hepatitis_c_required",
    },

    # ------------
    # Behavior tab
    # ------------

    "consent_capability": {
        "type": "bool_restriction",
        "searchable": True,
        "under_user_control": True,
        "attr": "consent_capability_required",
    },
    "caregiver_availability_status": {
        "type": "bool_restriction",
        "searchable": True,
        "under_user_control": True,
        "attr": "caregiver_availability_required",
    },
    "contraceptive_use": {
        "type": "bool_restriction",
        "searchable": True,
        "under_user_control": True,
        "attr": "contraceptive_use_requirement",
    },
    "no_pregnancy_or_lactation_status": {
        "type": "bool_restriction",
        "searchable": True,
        "attr": "no_pregnancy_or_lactation_required",
        "value_overrides": {
            "conditional_attr_name": "gender",
            "conditional_attr_value": "M",
            "new_value": True
        }
    },
    "pregnancy_test_result": {
        "type": "bool_restriction",
        "searchable": True,
        "under_user_control": True,
        "attr": "negative_pregnancy_test_result_required",
        "value_overrides": {
            "conditional_attr_name": "gender",
            "conditional_attr_value": "M",
            "new_value": True
        }
    },
    "no_mental_health_disorder_status": {
        "type": "bool_restriction",
        "searchable": True,
        "attr": "no_mental_health_disorder_required",
    },
    "no_concomitant_medication_status": {
        "type": "bool_restriction",
        "searchable": True,
        "attr": "no_concomitant_medication_required",
    },
    "no_tobacco_use_status": {
        "type": "bool_restriction",
        "searchable": True,
        "attr": "no_tobacco_use_required",
    },
    "no_substance_use_status": {
        "type": "bool_restriction",
        "searchable": True,
        "attr": "no_substance_use_required",
    },
    "no_geographic_exposure_risk": {
        "type": "bool_restriction",
        "searchable": True,
        "attr": "no_geographic_exposure_risk_required",
    },
    "no_active_infection_status": {
        "type": "bool_restriction",
        "searchable": True,
        "attr": "no_active_infection_required",
    },
    "renal_adequacy_status": {
        "type": "bool_restriction",
        "searchable": True,
        "is_computed_value": True,
        # "under_user_control": True,
        "attr": "renal_adequacy_required",
    },
    "hepatic_adequacy_status": {
        "type": "bool_restriction",
        "searchable": True,
        "is_computed_value": True,
        "attr": "hepatic_adequacy_required",
    },
    "haematological_adequacy_status": {
        "type": "bool_restriction",
        "searchable": True,
        "is_computed_value": True,
        "attr": "haematological_adequacy_required",
    },
}

# -------
# Disease
# -------


# Patient Age -- covered
# Gender -- covered
# Weight -- covered
# Height -- covered over BMI
# Ethnicity -- covered partially for ULNs
# Blood Pressure SBP -- covered
# Blood Pressure DBP -- covered
# Country -- use for distance search only
# Postal Code -- use for distance search only

# Disease -- covered
# Stage -- covered
# Karnofsky Performance Score -- covered
# ECOG Performance Status -- covered
# No Other Active Malignancies -- covered
# Peripheral Neuropathy Grade -- covered
# Cytogenic Markers -- NOT covered
# Molecular Markers -- NOT covered
# Plasma Cell Leukemia -- covered
# Progression -- covered
# GELF Criteria Status -- NOT covered
# FLIPI Score -- covered
# Tumor Grade -- covered

# ---------
# Treatment
# ---------

# Prior Therapy -- covered

# First Line Therapy -- covered
# Therapy Date -- no need
# Outcome -- no need
#
# Second Line Therapy  -- covered
# Therapy Date -- no need
# Outcome -- no need
#
# Later Therapy -- covered
# Therapy Date -- no need
# Outcome -- no need
#
# Relapse Count -- covered
# Refractory Status  -- covered

# -----
# Blood
# -----

# ANC -- covered
# Platelet -- covered
# White Blood Cells -- covered
# Serum Calcium Level -- covered
# Creatinine Clearance Rate -- covered
# Serum Creatinine -- covered
# Hemoglobin -- covered
# Bone Lesions -- partially covered -- in use in meetCRAB / meetSLiM
# Meets CRAB -- covered

# Glomerular Filtration Rate -- covered
# Liver Enzyme AST -- covered
# Liver Enzyme ALT -- covered
# Serum Bilirubin Total -- covered
# Serum Bilirubin Direct -- covered
# Clonal Bone Marrow Plasma Cells Percentage -- covered
# Kappa FLC -- partially covered -- in use in abnormal ratio
# Lambda FLC -- partially covered -- in use in abnormal ratio
# Meets SLIM -- covered

# ----
# Labs
# ----

# Monoclonal Protein Serum -- covered
# Monoclonal Protein Urine -- covered
# Lactate Dehydrogenase Level -- covered
# Pulmonary Function Test -- covered and under_user_control (skipping)
# Bone Imaging Result -- covered and under_user_control (skipping)
# Clonal Plasma Cells -- covered
# Ejection Fraction -- covered

# --------
# Behavior
# --------

# I have ability to consent -- covered and under user control (skipped)
# I have availability of a caregiver -- covered and under user control (skipped)
# I am using contraceptive -- covered and under user control (skipped)
# I am not pregnant or lactating -- covered and skipped (valid) for men
# I have a pregnancy test result -- covered and skipped (valid) for men + under user control (skipped)
# I have no mental health disorders -- covered
# I'm not taking concomitant medication -- covered
# I'm not using tobacco -- covered
# I'm not using non-prescription drugs recreationally -- covered
# I have no geographic (occupational, environmental, infectious disease) exposure risk -- covered

