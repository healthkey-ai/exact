from math import log10

import inflection
from django.db import models
from django.db.models import Q
from django.utils.functional import cached_property

from trials.services.patient_info.configs import THERAPY_LINES_ATTRS_UNDERSCORED
from trials.services.patient_info.convertors.base_convertor import BaseConvertor
from trials.services.patient_info.convertors.serum_calcium_convertor import SerumCalciumConvertor
from trials.services.patient_info.convertors.serum_creatinine_convertor import SerumCreatinineConvertor
from trials.services.trial_details.configs import *
from trials.services.therapies_mapper import *
from trials.services.user_to_trial_attrs_mapper import *


# Default hepatic adequacy thresholds (ratio to ULN). Used when the trial does
# not specify its own thresholds for the related laboratory attributes.
HEPATIC_ADEQUACY_BILIRUBIN_TOTAL_ULN_MAX = 1.5
HEPATIC_ADEQUACY_AST_ULN_MAX = 2.5
HEPATIC_ADEQUACY_ALT_ULN_MAX = 2.5

# Haematological adequacy thresholds, expressed in each field's stored scale so
# they line up with how trials state their own limits (e.g. platelet_count_min).
# ANC is stored per-uL (1500 = 1500/uL); platelet_count is stored in 10^9/L i.e.
# thousands/uL (100 = 100,000/uL); hemoglobin in g/dL. Fixed clinical defaults,
# mirroring the patient-side renal_adequacy_status pattern.
HAEMATOLOGICAL_ADEQUACY_ANC_MIN = 1500
HAEMATOLOGICAL_ADEQUACY_PLATELET_MIN = 100
HAEMATOLOGICAL_ADEQUACY_HEMOGLOBIN_MIN = 9


# Per-criterion source fields for derived high-risk MCL criteria. A criterion's
# absence is only confirmable once every source field it can be derived from is
# answered (drives the unknown-vs-none distinction, #4399/#4416). Codes absent
# from this map are treated as determinable. Ported verbatim from CB.
HIGH_RISK_MCL_CRITERIA_SOURCES = {
    'tp53_mutation': ['molecular_markers'],
    'kmt2d_mutation': ['molecular_markers'],
    'nsd2_mutation': ['molecular_markers'],
    # Gene-specific NOTCH codes are only confirmable-absent when molecular markers
    # are answered AND the ambiguous combined notch1or2Mutations option is NOT
    # selected (it can't disambiguate NOTCH1 from NOTCH2). The '_notch_specific'
    # token encodes that second condition (#4406).
    'notch1_mutation': ['molecular_markers', '_notch_specific'],
    'notch2_mutation': ['molecular_markers', '_notch_specific'],
    'notch1_or_2': ['molecular_markers'],
    'cdkn2a_alteration': ['molecular_markers'],
    'smarca4_mutation': ['molecular_markers'],
    'ccnd1_alteration': ['molecular_markers'],
    'bcl2_amplification': ['molecular_markers'],
    'del17p': ['molecular_markers', 'cytogenic_markers'],
    'complex_karyotype': ['molecular_markers', 'cytogenic_markers'],
    'complex_karyotype_strict': ['molecular_markers', 'cytogenic_markers'],
    'myc_rearrangement': ['molecular_markers', 'cytogenic_markers'],
    'p53_ihc_gte_50': ['p53_ihc'],
    'blastoid': ['morphologic_variant'],
    'pleomorphic': ['morphologic_variant'],
    'ki67_gt_30': ['ki67_proliferation_index'],
    'ki67_gte_30': ['ki67_proliferation_index'],
    'ki67_gt_50': ['ki67_proliferation_index'],
    'ki67_gte_50': ['ki67_proliferation_index'],
    'high_mipi': ['_mipi_risk'],
    'mipi_c_high': ['_mipi_c_risk'],
    'mipi_c_high_int_high_mipi': ['_mipi_c_risk'],
    'mipi_c_high_int_int_mipi': ['_mipi_c_risk'],
    'lesion_gte_5cm': ['largest_lesion_size'],
    'lesion_gte_7_5cm': ['largest_lesion_size'],
    'lesion_gt_10cm': ['largest_lesion_size'],
    'node_gte_5cm': ['largest_lymph_node_size'],
    'node_gte_7_5cm': ['largest_lymph_node_size'],
    'node_gte_10cm': ['largest_lymph_node_size'],
    'spleen_gte_13cm': ['spleen_size'],
    'spleen_gte_15cm': ['spleen_size'],
    'spleen_gte_20cm': ['spleen_size'],
    'lymphocytosis_gte_50k': ['absolute_lymphocyte_count'],
}

# Per-criterion applicability for bulky disease criteria (see #4400). Same
# semantics as HIGH_RISK_MCL_CRITERIA_SOURCES.
BULKY_DISEASE_CRITERIA_SOURCES = {
    'bulky_lesion_5cm': ['largest_lesion_size'],
    'bulky_lesion_7_5cm': ['largest_lesion_size'],
    'bulky_lesion_10cm': ['largest_lesion_size'],
    'bulky_node_5cm': ['largest_lymph_node_size'],
    'bulky_node_7_5cm': ['largest_lymph_node_size'],
    'bulky_node_10cm': ['largest_lymph_node_size'],
    'bulky_spleen_13cm': ['spleen_size'],
    'bulky_spleen_15cm': ['spleen_size'],
    'bulky_spleen_20cm_gt': ['spleen_size'],
    'bulky_spleen_20cm_gte': ['spleen_size'],
}


class PatientInfoAttributes:
    def __init__(self, patient_info):
        self.patient_info = patient_info
        self.mapping = USER_TO_TRIAL_ATTRS_MAPPING

    def is_attr_blank(self, attr_name):
        is_blank = False
        user_attr_value = self.get_value(attr_name)
        if attr_name in ('genetic_mutations', 'supportive_therapies', 'later_therapies') and user_attr_value == []:
            return True
        if user_attr_value is None:
            is_blank = True

        if attr_name == 'pre_existing_condition_categories':
            if self.patient_info.no_pre_existing_conditions is True:
                return False
            return len(user_attr_value) == 0

        trial_attr_meta = self.mapping[attr_name]

        if attr_name in THERAPY_LINES_ATTRS_UNDERSCORED:
            if self.patient_info.prior_therapy == 'None':
                return False

        if 'computed_value_type' in trial_attr_meta:
            user_attr_type = trial_attr_meta['computed_value_type']
        else:
            user_attr_type = type(self.patient_info.__class__._meta.get_field(attr_name))

        is_under_user_control = 'under_user_control' in trial_attr_meta and trial_attr_meta['under_user_control'] is True
        allow_blank_values = 'allow_blank_values' in trial_attr_meta and trial_attr_meta['allow_blank_values'] is True

        if not is_blank and not allow_blank_values:  # check the value can be blank
            if user_attr_type in ['str', models.fields.TextField] and user_attr_value == '':
                is_blank = True
            if user_attr_type in ['str', models.fields.CharField] and user_attr_value == '':
                is_blank = True
            if user_attr_type in ['int', models.fields.IntegerField] and user_attr_value == 0:
                is_blank = True
            if user_attr_type in ['float', models.fields.DecimalField] and user_attr_value == 0:
                is_blank = True
            if user_attr_type in ['float', models.fields.FloatField] and user_attr_value == 0:
                is_blank = True
            if is_under_user_control and user_attr_value is not True:
                is_blank = True

        return is_blank

    def get_value(self, attr_name):
        if attr_name == 'pre_existing_condition_categories':
            if self.patient_info.no_pre_existing_conditions is True:
                return ['none']
            elif hasattr(self.patient_info, '_pre_existing_condition_categories'):
                return [c.code for c in self.patient_info._pre_existing_condition_categories]
            else:
                return []

        user_attr_value = getattr(self.patient_info, attr_name)

        if attr_name not in self.mapping:
            return user_attr_value

        trial_attr_meta = self.mapping[attr_name]

        if attr_name == 'trial_type':
            try:
                user_attr_value = user_attr_value.code
            except:
                user_attr_value = None

        if attr_name == 'biopsy_grade':
            try:
                user_attr_value = int(user_attr_value)
            except:
                pass

        if "units_convertor" in trial_attr_meta:
            from_unit = getattr(self.patient_info, trial_attr_meta["user_input_units_attr"])
            to_unit = trial_attr_meta["default_unit"]
            user_attr_value = trial_attr_meta["units_convertor"].call(user_attr_value, from_unit, to_unit)

        if "value_overrides" in trial_attr_meta:
            conditional_attr_value = self.get_value(trial_attr_meta["value_overrides"]["conditional_attr_name"])
            if conditional_attr_value == trial_attr_meta["value_overrides"]["conditional_attr_value"]:
                user_attr_value = trial_attr_meta["value_overrides"]["new_value"]

        return user_attr_value

    def get_uln_value(self, attr_name):
        trial_attr_meta = self.mapping[attr_name]
        uln_user_attr_value = None

        if "uln_calculator" in trial_attr_meta:
            user_attr_value = self.get_value(attr_name)

            if user_attr_value:
                uln_user_attr_value = trial_attr_meta["uln_calculator"].call(user_attr_value, self.patient_info)

        return uln_user_attr_value

    def get_user_therapies(self):
        out = []
        if not self.is_attr_blank('supportive_therapies'):
            for item in self.get_value('supportive_therapies'):
                therapy_code = item.get('therapy')
                if therapy_code:
                    out.append(therapy_code)

        if not self.is_attr_blank('later_therapies'):
            for item in self.get_value('later_therapies'):
                therapy_code = item.get('therapy')
                if therapy_code:
                    out.append(therapy_code)

        for attr in THERAPY_LINES_ATTRS_UNDERSCORED:
            if attr != 'later_therapies' and not self.is_attr_blank(attr):
                value = self.get_value(attr)
                if value is not None:
                    out.append(value)

        return list(set(out))

    def get_supportive_therapy_codes(self):
        """Supportive-therapy codes the patient selected.

        Supportive therapies are a distinct matching axis from prior-line
        therapies; these codes gate a trial's supportive_therapies_required /
        supportive_therapies_excluded pair (#4449). Kept separate from
        get_user_therapies(), which mixes supportive codes into the prior-lines
        bag (legacy behaviour, intentionally left unchanged here).
        """
        out = []
        if not self.is_attr_blank('supportive_therapies'):
            for item in self.get_value('supportive_therapies'):
                therapy_code = item.get('therapy')
                if therapy_code:
                    out.append(therapy_code)
        return list(set(out))

    def get_user_therapy_component_ids(self):
        """Return the patient's component concept_ids (CTOMOP-supplied, OMOP mode only).

        Returns None when the field is absent — callers treat None as unknown and
        fail-closed (skip component/type filtering in the queryset; return 'unknown'
        in the matcher).  Returns a non-empty list or [] when the field is present.
        """
        val = self.get_value('therapy_component_ids')
        if val is None:
            return None
        return [str(v) for v in val if v is not None and str(v).strip().isdigit()]

    @cached_property
    def disease_code(self):
        disease = str(self.patient_info.disease).lower()
        if disease == 'multiple myeloma':
            return 'MM'
        elif disease == 'follicular lymphoma':
            return 'FL'
        elif disease == 'breast cancer':
            return 'BC'
        elif disease == 'chronic lymphocytic leukemia':
            return 'CLL'
        elif disease == 'mantle cell lymphoma':
            return 'MCL'
        return None

    @cached_property
    def kappa_lambda_ratio(self):
        if self.patient_info.kappa_flc is None or self.patient_info.lambda_flc is None:
            return None

        if float(self.patient_info.lambda_flc) == 0:
            return None

        return float(self.patient_info.kappa_flc) / float(self.patient_info.lambda_flc)

    @cached_property
    def abnormal_kappa_lambda_ratio(self):
        kappa_lambda_ratio = self.kappa_lambda_ratio

        if kappa_lambda_ratio is None:
            return None

        # from https://github.com/cancerbot-org/cancerbot/issues/858
        return kappa_lambda_ratio < 0.26 or kappa_lambda_ratio > 1.65

    @cached_property
    def meets_crab_c_hypercalcemia(self):
        # Serum calcium > 11 mg/dL (or >1 mg/dL above the ULN), ULN is 10.2-10.5
        if self.is_attr_blank('serum_calcium_level'):
            return None

        value = SerumCalciumConvertor.call(
            value=self.patient_info.serum_calcium_level,
            from_unit=self.patient_info.serum_calcium_level_units,
            to_unit="mg/dL"
        )

        return value > 11

    @cached_property
    def meets_crab_r_renal_insufficiency(self):
        # Creatinine clearance < 40 mL/min or serum creatinine > 2 mg/dL

        # Creatinine clearance < 40 mL/min
        if not self.is_attr_blank('creatinine_clearance_rate'):
            return self.patient_info.creatinine_clearance_rate < 40

        # serum creatinine > 2 mg/dL
        if self.is_attr_blank('serum_creatinine_level'):
            return None

        value = SerumCreatinineConvertor.call(
            value=self.patient_info.serum_creatinine_level,
            from_unit=self.patient_info.serum_creatinine_level_units,
            to_unit="mg/dL"
        )

        return value > 2

    @cached_property
    def meets_crab_a_anemia(self):
        # Hemoglobin < 10 g/dL or >2 g/dL below the normal limit
        # TODO: follow hemoglobin LLN conditions if we can calculate it from https://github.com/cancerbot-org/cancerbot/issues/599#issuecomment-2720729993

        # Hemoglobin < 10 g/dL
        if self.is_attr_blank('hemoglobin_level'):
            return None

        value = BaseConvertor.call(
            value=self.patient_info.hemoglobin_level,
            from_unit=self.patient_info.hemoglobin_level_units,
            to_unit="g/dL"
        )

        return value < 10

    @cached_property
    def meets_crab_b_bone_lesions(self):
        # Bone lesions, any answer matches [1, 2, >2], basically any non-blank value
        # return not self.is_attr_blank('bone_lesions')
        return self.patient_info.bone_lesions is not None and str(self.patient_info.bone_lesions) != ''

    @cached_property
    def meets_crab(self):
        components = [
            self.meets_crab_c_hypercalcemia,
            self.meets_crab_r_renal_insufficiency,
            self.meets_crab_a_anemia,
            self.meets_crab_b_bone_lesions
        ]

        for component in components:
            if component is True:
                return True

        for component in components:
            if component is None:
                return None

        return False

    @cached_property
    def meets_slim_s_sixty_percent(self):
        # S – Sixty percent (≥60%) clonal plasma cells in the bone marrow
        if self.is_attr_blank('clonal_plasma_cells'):
            return None

        return self.patient_info.clonal_plasma_cells >= 60

    @cached_property
    def meets_slim_li_light_chain_ratio(self):
        # Li – Light chain ratio (involved/uninvolved free light chain ratio ≥100)
        if self.kappa_lambda_ratio is None:
            return None

        return self.kappa_lambda_ratio >= 100

    @cached_property
    def meets_slim_m_mri_with_more_than_one(self):
        # M - MRI with more than 1 focal lesion
        # return not self.is_attr_blank('bone_lesions')
        if self.patient_info.bone_lesions is None or str(self.patient_info.bone_lesions) == '':
            return None

        return str(self.patient_info.bone_lesions) != '1'

    @cached_property
    def meets_slim(self):
        components = [
            self.meets_slim_s_sixty_percent,
            self.meets_slim_li_light_chain_ratio,
            self.meets_slim_m_mri_with_more_than_one
        ]

        for component in components:
            if component is True:
                return True

        for component in components:
            if component is None:
                return None

        return False

    @cached_property
    def measurable_disease_imwg_serum_m_protein_is_high(self):
        # Serum M-protein (Yes if ≥ 0.5 g/dL)
        if self.is_attr_blank('monoclonal_protein_serum'):
            return None

        return self.patient_info.monoclonal_protein_serum >= 0.5

    @cached_property
    def measurable_disease_imwg_serum_m_urine_is_high(self):
        # Urine M-protein (Yes if ≥ 200 mg/24h)
        if self.is_attr_blank('monoclonal_protein_urine'):
            return None

        return self.patient_info.monoclonal_protein_urine >= 200

    @cached_property
    def measurable_disease_imwg_kappa_lambda_abnormal_and_high(self):
        # Kappa/Lambda abnormal + (Kappa FLC OR Lambda FLC ≥ 100 mg/L)
        if self.patient_info.kappa_flc is None or self.patient_info.lambda_flc is None:
            return None

        if not self.abnormal_kappa_lambda_ratio:
            return False

        return self.patient_info.kappa_flc >= 10 or self.patient_info.lambda_flc >= 10

    @cached_property
    def measurable_disease_imwg(self):
        components = [
            self.measurable_disease_imwg_serum_m_protein_is_high,
            self.measurable_disease_imwg_serum_m_urine_is_high,
            self.measurable_disease_imwg_kappa_lambda_abnormal_and_high
        ]

        for component in components:
            if component is True:
                return True

        # All components None → no relevant lab data, result is unknown.
        # Avoids the "shows No by default" UX bug (#4143 / #4156).
        if all(c is None for c in components):
            return None

        return False

    @cached_property
    def bmi(self):
        weight = self.patient_info.weight
        height = self.patient_info.height
        if not weight or not height:
            return None

        weight = BaseConvertor.call(weight, self.patient_info.weight_units, "kg")
        height = BaseConvertor.call(height, self.patient_info.height_units, "m")
        return weight / (height ** 2)

    @cached_property
    def hr_status(self):
        if self.patient_info.estrogen_receptor_status == 'er_plus_with_hi_exp' or self.patient_info.progesterone_receptor_status == 'pr_plus_with_hi_exp':
            return 'hr_plus_with_hi_exp'
        elif self.patient_info.estrogen_receptor_status is None or self.patient_info.progesterone_receptor_status is None:
            return None
        elif self.patient_info.estrogen_receptor_status == 'er_plus' or self.patient_info.progesterone_receptor_status == 'pr_plus':
            return 'hr_plus'
        elif self.patient_info.estrogen_receptor_status == 'er_plus_with_low_exp' or self.patient_info.progesterone_receptor_status == 'pr_plus_with_low_exp':
            return 'hr_plus_with_low_exp'
        elif self.patient_info.estrogen_receptor_status == 'er_minus' and self.patient_info.progesterone_receptor_status == 'pr_minus':
            return 'hr_minus'
        return None

    @cached_property
    def tnbc_status(self):
        if self.patient_info.estrogen_receptor_status == 'er_minus' and self.patient_info.progesterone_receptor_status == 'pr_minus' and self.patient_info.her2_status == 'her2_minus':
            return True
        elif self.patient_info.estrogen_receptor_status is None or self.patient_info.progesterone_receptor_status is None or self.patient_info.her2_status is None:
            return False
        return False

    @cached_property
    def treatment_refractory_status(self):

        high_level_outcomes_is_a_refractory = [
            'MRD',  # 'Minimal Residual Disease (MRD) Negativity'
            'SD',  # 'Stable Disease (SD)'
            'PD',  # 'Progressive Disease (PD)'
        ]

        refractory_status_level = [
             "notRefractory",  # "Not Refractory (progression halted)"
             "primaryRefractory",  # "Primary Refractory"
             "secondaryRefractory",  # "Secondary Refractory"
             "multiRefractory",  # "Multi-Refractory"
        ]

        if self.patient_info.prior_therapy == 'None':
            return "notRefractory"

        if self.patient_info.first_line_outcome is None and self.patient_info.second_line_outcome is None and self.patient_info.later_outcome is None:
            return None

        level = 0

        if self.patient_info.first_line_outcome in high_level_outcomes_is_a_refractory:
            level = level + 1

        if self.patient_info.second_line_outcome in high_level_outcomes_is_a_refractory:
            level = level + 1

        if self.patient_info.later_outcome in high_level_outcomes_is_a_refractory:
            level = level + 1

        return refractory_status_level[level]

    @cached_property
    def end_date_from_last_therapy_line(self):
        return self.patient_info.later_date or self.patient_info.second_line_date or self.patient_info.first_line_date

    @cached_property
    def stem_cell_transplant_history_from_therapy_lines(self):
        if str(self.patient_info.prior_therapy).lower() == 'none':
            return None

        therapy_mapping = {
            'asct': 'completedASCT',
            'hdc_asct': 'completedASCT',
            'agsct': 'completedAllogeneicSCT',
        }
        if self.patient_info.later_therapies != []:
            for later_therapy in self.patient_info.later_therapies:
                res = therapy_mapping.get(later_therapy.get('therapy'))
                if res:
                    return res
            return None
        if self.patient_info.later_therapy in therapy_mapping:
            return therapy_mapping.get(self.patient_info.later_therapy)
        if self.patient_info.second_line_therapy in therapy_mapping:
            return therapy_mapping.get(self.patient_info.second_line_therapy)
        if self.patient_info.first_line_therapy in therapy_mapping:
            return therapy_mapping.get(self.patient_info.first_line_therapy)

        return None

    @cached_property
    def renal_adequacy_status(self):
        if self.patient_info.estimated_glomerular_filtration_rate and self.patient_info.estimated_glomerular_filtration_rate < 60:
            return False

        if self.patient_info.creatinine_clearance_rate and self.patient_info.creatinine_clearance_rate < 60:
            return False

        if self.patient_info.estimated_glomerular_filtration_rate is None or self.patient_info.creatinine_clearance_rate is None:
            return False

        return True

    @cached_property
    def hepatic_adequacy_status(self):
        bilirubin_total_uln = self.get_uln_value('serum_bilirubin_level_total')
        ast_uln = self.get_uln_value('liver_enzyme_levels_ast')
        alt_uln = self.get_uln_value('liver_enzyme_levels_alt')

        if bilirubin_total_uln is None or ast_uln is None or alt_uln is None:
            return False

        return (
            bilirubin_total_uln <= HEPATIC_ADEQUACY_BILIRUBIN_TOTAL_ULN_MAX
            and ast_uln <= HEPATIC_ADEQUACY_AST_ULN_MAX
            and alt_uln <= HEPATIC_ADEQUACY_ALT_ULN_MAX
        )

    @cached_property
    def haematological_adequacy_status(self):
        anc = self.get_value('absolute_neutrophile_count')
        platelet_count = self.get_value('platelet_count')
        hemoglobin = self.get_value('hemoglobin_level')

        if anc is None or platelet_count is None or hemoglobin is None:
            return False

        return (
            float(anc) >= HAEMATOLOGICAL_ADEQUACY_ANC_MIN
            and float(platelet_count) >= HAEMATOLOGICAL_ADEQUACY_PLATELET_MIN
            and float(hemoglobin) >= HAEMATOLOGICAL_ADEQUACY_HEMOGLOBIN_MIN
        )

    @cached_property
    def refractory_status_from_therapy_lines(self):
        refractory_outcome_ids = ('MRD', 'SD', 'PD')

        if self.patient_info.prior_therapy == 'More than two lines of therapy':
            if self.patient_info.later_outcome in refractory_outcome_ids:
                return 'multiRefractory'
            elif self.patient_info.later_outcome is not None:
                return 'notRefractory'
            else:
                return None

        if self.patient_info.prior_therapy == 'Two lines':
            if self.patient_info.second_line_outcome in refractory_outcome_ids:
                return 'secondaryRefractory'
            elif self.patient_info.second_line_outcome is not None:
                return 'notRefractory'
            else:
                return None

        if self.patient_info.prior_therapy == 'One line':
            if self.patient_info.first_line_outcome in refractory_outcome_ids:
                return 'primaryRefractory'
            elif self.patient_info.first_line_outcome is not None:
                return 'notRefractory'
            else:
                return None

        return None

    @cached_property
    def meets_meas_or_bone_status(self):
        if self.patient_info.measurable_disease_by_recist_status is True or self.patient_info.bone_only_metastasis_status is True:
            return True
        elif self.patient_info.measurable_disease_by_recist_status is None or self.patient_info.bone_only_metastasis_status is None:
            return None
        return False

    @cached_property
    def tp53_disruption(self):
        """
        TP53 Disruption = True if patient has:
        - del17p13 in cytogenic_markers OR
        - del17p13 in molecular_markers OR
        - tp53Mutation in molecular_markers
        """
        cytogenic = self.patient_info.cytogenic_markers or ''
        molecular = self.patient_info.molecular_markers or ''

        cytogenic_list = [m.strip() for m in cytogenic.split(',') if m.strip()]
        molecular_list = [m.strip() for m in molecular.split(',') if m.strip()]

        if 'del17p13' in cytogenic_list or 'del17p13' in molecular_list or 'tp53Mutation' in molecular_list:
            return True

        return False

    def profile_completeness(self) -> int | None:
        disease_code = self.disease_code
        relevant_attrs = []
        for attr_name, meta in self.mapping.items():
            attr_disease = meta.get("disease")
            if attr_disease is None:
                relevant_attrs.append(attr_name)
            elif disease_code is not None:
                diseases = attr_disease if isinstance(attr_disease, list) else [attr_disease]
                if disease_code in diseases:
                    relevant_attrs.append(attr_name)
        if not relevant_attrs:
            return None
        filled = sum(1 for attr in relevant_attrs if not self.is_attr_blank(attr))
        return round(filled / len(relevant_attrs) * 100)

    def cleanup(self):
        if self.patient_info.prior_therapy == 'More than two lines of therapy':
            return

        if self.patient_info.prior_therapy == 'Two lines':
            self.patient_info.later_therapies = []
            self.patient_info.later_therapy = None
            self.patient_info.later_date = None
            self.patient_info.later_outcome = None
            return

        if self.patient_info.prior_therapy == 'One line':
            self.patient_info.later_therapies = []
            self.patient_info.later_therapy = None
            self.patient_info.later_date = None
            self.patient_info.later_outcome = None
            self.patient_info.second_line_therapy = None
            self.patient_info.second_line_date = None
            self.patient_info.second_line_outcome = None
            return

        if self.patient_info.prior_therapy == 'None':
            self.patient_info.later_therapies = []
            self.patient_info.later_date = None
            self.patient_info.later_outcome = None
            self.patient_info.second_line_therapy = None
            self.patient_info.second_line_date = None
            self.patient_info.second_line_outcome = None
            self.patient_info.first_line_therapy = None
            self.patient_info.first_line_date = None
            self.patient_info.first_line_outcome = None

            self.patient_info.stem_cell_transplant_history = 'None'
            return

    # ---------------------------------------------------------------------
    # MCL derivations (#41).
    # Ported from CB patient_info_attributes.py; bulky_disease_criteria and
    # high_risk_mcl_criteria both return a comma-joined string (or None), and
    # field names / codes match CB exactly.
    # MIPI: Hoster et al. 2008 (Blood 111:558-565).
    # MIPI-C: categorical MIPI x Ki-67 table (Hoster et al. 2014, ASH 2014
    # abstract — full JCO 2016 publication uses a continuous variant).
    # See follow-up issue for clinical-cutoff verification.
    # ---------------------------------------------------------------------

    @cached_property
    def mipi_risk(self):
        pi = self.patient_info
        age = pi.patient_age
        ecog = pi.ecog_performance_status
        wbc = pi.white_blood_cell_count
        ldh = pi.lactate_dehydrogenase_level

        if any(v is None for v in (age, ecog, wbc, ldh)):
            return None
        if float(wbc) <= 0 or float(ldh) <= 0:
            return None

        # The MIPI formula takes WBC as the absolute count per microliter (e.g.
        # 7000 -> log10(7000)), per Hoster et al. 2008. The previous code
        # converted to cells/L and divided by 1e9, i.e. used the x10^9/L value
        # (7 -> log10(7)), under-scoring this term by log10(1000) = 3 (~2.82
        # points) and systematically under-classifying MCL risk (CB #4421).
        # ULN proxy 250 mirrors CB; revisit if a per-lab ULN becomes available.
        wbc_per_ul = float(BaseConvertor.call(
            wbc, pi.white_blood_cell_count_units or 'CELLS/L', 'CELLS/UL'
        ))
        score = (
            0.03535 * age
            + 0.6978 * (1 if ecog >= 2 else 0)
            + 1.367 * log10(float(ldh) / 250)
            + 0.9393 * log10(wbc_per_ul)
        )

        if score < 5.7:
            return 'low'
        elif score < 6.5:
            return 'intermediate'
        return 'high'

    @cached_property
    def mipi_c_risk(self):
        mipi = self.mipi_risk
        ki67 = self.patient_info.ki67_proliferation_index

        if mipi is None or ki67 is None:
            return None

        # 4-tier table (Hoster 2014). Threshold Ki-67 = 30%.
        if mipi == 'low':
            return 'low' if ki67 < 30 else 'low_intermediate'
        if mipi == 'intermediate':
            return 'low_intermediate' if ki67 < 30 else 'high_intermediate'
        # high
        return 'high_intermediate' if ki67 < 30 else 'high'

    @cached_property
    def bulky_disease_criteria(self):
        """Comma-joined codes for each bulky-disease criterion the patient meets.

        Multiple thresholds can fire at once (e.g. a 12cm lesion produces
        bulky_lesion_5cm, _7_5cm, and _10cm) so trials requiring any one of
        them all match. Returns None when no inputs or no thresholds met.
        """
        pi = self.patient_info
        criteria = []

        lesion = pi.largest_lesion_size
        if lesion is not None:
            if lesion >= 5:
                criteria.append('bulky_lesion_5cm')
            if lesion >= 7.5:
                criteria.append('bulky_lesion_7_5cm')
            if lesion >= 10:
                criteria.append('bulky_lesion_10cm')

        node = pi.largest_lymph_node_size
        if node is not None:
            if node >= 5:
                criteria.append('bulky_node_5cm')
            if node >= 7.5:
                criteria.append('bulky_node_7_5cm')
            if node >= 10:
                criteria.append('bulky_node_10cm')

        spleen = pi.spleen_size
        if spleen is not None:
            if spleen > 13:
                criteria.append('bulky_spleen_13cm')
            if spleen > 15:
                criteria.append('bulky_spleen_15cm')
            if spleen > 20:
                criteria.append('bulky_spleen_20cm_gt')
            if spleen >= 20:
                criteria.append('bulky_spleen_20cm_gte')

        return ','.join(criteria) if criteria else None

    @cached_property
    def high_risk_mcl_criteria(self):
        """Compute which high-risk MCL criteria the patient meets."""
        pi = self.patient_info
        criteria = []

        molecular = pi.molecular_markers or ''
        molecular_list = [m.strip() for m in molecular.split(',') if m.strip()]

        if 'tp53Mutation' in molecular_list:
            criteria.append('tp53_mutation')
        if 'kmt2dMutation' in molecular_list:
            criteria.append('kmt2d_mutation')
        if 'nsd2Mutation' in molecular_list:
            criteria.append('nsd2_mutation')
        if 'notch1or2Mutations' in molecular_list:
            # The patient option is a combined NOTCH1/NOTCH2 selection; we cannot
            # tell which gene is mutated, so emit a single combined code rather
            # than both specific codes (#4406). A trial requiring NOTCH1 or NOTCH2
            # specifically must not match this ambiguous input.
            criteria.append('notch1_or_2')
        if 'cdkn2aAlteration' in molecular_list:
            criteria.append('cdkn2a_alteration')
        if 'smarca4Mutation' in molecular_list:
            criteria.append('smarca4_mutation')
        if 'ccnd1Alteration' in molecular_list:
            criteria.append('ccnd1_alteration')
        if 'bcl2Amplification' in molecular_list:
            criteria.append('bcl2_amplification')

        cytogenic = pi.cytogenic_markers or ''
        cytogenic_list = [m.strip() for m in cytogenic.split(',') if m.strip()]
        all_markers = set(cytogenic_list + molecular_list)

        if 'del17p13' in all_markers:
            criteria.append('del17p')
        # complexKaryotypeExcludingT1114 is the strict form (>=3 abnormalities in
        # addition to t(11;14), per NCT06357676); it also satisfies the plain
        # complex_karyotype criterion (#4406).
        if 'complexKaryotype' in all_markers or 'complexKaryotypeExcludingT1114' in all_markers:
            criteria.append('complex_karyotype')
        if 'complexKaryotypeExcludingT1114' in all_markers:
            criteria.append('complex_karyotype_strict')
        if 'mycRearrangements' in all_markers:
            criteria.append('myc_rearrangement')

        p53 = pi.p53_ihc
        if p53 is not None and p53 >= 50:
            criteria.append('p53_ihc_gte_50')

        morphology = pi.morphologic_variant
        if morphology == 'blastoid':
            criteria.append('blastoid')
        elif morphology == 'pleomorphic':
            criteria.append('pleomorphic')

        ki67 = pi.ki67_proliferation_index
        if ki67 is not None:
            if ki67 > 30:
                criteria.append('ki67_gt_30')
            if ki67 >= 30:
                criteria.append('ki67_gte_30')
            if ki67 > 50:
                criteria.append('ki67_gt_50')
            if ki67 >= 50:
                criteria.append('ki67_gte_50')

        mipi = self.mipi_risk
        mipi_c = self.mipi_c_risk

        if mipi == 'high':
            criteria.append('high_mipi')

        if mipi_c == 'high':
            criteria.append('mipi_c_high')
        elif mipi_c == 'high_intermediate':
            if mipi == 'high':
                criteria.append('mipi_c_high_int_high_mipi')
            elif mipi == 'intermediate':
                criteria.append('mipi_c_high_int_int_mipi')

        lesion = pi.largest_lesion_size
        if lesion is not None:
            if lesion >= 5:
                criteria.append('lesion_gte_5cm')
            if lesion >= 7.5:
                criteria.append('lesion_gte_7_5cm')
            if lesion > 10:
                criteria.append('lesion_gt_10cm')

        node = pi.largest_lymph_node_size
        if node is not None:
            if node >= 5:
                criteria.append('node_gte_5cm')
            if node >= 7.5:
                criteria.append('node_gte_7_5cm')
            if node >= 10:
                criteria.append('node_gte_10cm')

        spleen = pi.spleen_size
        if spleen is not None:
            if spleen >= 13:
                criteria.append('spleen_gte_13cm')
            if spleen >= 15:
                criteria.append('spleen_gte_15cm')
            if spleen >= 20:
                criteria.append('spleen_gte_20cm')

        alc = pi.absolute_lymphocyte_count
        if alc is not None and alc >= 50000:
            criteria.append('lymphocytosis_gte_50k')

        return ','.join(criteria) if criteria else None

    def _criteria_source_blank(self, source):
        """Whether a single source token backing a derived criterion is blank."""
        if source == '_mipi_risk':
            return self.mipi_risk is None
        if source == '_mipi_c_risk':
            return self.mipi_c_risk is None
        if source == '_notch_specific':
            # NOTCH1/NOTCH2 cannot be disambiguated while the combined option is
            # selected, so a gene-specific code stays undeterminable then.
            molecular = self.patient_info.molecular_markers or ''
            return 'notch1or2Mutations' in [m.strip() for m in molecular.split(',')]
        value = getattr(self.patient_info, source, None)
        return value is None or value == ''

    def _criteria_unknown_codes(self, required_codes, sources_map):
        """Subset of required_codes that cannot be determined from patient data.

        A code is unknown when ANY source field it can be derived from is blank:
        its absence is only confirmed once every potential source is answered.
        Codes absent from sources_map are treated as determinable (never unknown)
        so an unrecognised required code does not silently inflate the unknown
        count.
        """
        unknown = set()
        for code in required_codes:
            sources = sources_map.get(code)
            if not sources:
                continue
            if any(self._criteria_source_blank(s) for s in sources):
                unknown.add(code)
        return unknown

    def high_risk_mcl_criteria_unknown_codes(self, required_codes):
        return self._criteria_unknown_codes(required_codes, HIGH_RISK_MCL_CRITERIA_SOURCES)

    def bulky_disease_criteria_unknown_codes(self, required_codes):
        return self._criteria_unknown_codes(required_codes, BULKY_DISEASE_CRITERIA_SOURCES)

    def high_risk_mcl_criteria_all_unknown_codes(self):
        """Every high-risk code whose source data is blank for this patient.

        The whole-vocabulary unknown set (#4416): the trials-list SQL counter
        intersects it per trial to reproduce the matcher's per-criterion verdict
        instead of the aggregate is_attr_blank check.
        """
        return self._criteria_unknown_codes(HIGH_RISK_MCL_CRITERIA_SOURCES.keys(), HIGH_RISK_MCL_CRITERIA_SOURCES)

    def bulky_disease_criteria_all_unknown_codes(self):
        return self._criteria_unknown_codes(BULKY_DISEASE_CRITERIA_SOURCES.keys(), BULKY_DISEASE_CRITERIA_SOURCES)
