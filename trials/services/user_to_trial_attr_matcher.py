import itertools
import datetime as dt

from trials.enums import PriorTherapyLines
from trials.services.patient_info.configs import (
    USER_TO_TRIAL_ATTRS_MAPPING,
    THERAPY_LINES_ATTRS_UNDERSCORED,
    ATTR_MAPPING_TYPE_COMPUTED, SCT_HISTORY_EXCLUDED_MAPPING,
    sct_value_is_none,
)
from trials.services.patient_info.genetic_mutations import GeneticMutations
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes
from trials.services.patient_info.patient_info_flipi_score import PatientInfoFlipyScore
from trials.services.utils import get_overlap


def min_max_match(min_val, max_val, value, value_is_blank):
    if min_val is None and max_val is None:
        return None
    elif value_is_blank:
        return 'unknown'
    elif min_val is not None and value < min_val:
        return 'not_matched'
    elif max_val is not None and value > max_val:
        return 'not_matched'
    else:
        return 'matched'


class UserToTrialAttrMatcher:
    def __init__(self, trial, patient_info):
        self.trial = trial
        self.patient_info = patient_info
        self.mapping = USER_TO_TRIAL_ATTRS_MAPPING
        self.patient_info_attr = PatientInfoAttributes(patient_info)
        self.disease_code = self.get_disease_code_from_trial(trial)

    def get_disease_code_from_trial(self, trial):
        disease = str(trial.disease).lower()
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

    def trial_match_status(self):
        out = {}
        for attr, trial_attr_meta in self.mapping.items():
            if "disease" in trial_attr_meta and (self.disease_code is None or self.disease_code not in trial_attr_meta["disease"]):
                continue
            out[attr] = self.attr_match_status(attr)

        if 'not_matched' in out.values():
            return 'not_eligible'
        elif 'unknown' in out.values():
            return 'potential'
        else:
            return 'eligible'

    def trial_match_score(self):
        eligible_count = 0
        all_count = 0

        for attr, trial_attr_meta in self.mapping.items():
            if "disease" in trial_attr_meta and (self.disease_code is None or self.disease_code not in trial_attr_meta["disease"]):
                continue
            status = self.attr_match_status(attr)
            all_count = all_count + 1
            if status == 'not_matched':
                return 0
            elif status == 'matched':
                eligible_count = eligible_count + 1

        if all_count == 0:
            return 0
        return int(float(eligible_count) * 100 / float(all_count))

    def is_patient_info_attr_blank(self, patient_info_attr):
        return self.patient_info_attr.is_attr_blank(patient_info_attr)

    def therapy_related_things_mismatch_status(self):
        if self.patient_info.prior_therapy is None or self.patient_info.prior_therapy == '':
            return 'unknown'

        if self.patient_info.prior_therapy == 'More than two lines of therapy':
            if self.patient_info.later_therapies == [] or not self.patient_info.later_therapy or not self.patient_info.second_line_therapy or not self.patient_info.first_line_therapy:
                return 'unknown'
            else:
                return 'not_matched'

        if self.patient_info.prior_therapy == 'Two lines':
            if not self.patient_info.second_line_therapy or not self.patient_info.first_line_therapy:
                return 'unknown'
            else:
                return 'not_matched'

        if self.patient_info.prior_therapy == 'One line':
            if not self.patient_info.first_line_therapy:
                return 'unknown'
            else:
                return 'not_matched'

        return 'not_matched'

    def therapy_related_things_match_status(self):
        therapies = []
        therapy_components = []
        therapy_components_to_therapy = {}
        therapy_types = []
        therapy_types_to_therapy = {}
        therapy_codes = self.patient_info_attr.get_user_therapies()

        mismatch_status = self.therapy_related_things_mismatch_status()

        if len(therapy_codes) > 0:
            from trials.models import Therapy

            therapies = Therapy.objects.filter(code__in=therapy_codes)
            for therapy in therapies:
                for component in therapy.components.order_by('id').all():
                    if component not in therapy_components:
                        therapy_components.append(component)
                        therapy_components_to_therapy[component.code] = component.title

                        for category in component.categories.all():
                            if category not in therapy_types:
                                therapy_types.append(category)
                                therapy_types_to_therapy[category.code] = category.title

        therapies = {x.code: x.title for x in therapies}

        def match_required(trial_values, matching_values, mismatch_status):
            overlap = get_overlap(trial_values, matching_values.keys())
            if trial_values == []:
                status = 'matched'
            else:
                status = 'matched' if len(overlap) > 0 else mismatch_status

            values = []
            for k, v in matching_values.items():
                if k in overlap:
                    values.append(f'**{v}**')
                else:
                    values.append(v)

            return {
                "status": status,
                "values": sorted(list(set(values)))
            }

        def match_excluded(trial_values, matching_values):
            overlap = get_overlap(trial_values, matching_values.keys())
            if trial_values == []:
                status = 'matched'
            else:
                status = 'not_matched' if len(overlap) > 0 else 'matched'

            values = []
            for k, v in matching_values.items():
                if k in overlap:
                    values.append(f'**{v}**')
                else:
                    values.append(v)

            return {
                "status": status,
                "values": sorted(list(set(values)))
            }

        out = {
            "therapiesRequired": match_required(self.trial.therapies_required, therapies, mismatch_status),
            "therapiesExcluded": match_excluded(self.trial.therapies_excluded, therapies),
            "therapyTypesRequired": match_required(self.trial.therapy_types_required, therapy_types_to_therapy, mismatch_status),
            "therapyTypesExcluded": match_excluded(self.trial.therapy_types_excluded, therapy_types_to_therapy),
            "therapyComponentsRequired": match_required(self.trial.therapy_components_required, therapy_components_to_therapy, mismatch_status),
            "therapyComponentsExcluded": match_excluded(self.trial.therapy_components_excluded, therapy_components_to_therapy),
        }

        return out

    def attr_match_status(self, patient_info_attr):
        patient_info_attr_value = self.patient_info_attr.get_value(patient_info_attr)
        patient_info_attr_is_blank = self.is_patient_info_attr_blank(patient_info_attr)

        trial_attr_meta = self.mapping[patient_info_attr]
        trial_attr_name = trial_attr_meta["attr"]

        def match_therapy_things(values, required_list, excluded_list, has_no_prior_therapy):
            if required_list == [] and excluded_list == []:
                return 'matched'

            if values is None or values == '':
                if not has_no_prior_therapy:
                    return 'unknown'

            overlap = get_overlap(values or [], excluded_list)
            if len(overlap) > 0:
                return 'not_matched'

            overlap = get_overlap(values or [], required_list)
            if len(required_list) > 0 and len(overlap) == 0:
                return 'not_matched'

            return 'matched'

        def match_therapy_related_things(values, has_no_prior_therapy):
            results = []
            res = match_therapy_things(values, self.trial.therapies_required, self.trial.therapies_excluded, has_no_prior_therapy)
            if res == 'not_matched':
                return res
            results.append(res)

            therapies = None
            if values:
                from trials.models import Therapy
                therapies = Therapy.objects.filter(code__in=values).all()

            if therapies and therapies.count() > 0:
                from trials.models import TherapyComponent
                from trials.models import TherapyComponentCategory

                components = TherapyComponent.objects.filter(therapycomponentconnection__therapy__in=therapies).order_by('id').all()
                component_codes = [x.code for x in components]

                categories = TherapyComponentCategory.objects.filter(therapycomponentcategoryconnection__component__in=components).order_by('id').all()
                therapy_types = [x.code for x in categories]
            else:
                component_codes = None
                therapy_types = None

            res = match_therapy_things(component_codes, self.trial.therapy_components_required, self.trial.therapy_components_excluded, has_no_prior_therapy)
            if res == 'not_matched':
                return res
            results.append(res)

            res = match_therapy_things(therapy_types, self.trial.therapy_types_required, self.trial.therapy_types_excluded, has_no_prior_therapy)
            if res == 'not_matched':
                return res
            results.append(res)

            if 'unknown' in results:
                return 'unknown'

            return 'matched'

        prior_therapy = self.patient_info_attr.get_value('prior_therapy')
        has_no_prior_therapy = prior_therapy in ["None"]

        if "custom_search" in trial_attr_meta and trial_attr_meta["custom_search"] is True:
            if patient_info_attr == "pre_existing_condition_categories":
                trial_attr_value = getattr(self.trial, trial_attr_name)
                if trial_attr_value is None or trial_attr_value == []:
                    return 'matched'
                elif patient_info_attr_is_blank:
                    return 'unknown'
                elif len(get_overlap(patient_info_attr_value, trial_attr_value)) > 0:
                    return 'not_matched'
                else:
                    return 'matched'

            elif patient_info_attr == "stem_cell_transplant_history":
                trial_attr_sct_history_required = getattr(self.trial, 'stem_cell_transplant_history_required')
                trial_attr_sct_history_excluded = getattr(self.trial, 'stem_cell_transplant_history_excluded')

                # See sct_value_is_none() — tolerates both storage shapes
                # ('None' bare string from signal cleanup; ['None'] list
                # from the multiselect) and whitespace/casing variants
                # (#4333, #4340).
                user_has_no_sct = sct_value_is_none(patient_info_attr_value)
                if not trial_attr_sct_history_required and not trial_attr_sct_history_excluded:
                    return 'matched'

                if patient_info_attr_is_blank:
                    return 'unknown'

                if trial_attr_sct_history_required and user_has_no_sct:
                    return 'not_matched'

                def has_mapped_items(pi_vals, excluded_list):
                    mapped_items = []
                    if sct_value_is_none(pi_vals):
                        return False
                    if not isinstance(pi_vals, (list, tuple)):
                        pi_vals = [pi_vals]

                    for pi_val in pi_vals:
                        for rec in SCT_HISTORY_EXCLUDED_MAPPING.get(pi_val, [pi_val]):
                            mapped_items.append(rec)

                    mapped_items = list(set(mapped_items))

                    for item in mapped_items:
                        if item in excluded_list:
                            return True

                    return False

                if not trial_attr_sct_history_excluded:
                    return 'matched'
                elif has_mapped_items(patient_info_attr_value, trial_attr_sct_history_excluded):
                    return 'not_matched'
                else:
                    return 'matched'

            elif patient_info_attr == "concomitant_medications":
                concomitant_medications_excluded = getattr(self.trial, 'concomitant_medications_excluded')
                concomitant_medications_washout_period_duration = getattr(self.trial, 'concomitant_medications_washout_period_duration')

                user_has_no_cm = str(patient_info_attr_value).lower() == 'none'
                if not concomitant_medications_excluded:
                    return 'matched'

                if patient_info_attr_is_blank:
                    return 'unknown'

                if concomitant_medications_washout_period_duration and user_has_no_cm:
                    return 'matched'

                def has_mapped_items(pi_vals, excluded_list):
                    if not isinstance(pi_vals, (list, tuple)):
                        pi_vals = [pi_vals]

                    if pi_vals == ['None']:
                        return False

                    for item in pi_vals:
                        if item in excluded_list:
                            return True

                    return False

                if not has_mapped_items(patient_info_attr_value, concomitant_medications_excluded):
                    return 'matched'

                if not concomitant_medications_washout_period_duration:
                    return 'not_matched'

                concomitant_medication_date = self.patient_info_attr.get_value('concomitant_medication_date')
                if not concomitant_medication_date:
                    return 'not_matched'

                current_washout_period_in_days = (dt.date.today() - concomitant_medication_date).days
                return 'matched' if concomitant_medications_washout_period_duration < current_washout_period_in_days else 'not_matched'

            elif patient_info_attr == "stage":
                if self.trial.stages == []:
                    return 'matched'
                elif patient_info_attr_is_blank:
                    return 'unknown'
                elif len(get_overlap([patient_info_attr_value], self.trial.stages)) > 0:
                    return 'matched'
                else:
                    return 'not_matched'

            elif patient_info_attr == "disease":
                trial_attr_value = getattr(self.trial, trial_attr_name)
                if trial_attr_value is None or trial_attr_value == '':
                    return 'matched'
                elif patient_info_attr_is_blank or patient_info_attr_value == '':
                    return 'unknown'
                elif str(patient_info_attr_value).lower() in str(trial_attr_value).lower():
                    return 'matched'
                else:
                    return 'not_matched'

            elif patient_info_attr == "plasma_cell_leukemia":
                attr_no_pcl_required = 'no_plasma_cell_leukemia_required'
                attr_pcl_required = 'plasma_cell_leukemia_required'

                trial_attr_no_pcl_required = getattr(self.trial, attr_no_pcl_required)
                trial_attr_pcl_required = getattr(self.trial, attr_pcl_required)

                if trial_attr_no_pcl_required is not True and trial_attr_pcl_required is not True:
                    return 'matched'

                if patient_info_attr_value is None:
                    return 'unknown'

                if patient_info_attr_value is True and trial_attr_no_pcl_required is not True:
                    return 'matched'
                elif patient_info_attr_value is False and trial_attr_pcl_required is not True:
                    return 'matched'
                else:
                    return 'not_matched'

            elif patient_info_attr == "progression":
                trial_attr_active_required = getattr(self.trial, 'disease_progression_active_required')
                trial_attr_smoldering_required = getattr(self.trial, 'disease_progression_smoldering_required')

                if trial_attr_active_required is not True and trial_attr_smoldering_required is not True:
                    return 'matched'

                # "Unknown" is sent from the UI as an empty string (see ValueOptions.progressions)
                if patient_info_attr_value is None or patient_info_attr_value == '':
                    return 'unknown'

                if patient_info_attr_value == 'active' and trial_attr_smoldering_required is not True:
                    return 'matched'
                elif patient_info_attr_value == 'smoldering' and trial_attr_active_required is not True:
                    return 'matched'
                else:
                    return 'not_matched'

            elif patient_info_attr == "last_treatment":
                trial_attr_value = getattr(self.trial, 'washout_period_duration')
                if trial_attr_value is None:
                    return 'matched'

                if has_no_prior_therapy:
                    return 'matched'

                if patient_info_attr_value is None:
                    return 'unknown'

                current_washout_period_in_days = (dt.date.today() - patient_info_attr_value).days

                return 'matched' if trial_attr_value < current_washout_period_in_days else 'not_matched'

            elif patient_info_attr == "treatment_refractory_status":
                trial_attr_not_refractory_required = getattr(self.trial, 'not_refractory_required')
                trial_attr_refractory_required = getattr(self.trial, 'refractory_required')

                if trial_attr_not_refractory_required is not True and trial_attr_refractory_required is not True:
                    return 'matched'

                if not patient_info_attr_value:
                    return 'unknown'

                #  "notRefractory": "Not Refractory (progression halted)",
                #  "primaryRefractory": "Primary Refractory",
                #  "secondaryRefractory": "Secondary Refractory",
                #  "multiRefractory": "Multi-Refractory",
                if patient_info_attr_value in ['notRefractory', 'Not Refractory (progression halted)']:
                    return 'matched' if trial_attr_refractory_required is not True else 'not_matched'
                elif trial_attr_not_refractory_required is not True:
                    return 'matched'
                else:
                    return 'not_matched'

            elif patient_info_attr == "tumor_grade":
                attr_min_name = 'tumor_grade_min'
                attr_max_name = 'tumor_grade_max'
                trial_attr_value_min = getattr(self.trial, attr_min_name)
                trial_attr_value_max = getattr(self.trial, attr_max_name)

                if not patient_info_attr_is_blank:
                    if isinstance(patient_info_attr_value, int) or str(patient_info_attr_value).isdigit():
                        patient_info_attr_value = int(patient_info_attr_value)
                    else:
                        from trials.services.value_options import ValueOptions
                        mapping = {}
                        for k, v in ValueOptions().tumor_grades().items():
                            mapping[v.lower()] = k

                        tumor_grade_val = str(patient_info_attr_value).lower()
                        patient_info_attr_value = mapping.get(tumor_grade_val, None)

                if trial_attr_value_min is None and trial_attr_value_max is None:
                    return 'matched'
                elif patient_info_attr_is_blank:
                    return 'unknown'
                elif trial_attr_value_min is not None and patient_info_attr_value < trial_attr_value_min:
                    return 'not_matched'
                elif trial_attr_value_max is not None and patient_info_attr_value > trial_attr_value_max:
                    return 'not_matched'
                else:
                    return 'matched'

            elif patient_info_attr == "flipi_score_options":
                attr_min_name = 'flipi_score_min'
                attr_max_name = 'flipi_score_max'
                trial_attr_value_min = getattr(self.trial, attr_min_name)
                trial_attr_value_max = getattr(self.trial, attr_max_name)

                if not patient_info_attr_is_blank:
                    patient_info_attr_value = PatientInfoFlipyScore.scope_by_options(patient_info_attr_value)

                if trial_attr_value_min is None and trial_attr_value_max is None:
                    return 'matched'
                elif patient_info_attr_is_blank:
                    return 'unknown'
                elif trial_attr_value_min is not None and patient_info_attr_value < trial_attr_value_min:
                    return 'not_matched'
                elif trial_attr_value_max is not None and patient_info_attr_value > trial_attr_value_max:
                    return 'not_matched'
                else:
                    return 'matched'

            elif patient_info_attr == "prior_therapy":
                # "None", "One line", "Two lines", "More than two lines of therapy"
                raw_value = str(patient_info_attr_value).lower()

                if raw_value == 'none':
                    patient_info_attr_value = 0
                elif raw_value == 'one line':
                    patient_info_attr_value = 1
                elif raw_value == 'two lines':
                    patient_info_attr_value = 2
                elif raw_value == 'more than two lines of therapy':
                    patient_info_attr_value = 3
                else:
                    patient_info_attr_value = None
                    patient_info_attr_is_blank = True

                attr_min_name = 'therapy_lines_count_min'
                attr_max_name = 'therapy_lines_count_max'
                trial_attr_value_min = getattr(self.trial, attr_min_name)
                trial_attr_value_max = getattr(self.trial, attr_max_name)

                if trial_attr_value_min is None and trial_attr_value_max is None:
                    return 'matched'
                elif patient_info_attr_is_blank:
                    return 'unknown'
                elif trial_attr_value_min is not None and patient_info_attr_value < trial_attr_value_min:
                    return 'not_matched'
                elif trial_attr_value_max is not None and patient_info_attr_value > trial_attr_value_max:
                    return 'not_matched'
                else:
                    return 'matched'

            elif patient_info_attr in [*THERAPY_LINES_ATTRS_UNDERSCORED, 'supportive_therapies']:
                if patient_info_attr == 'first_line_therapy':  # calc things for just one line of therapy
                    therapies = self.patient_info_attr.get_user_therapies()
                    if len(therapies) == 0:
                        therapies = None
                    return match_therapy_related_things(therapies, has_no_prior_therapy)
                else:
                    return 'matched'

            elif patient_info_attr == 'genetic_mutations':
                trial_mutation_genes_required = getattr(self.trial, 'mutation_genes_required')
                trial_mutation_variants_required = getattr(self.trial, 'mutation_variants_required')
                trial_mutation_origins_required = getattr(self.trial, 'mutation_origins_required')
                trial_mutation_interpretations_required = getattr(self.trial, 'mutation_interpretations_required')
                pi_genes = GeneticMutations.mutation_genes(patient_info_attr_value)
                pi_variants = GeneticMutations.mutation_variants(patient_info_attr_value)
                pi_origins = GeneticMutations.mutation_origins(patient_info_attr_value)
                pi_interpretations = GeneticMutations.mutation_interpretations(patient_info_attr_value)

                if trial_mutation_genes_required == [] and trial_mutation_variants_required == [] and trial_mutation_origins_required == [] and trial_mutation_interpretations_required == []:
                    return 'matched'
                elif patient_info_attr_is_blank:
                    return 'unknown'
                else:
                    # match genes
                    if len(trial_mutation_genes_required) > 0 and len(get_overlap(pi_genes, trial_mutation_genes_required)) == 0:
                        return 'not_matched'
                    # match variants
                    elif len(trial_mutation_variants_required) > 0 and len(get_overlap(pi_variants, trial_mutation_variants_required)) == 0:
                        return 'not_matched'
                    # match origins
                    elif len(trial_mutation_origins_required) > 0 and len(get_overlap(pi_origins, trial_mutation_origins_required)) == 0:
                        return 'not_matched'
                    # match interpretations
                    elif len(trial_mutation_interpretations_required) > 0 and len(get_overlap(pi_interpretations, trial_mutation_interpretations_required)) == 0:
                        return 'not_matched'
                    return 'matched'

            elif trial_attr_meta["type"] == ATTR_MAPPING_TYPE_COMPUTED:
                matching_results = []

                def get_matching_result(tr_attr_name, uvalue_func):
                    # TODO: it's a bit hackish, but should be OK as long as we follow internal naming convention
                    is_exclude = '_excluded' in trial_subattr_name

                    tr_attr_value = getattr(self.trial, tr_attr_name)
                    if tr_attr_value is None:
                        return 'matched'
                    if tr_attr_value is False:
                        return 'matched'
                    if isinstance(tr_attr_value, (list, tuple)) and tr_attr_value == []:
                        return 'matched'
                    if not isinstance(tr_attr_value, (list, tuple)):
                        tr_attr_value = [tr_attr_value]
                    uvalue = uvalue_func(self.patient_info)
                    if isinstance(uvalue, bool):
                        uvalue = [uvalue]
                    elif isinstance(uvalue, str):
                        uvalue = uvalue.split(",") if uvalue else []
                        uvalue = [str(x).strip() for x in uvalue]
                    elif uvalue is None:
                        uvalue = []
                    else:
                        uvalue = [uvalue]

                    if is_exclude:
                        if len(get_overlap(uvalue, tr_attr_value)) > 0:
                            return 'not_matched'
                        elif patient_info_attr_is_blank:
                            return 'unknown'
                        else:
                            return 'matched'
                    else:
                        if len(get_overlap(uvalue, tr_attr_value)) > 0:
                            return 'matched'
                        elif patient_info_attr_is_blank:
                            return 'unknown'
                        else:
                            return 'not_matched'

                for trial_subattr_name, uvalue_function in trial_attr_meta["uvalue_function"].items():
                    matching_result = get_matching_result(trial_subattr_name, uvalue_function)
                    matching_results.append(matching_result)

                if 'not_matched' in matching_results:
                    return 'not_matched'
                if 'unknown' in matching_results:
                    return 'unknown'
                return 'matched'

            else:
                raise Exception(f'type "{trial_attr_meta["type"]}" is not supported for user_attr "{patient_info_attr}"')

        elif trial_attr_meta["type"] == "value":
            trial_attr_value = getattr(self.trial, trial_attr_name)
            if trial_attr_value is None:
                return 'matched'
            elif patient_info_attr_is_blank:
                return 'unknown'
            elif patient_info_attr_value == trial_attr_value:
                return 'matched'
            else:
                return 'not_matched'

        elif trial_attr_meta["type"] == "str_value":
            trial_attr_value = getattr(self.trial, trial_attr_name)
            if trial_attr_value is None or trial_attr_value == '':
                return 'matched'
            elif patient_info_attr_is_blank or patient_info_attr_value == '':
                return 'unknown'
            elif str(patient_info_attr_value).lower() == str(trial_attr_value).lower():
                return 'matched'
            else:
                return 'not_matched'

        elif trial_attr_meta["type"] == "bool_restriction":
            under_user_control = "under_user_control" in trial_attr_meta and trial_attr_meta["under_user_control"] is True
            trial_attr_value = getattr(self.trial, trial_attr_name)
            if trial_attr_value is None:
                trial_attr_value = False
            if patient_info_attr_value is None:
                patient_info_attr_value = False
            if patient_info_attr_value is True:
                return 'matched'
            elif patient_info_attr_value == trial_attr_value:
                return 'matched'
            elif under_user_control:
                return 'unknown'
            else:
                return 'not_matched'

        elif trial_attr_meta["type"] == "inversed_bool_restriction":
            trial_attr_value = getattr(self.trial, trial_attr_name)
            if trial_attr_value is None:
                trial_attr_value = False
            if patient_info_attr_value is None:
                patient_info_attr_value = False

            if patient_info_attr_value is False:
                return 'matched'
            elif patient_info_attr_value == trial_attr_value:
                return 'not_matched'
            else:
                return 'matched'

        elif trial_attr_meta["type"] == "min_value":
            if 'attr_min' in trial_attr_meta:
                attr_min_name = trial_attr_meta["attr_min"]
            else:
                attr_min_name = f'{trial_attr_meta["attr"]}_min'

            trial_attr_value_min = getattr(self.trial, attr_min_name)

            if trial_attr_value_min is None:
                return 'matched'
            elif patient_info_attr_is_blank:
                return 'unknown'
            elif trial_attr_value_min is not None and patient_info_attr_value < trial_attr_value_min:
                return 'not_matched'
            else:
                return 'matched'

        elif trial_attr_meta["type"] == "max_value":
            if 'attr_max' in trial_attr_meta:
                attr_max_name = trial_attr_meta["attr_max"]
            else:
                attr_max_name = f'{trial_attr_meta["attr"]}_max'

            trial_attr_value_max = getattr(self.trial, attr_max_name)

            if trial_attr_value_max is None:
                return 'matched'
            elif patient_info_attr_is_blank:
                return 'unknown'
            elif trial_attr_value_max is not None and patient_info_attr_value > trial_attr_value_max:
                return 'not_matched'
            else:
                return 'matched'

        elif trial_attr_meta["type"] == "min_max_value":
            if 'attr_min' in trial_attr_meta:
                attr_min_name = trial_attr_meta["attr_min"]
            else:
                attr_min_name = f'{trial_attr_meta["attr"]}_min'
            if 'attr_max' in trial_attr_meta:
                attr_max_name = trial_attr_meta["attr_max"]
            else:
                attr_max_name = f'{trial_attr_meta["attr"]}_max'

            trial_attr_value_min = getattr(self.trial, attr_min_name)
            trial_attr_value_max = getattr(self.trial, attr_max_name)

            abs_vals_match_res = min_max_match(trial_attr_value_min, trial_attr_value_max, patient_info_attr_value, patient_info_attr_is_blank)
            if "uln_attr_min" in trial_attr_meta and "uln_attr_max" in trial_attr_meta:
                trial_attr_value_uln_min = getattr(self.trial, trial_attr_meta["uln_attr_min"])
                trial_attr_value_uln_max = getattr(self.trial, trial_attr_meta["uln_attr_max"])
                user_attr_value_uln = self.patient_info_attr.get_uln_value(patient_info_attr)
                user_attr_value_uln_is_blank = patient_info_attr_is_blank or user_attr_value_uln is None
                uln_vals_match_res = min_max_match(trial_attr_value_uln_min, trial_attr_value_uln_max, user_attr_value_uln, user_attr_value_uln_is_blank)
                if abs_vals_match_res is None and uln_vals_match_res is None:
                    return 'matched'
                elif abs_vals_match_res == 'not_matched' or uln_vals_match_res == 'not_matched':
                    return 'not_matched'
                elif abs_vals_match_res == 'unknown' and uln_vals_match_res == 'unknown':
                    return 'unknown'
                elif abs_vals_match_res == 'matched' or uln_vals_match_res == 'matched':
                    return 'matched'
                return 'unknown'
            else:
                return abs_vals_match_res or 'matched'  # None means matched

        else:
            raise Exception(f'type "{trial_attr_meta["type"]}" is not supported')
