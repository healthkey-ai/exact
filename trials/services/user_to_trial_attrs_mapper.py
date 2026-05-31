import inflection
from django.db import models
from django.db.models import Q

from trials.services.attribute_names import AttributeNames
from trials.services.patient_info.configs import USER_TO_TRIAL_ATTRS_MAPPING, TRIAL_ATTRS_JSON_AS_A_LIST
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes
from trials.services.utils import disease_attr_applies


class UserToTrialAttrsMapper:
    def potential_attrs_for_trial(self, trial, counts):
        def item(trial_attribute_name, user_attribute_name, trial_obj, cnt):
            if getattr(trial_obj, trial_attribute_name) is None:
                return

            if trial_attribute_name in TRIAL_ATTRS_JSON_AS_A_LIST:
                if getattr(trial_obj, trial_attribute_name) == []:
                    return

            return {
                'trialAttributeName': AttributeNames.get_by_snake_case(trial_attribute_name),
                'userAttributeName': AttributeNames.get_by_snake_case(user_attribute_name),
                'userAttributeTitle': inflection.humanize(user_attribute_name).title(),
                'count': cnt
            }

        def items(trial_attribute_name, user_attribute_name, trial_obj, cnt, trial_attr_meta):
            out = []
            if "attrs_to_compute" in trial_attr_meta:
                for val in trial_attr_meta["attrs_to_compute"]:
                    out.append(item(trial_attribute_name, val, trial_obj, cnt))
            else:
                out.append(item(trial_attribute_name, user_attribute_name, trial_obj, cnt))

            return out

        out = []

        mapping = USER_TO_TRIAL_ATTRS_MAPPING
        for user_attr in counts.keys():

            count = counts[user_attr]
            trial_attr_meta = mapping[user_attr]

            # compare with Trial
            trial_attr_name = trial_attr_meta["attr"]

            def all_is_not_true(trial, attrs):
                for trial_name_custom_search_attr in attrs:
                    if getattr(trial, trial_name_custom_search_attr) is True:
                        return False
                return True

            is_under_user_control = 'under_user_control' in trial_attr_meta and trial_attr_meta['under_user_control'] is True
            if (trial_attr_meta["type"] == "bool_restriction" and is_under_user_control) or user_attr in ["plasma_cell_leukemia", "progression", "treatment_refractory_status", "stem_cell_transplant_history", "abnormal_kappa_lambda_ratio", "meets_slim", "meets_crab", "bone_only_metastasis_status", "measurable_disease_by_recist_status", "tnbc_status"]:
                if isinstance(trial_attr_name, list):
                    if all_is_not_true(trial=trial, attrs=trial_attr_name):
                        continue  # skip
                elif getattr(trial, trial_attr_name) is not True:
                    continue  # skip
            elif trial_attr_meta["type"] == "str_value":
                val = getattr(trial, trial_attr_name)
                if val is None or str(val) == '':
                    continue  # skip
            elif trial_attr_meta["attr"] == "stages":
                trial_attr_name = "has_stages"
                if getattr(trial, trial_attr_name) is not True:
                    continue  # skip

            if trial_attr_meta["type"] == "min_value":
                if "attr_min" in trial_attr_meta:
                    attr_min_name = trial_attr_meta["attr_min"]
                else:
                    attr_min_name = f'{trial_attr_meta["attr"]}_min'
                out = out + items(attr_min_name, user_attr, trial, count, trial_attr_meta)

            elif trial_attr_meta["type"] == "max_value":
                if "attr_max" in trial_attr_meta:
                    attr_max_name = trial_attr_meta["attr_max"]
                else:
                    attr_max_name = f'{trial_attr_meta["attr"]}_max'
                out = out + items(attr_max_name, user_attr, trial, count, trial_attr_meta)

            elif trial_attr_meta["type"] == "min_max_value":
                if "attr_min" in trial_attr_meta:
                    attr_min_name = trial_attr_meta["attr_min"]
                else:
                    attr_min_name = f'{trial_attr_meta["attr"]}_min'
                out = out + items(attr_min_name, user_attr, trial, count, trial_attr_meta)

                if "attr_max" in trial_attr_meta:
                    attr_max_name = trial_attr_meta["attr_max"]
                else:
                    attr_max_name = f'{trial_attr_meta["attr"]}_max'
                out = out + items(attr_max_name, user_attr, trial, count, trial_attr_meta)

            else:
                if isinstance(trial_attr_name, list):
                    for trial_name_custom_search_attr in trial_attr_name:
                        out = out + items(trial_name_custom_search_attr, user_attr, trial, count, trial_attr_meta)
                else:
                    out = out + items(trial_attr_name, user_attr, trial, count, trial_attr_meta)

        out = [x for x in out if x is not None]
        out = sorted(out, key=lambda d: -d['count'])
        return out

    def potential_attrs_to_check(self, patient_info, counts=None, with_eligible=False):
        attrs2check = {}
        eligible_attrs2check = {}
        if patient_info is None and counts is None:
            if with_eligible:
                return attrs2check, eligible_attrs2check
            return attrs2check

        service = None
        has_no_prior_therapy = False

        if patient_info:
            service = PatientInfoAttributes(patient_info)
            has_no_prior_therapy = patient_info.prior_therapy == 'None'

        mapping = USER_TO_TRIAL_ATTRS_MAPPING
        for user_attr in mapping.keys():
            is_filled_by_user = False

            trial_attr_meta = mapping[user_attr]

            if 'skip_in_counts' in trial_attr_meta and trial_attr_meta['skip_in_counts'] is True:
                continue

            if has_no_prior_therapy and user_attr == 'last_treatment':
                continue  # skip check for potential counts

            is_under_user_control = 'under_user_control' in trial_attr_meta and trial_attr_meta['under_user_control'] is True

            if patient_info:
                if "disease" in trial_attr_meta and (
                    service.disease_code is None
                    or not disease_attr_applies(trial_attr_meta["disease"], service.disease_code)
                ):
                    continue

                is_blank = service.is_attr_blank(user_attr)

                if not is_blank:
                    is_filled_by_user = True
                    # continue

            then_value = 'NULL ELSE 1'
            count_value = 0
            if counts is not None:
                count_value = counts.get(user_attr, 0)
                if count_value > 0:
                    then_value = f'0 ELSE {count_value}'
                else:
                    then_value = '0 ELSE 1'

            # compare with Trial
            trial_attr_name = trial_attr_meta["attr"]

            if trial_attr_meta["type"] == "min_value":
                if 'attr_min' in trial_attr_meta:
                    attr_min_name = trial_attr_meta["attr_min"]
                else:
                    attr_min_name = f'{trial_attr_meta["attr"]}_min'

                sql_query = f'(CASE WHEN {attr_min_name} IS NULL THEN {then_value} END)'

            elif trial_attr_meta["type"] == "max_value":
                if 'attr_max' in trial_attr_meta:
                    attr_max_name = trial_attr_meta["attr_max"]
                else:
                    attr_max_name = f'{trial_attr_meta["attr"]}_max'

                sql_query = f'(CASE WHEN {attr_max_name} IS NULL THEN {then_value} END)'

            elif trial_attr_meta["type"] == "min_max_value":
                if 'attr_min' in trial_attr_meta:
                    attr_min_name = trial_attr_meta["attr_min"]
                else:
                    attr_min_name = f'{trial_attr_meta["attr"]}_min'
                if 'attr_max' in trial_attr_meta:
                    attr_max_name = trial_attr_meta["attr_max"]
                else:
                    attr_max_name = f'{trial_attr_meta["attr"]}_max'

                cases = f"{attr_min_name} IS NULL AND {attr_max_name} IS NULL"
                if "uln_attr_min" in trial_attr_meta and "uln_attr_max" in trial_attr_meta:
                    cases = f'{cases} AND {trial_attr_meta["uln_attr_min"]} IS NULL AND {trial_attr_meta["uln_attr_max"]} IS NULL'

                sql_query = f'(CASE WHEN {cases} THEN {then_value} END)'

            else:
                if (trial_attr_meta["type"] == "bool_restriction" and is_under_user_control) or user_attr in ["plasma_cell_leukemia", "progression", "treatment_refractory_status", "stem_cell_transplant_history", "abnormal_kappa_lambda_ratio", "meets_slim", "meets_crab", "bone_only_metastasis_status", "measurable_disease_by_recist_status", "measurable_disease_imwg", "tnbc_status", "tp53_disruption", "btk_inhibitor_refractory", "bcl2_inhibitor_refractory", "measurable_disease_iwcll", "hepatomegaly", "autoimmune_cytopenias_refractory_to_steroids", "lymphadenopathy", "splenomegaly", "bone_marrow_involvement"]:
                    sql_check = "IS NOT TRUE"
                elif trial_attr_meta["type"] == "str_value":
                    sql_check = {'cond': ['IS NULL', "= ''"], 'type': 'OR'}
                # elif trial_attr_meta["attr"] == "stages":
                #     trial_attr_name = "has_stages"
                #     sql_check = "IS FALSE"
                elif trial_attr_name in TRIAL_ATTRS_JSON_AS_A_LIST:
                    sql_check = "= '[]'::jsonb"
                else:
                    sql_check = "IS NULL"

                if isinstance(trial_attr_name, list):
                    columns = []
                    orig_sql_check = sql_check
                    for trial_name_custom_search_attr in trial_attr_name:
                        if trial_name_custom_search_attr in TRIAL_ATTRS_JSON_AS_A_LIST:
                            sql_check = "= '[]'::jsonb"
                        else:
                            sql_check = orig_sql_check
                        if isinstance(sql_check, dict):
                            column_conds = [f'{trial_name_custom_search_attr} {x}' for x in sql_check['cond']]
                            columns.append(' OR '.join(column_conds))
                        else:
                            columns.append(f'{trial_name_custom_search_attr} {sql_check}')
                    sql_query = f'(CASE WHEN {" AND ".join(columns)} THEN {then_value} END)'
                    # print(">>>>>sql_query:", sql_query)
                else:
                    if isinstance(sql_check, dict):
                        column_conds = [f'{trial_attr_name} {x}' for x in sql_check['cond']]
                        sql_query = f'(CASE WHEN ({" OR ".join(column_conds)}) THEN {then_value} END)'
                    else:
                        sql_query = f'(CASE WHEN {trial_attr_name} {sql_check} THEN {then_value} END)'

            if sql_query and (patient_info or count_value > 0):
                if is_filled_by_user:
                    eligible_attrs2check[user_attr] = sql_query
                else:
                    attrs2check[user_attr] = sql_query

        if with_eligible:
            return attrs2check, eligible_attrs2check
        return attrs2check
