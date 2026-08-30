import re

import inflection
from django.db import models
from django.db.models import Q

from exact_matching.attribute_names import AttributeNames
from exact_matching.patient_info.configs import (
    THERAPY_LINES_ATTRS_UNDERSCORED,
    TRIAL_ATTRS_JSON_AS_A_LIST,
    USER_TO_TRIAL_ATTRS_MAPPING,
)
from exact_matching.patient_info.patient_info_attributes import PatientInfoAttributes
from exact_matching.utils import disease_attr_applies


# The therapy criteria split by which matcher leg decides them. The regimen leg
# reads the patient's raw values, so it stays decidable even when the graph
# cannot expand them; the component and type legs do not (#5013).
_REGIMEN_ATTRS = ('therapies_required', 'therapies_excluded')
_COMPONENT_AND_TYPE_ATTRS = (
    'therapy_types_required', 'therapy_types_excluded',
    'therapy_components_required', 'therapy_components_excluded',
)


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

    @staticmethod
    def _jsonb_intersect_count(column, codes):
        """SQL counting how many elements of a jsonb-array column are in `codes`.

        `codes` is a fixed (patient-derived) set of controlled-vocabulary codes,
        so it is inlined safely after stripping anything outside [a-zA-Z0-9_].
        """
        safe = [c for c in codes if re.fullmatch(r'[a-zA-Z0-9_]+', c or '')]
        if not safe:
            return '0'
        in_list = ', '.join(f"'{c}'" for c in sorted(set(safe)))
        return (
            f"(SELECT COUNT(*) FROM jsonb_array_elements_text(COALESCE({column}, '[]'::jsonb)) "
            f"AS _e(code) WHERE _e.code IN ({in_list}))"
        )

    def _criteria_count_match_expressions(self, trial_attr_meta, service):
        """SQL booleans ``(gating, matched)`` for a per-criterion attribute.

        ``gating`` is true when the trial actually constrains this attribute
        (any of required / sufficient_any / excluded is non-empty); ``matched``
        is true when the per-criterion matcher verdict equals `matched`.

        Replicates `UserToTrialAttrMatcher._match_criteria_count` (#4399/#4401) at
        the per-criterion granularity the aggregate `is_attr_blank` + `'[]'`
        checks lacked (#4416). The patient is fixed, so its derived (known) codes
        D and blank-source (undeterminable) codes U are constants; only the
        trial's required / excluded / sufficient_any / min_count vary per row.

        Verdict is `matched` (eligible) iff inclusion is satisfied AND no excluded
        criterion is present-or-undeterminable. Inclusion only needs the matched
        (D-intersection) counts; the unknown counts only distinguish unknown from
        not_matched, and not_matched trials are filtered out before counting.
        """
        derived_csv = trial_attr_meta['criteria_derived'](service.patient_info)
        derived = {c.strip() for c in (derived_csv or '').split(',') if c.strip()}
        unknown = set(trial_attr_meta['criteria_all_unknown_codes'](service))

        required_attr = trial_attr_meta.get('criteria_required_attr')
        sufficient_attr = trial_attr_meta.get('criteria_sufficient_any_attr')
        excluded_attr = trial_attr_meta.get('criteria_excluded_attr')
        min_count_attr = trial_attr_meta.get('criteria_min_count_attr')

        def length(attr):
            return f"jsonb_array_length(COALESCE({attr}, '[]'::jsonb))" if attr else '0'

        required_len = length(required_attr)
        sufficient_len = length(sufficient_attr)
        excluded_len = length(excluded_attr)
        required_matched = self._jsonb_intersect_count(required_attr, derived) if required_attr else '0'
        sufficient_matched = self._jsonb_intersect_count(sufficient_attr, derived) if sufficient_attr else '0'
        excluded_matched = self._jsonb_intersect_count(excluded_attr, derived) if excluded_attr else '0'
        excluded_unknown = self._jsonb_intersect_count(excluded_attr, unknown) if excluded_attr else '0'

        if min_count_attr:
            min_count = f"(CASE WHEN {min_count_attr} IS NULL OR {min_count_attr} < 1 THEN 1 ELSE {min_count_attr} END)"
        else:
            min_count = '1'

        inclusion = (
            f"(({required_len} = 0 AND {sufficient_len} = 0)"
            f" OR ({required_len} > 0 AND {required_matched} >= {min_count})"
            f" OR ({sufficient_len} > 0 AND {sufficient_matched} >= 1))"
        )
        exclusion_clear = f"({excluded_len} = 0 OR ({excluded_matched} = 0 AND {excluded_unknown} = 0))"
        gating = f"({required_len} > 0 OR {sufficient_len} > 0 OR {excluded_len} > 0)"
        matched = f"({inclusion} AND {exclusion_clear})"
        return gating, matched

    @staticmethod
    def _all_lists_empty(columns):
        """SQL for "every one of these jsonb list columns is empty".

        NULL means "no list constraint", same as '[]' — see the #4832 note on the
        generic list branch below.
        """
        return ' AND '.join(
            f"({column} IS NULL OR {column} = '[]'::jsonb)" for column in columns
        )

    @staticmethod
    def _therapies_do_not_expand(service):
        """True when the patient's therapies yield neither components nor types.

        Mirrors the matcher's derivation (`_match_therapy_related_things`), so the
        count and the verdict agree on what an unexpandable therapy answer means.

        "Cannot expand" is narrower than it sounds: `derive_component_and_type_values`
        returns (None, None) only when NO regimen resolves. A regimen that exists but
        has no components resolves to ([], []), which the matcher then reads as a
        definite 'not_matched' while the SQL filter skips the component check —
        a separate divergence, tracked in #392.
        Both halves must be unknown: a patient whose components are unknown but
        whose types are known can still decide a type criterion, and that case is
        the OMOP-path question tracked in healthkey-ai/exact#390, not this one.
        Called at most once per `potential_attrs_to_check`, not per trial.
        """
        from exact_matching.therapy_match_profile import (
            omop_therapy_enabled, omop_therapy_types_enabled,
        )
        from trials.services.omop.therapy_graph import derive_component_and_type_values

        therapies = service.get_user_therapies()
        if not therapies:
            # No therapy values at all: `is_attr_blank` already answers this case.
            return False
        component_ids = service.get_user_therapy_component_ids() if omop_therapy_enabled() else None
        class_ids = service.get_user_therapy_type_ids() if omop_therapy_types_enabled() else None
        # Called the same way the search filter calls it (querysets/trial.py), not
        # through the matcher's injectable data port: a host that injects its own
        # port would answer the VERDICT from its derivation and this COUNT from
        # EXACT's, which is the divergence this change exists to close. Plumbing a
        # port in here is worth doing when a host actually injects one.
        component_values, type_values = derive_component_and_type_values(
            therapies, component_ids, patient_class_ids=class_ids)
        return component_values is None and type_values is None

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

        # Derived at most once per call (None = not computed yet). Only
        # `first_line_therapy` reaches the check today — the other therapy-line
        # attrs are `skip_in_counts` — but the memo keeps that an optimisation
        # rather than a correctness assumption.
        therapies_do_not_expand = None

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

                # A therapy-line answer the therapy graph cannot expand is not an
                # answer the matcher can use (#5013). `_match_therapy_lines_attr`
                # runs the patient's therapies through
                # `derive_component_and_type_values`; when they resolve to no
                # regimen the component and type legs come back 'unknown', so a
                # trial carrying a component or type criterion is Potential to the
                # matcher — while this count, seeing a non-empty string, called the
                # attr answered and left the trial Eligible.
                #
                # Scope matters: the matcher decides the REGIMEN leg on the raw
                # values (`_match_therapy_things(values, therapies_required, ...)`
                # runs before any derivation), so it is definite about a trial that
                # gates only on regimens. Emitting the attr's whole six-column
                # fragment as potential would demote those trials — 452 of them in
                # a CB catalog, mostly exclusion-only — and simply move the
                # divergence to the other direction. So: potential when the trial
                # gates on a component/type column, answered when it gates only on
                # a regimen column.
                #
                # The hard filter is untouched either way: the regimen overlap in
                # `eligible_for_therapy_related_things_from_lines` still runs on the
                # raw values, so a value that contradicts a required regimen keeps
                # excluding the trial.
                # Not gated on `is_filled_by_user`: the matcher answers
                # `first_line_therapy` from `get_user_therapies()`, which folds in
                # the other therapy lines and the supportive rows, so an
                # unexpandable value in `second_line_therapy` leaves this field
                # blank while still driving the verdict. Deciding from the same bag
                # is what keeps the two paths together —
                # `_therapies_do_not_expand` returns False for an empty bag, the
                # case `is_attr_blank` already owns.
                if user_attr in THERAPY_LINES_ATTRS_UNDERSCORED \
                        and not has_no_prior_therapy:
                    if therapies_do_not_expand is None:
                        therapies_do_not_expand = self._therapies_do_not_expand(service)
                    if therapies_do_not_expand:
                        unknown_legs_empty = self._all_lists_empty(_COMPONENT_AND_TYPE_ATTRS)
                        regimen_legs_empty = self._all_lists_empty(_REGIMEN_ATTRS)
                        attrs2check[user_attr] = (
                            f'(CASE WHEN {unknown_legs_empty} THEN NULL ELSE 1 END)')
                        eligible_attrs2check[user_attr] = (
                            f'(CASE WHEN ({unknown_legs_empty}) AND NOT ({regimen_legs_empty}) '
                            f'THEN 1 ELSE NULL END)')
                        continue

                # Per-criterion "named OR" attrs (high-risk MCL): replicate the
                # three-valued matcher per trial instead of the aggregate
                # is_attr_blank + '[]' checks (#4416). Patient-side only; the
                # counts-only profit path (patient_info=None) keeps the aggregate
                # SQL below.
                if trial_attr_meta.get("criteria_count_match"):
                    gating, matched_cond = self._criteria_count_match_expressions(trial_attr_meta, service)
                    # matched (incl. a non-gating trial) -> NULL (eligible / not
                    # potential); a gating trial that isn't matched -> 1 (potential).
                    # This collapses matcher `unknown` and `not_matched` to potential.
                    # Excluding `not_matched` rows from the list entirely is the job
                    # of the coarse eligible_for_high_risk_mcl_criteria filter
                    # (has_any_keys), which today keeps a min_count>=2 trial a patient
                    # overlaps but can't satisfy, and can drop unknown-only-required
                    # trials; making that filter per-criterion is follow-up work
                    # (issue #186; CB #4419). Net vs the old aggregate path: these
                    # now read potential instead of eligible.
                    attrs2check[user_attr] = f'(CASE WHEN {matched_cond} THEN NULL ELSE 1 END)'
                    # Match-score numerator: count only a gate the trial actually has
                    # and the patient definitively satisfies. A non-gating trial is
                    # neutral (NULL), matching the old aggregate SQL.
                    eligible_attrs2check[user_attr] = f'(CASE WHEN {gating} AND {matched_cond} THEN 1 ELSE NULL END)'
                    continue

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
                    # A NULL jsonb column means "no list constraint", same as '[]'.
                    # `NULL = '[]'::jsonb` is NULL (not TRUE), which would wrongly
                    # fire the ELSE (potential) branch, so treat NULL as empty —
                    # matching potential_attrs_for_trial (skips NULL) and the
                    # matcher (empty list = matched). (#4832 count/attribution reconcile.)
                    sql_check = {'cond': ['IS NULL', "= '[]'::jsonb"], 'type': 'OR'}
                else:
                    sql_check = "IS NULL"

                if isinstance(trial_attr_name, list):
                    columns = []
                    orig_sql_check = sql_check
                    for trial_name_custom_search_attr in trial_attr_name:
                        if trial_name_custom_search_attr in TRIAL_ATTRS_JSON_AS_A_LIST:
                            # NULL jsonb == empty list (see the scalar branch above).
                            sql_check = {'cond': ['IS NULL', "= '[]'::jsonb"], 'type': 'OR'}
                        else:
                            sql_check = orig_sql_check
                        if isinstance(sql_check, dict):
                            column_conds = [f'{trial_name_custom_search_attr} {x}' for x in sql_check['cond']]
                            # Parenthesize: these OR conds are AND-joined with the
                            # other columns below, so without parens SQL precedence
                            # ('a OR b AND c') would regroup them wrongly.
                            columns.append('(' + ' OR '.join(column_conds) + ')')
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
