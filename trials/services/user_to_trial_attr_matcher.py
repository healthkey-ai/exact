import itertools
import datetime as dt
from dataclasses import dataclass
from typing import Any

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
from trials.services.utils import disease_attr_applies, get_overlap


@dataclass
class _AttrMatchCtx:
    """Per-call state for `attr_match_status` handlers — bundles values
    derived once at the top of dispatch so each handler doesn't recompute.

    Mutable on purpose: the `prior_therapy` handler rewrites `value` and
    `is_blank` when the raw string doesn't match a known therapy-line
    label (preserving pre-refactor behaviour exactly).
    """
    name: str
    value: Any
    is_blank: bool
    meta: dict
    has_no_prior_therapy: bool

    @property
    def trial_attr_name(self):
        return self.meta["attr"]


# Per-attr handlers for `custom_search=True` configs. Each value names a
# bound method on `UserToTrialAttrMatcher` resolved via `getattr` at
# dispatch time — string-keyed dispatch sidesteps the chicken-and-egg of
# referencing methods that don't exist yet at module-load.
_CUSTOM_SEARCH_NAMED_HANDLERS = {
    'pre_existing_condition_categories': '_match_pre_existing_condition_categories',
    'stem_cell_transplant_history': '_match_stem_cell_transplant_history',
    'concomitant_medications': '_match_concomitant_medications',
    'stage': '_match_stage',
    'disease': '_match_disease',
    'plasma_cell_leukemia': '_match_plasma_cell_leukemia',
    'progression': '_match_progression',
    'last_treatment': '_match_last_treatment',
    'treatment_refractory_status': '_match_treatment_refractory_status',
    'tumor_grade': '_match_tumor_grade',
    'flipi_score_options': '_match_flipi_score_options',
    'prior_therapy': '_match_prior_therapy',
    'genetic_mutations': '_match_genetic_mutations',
}

# Therapy-line attrs fall back to a single handler after the named
# dispatch misses (these were originally a `patient_info_attr in [...]`
# branch in the megamethod).
_THERAPY_LINES_FALLBACK_ATTRS = frozenset(THERAPY_LINES_ATTRS_UNDERSCORED) | {'supportive_therapies'}

# Handlers keyed by `trial_attr_meta["type"]` — the non-custom_search path.
_TYPE_HANDLERS = {
    'value': '_match_type_value',
    'str_value': '_match_type_str_value',
    'bool_restriction': '_match_type_bool_restriction',
    'inversed_bool_restriction': '_match_type_inversed_bool_restriction',
    'min_value': '_match_type_min_value',
    'max_value': '_match_type_max_value',
    'min_max_value': '_match_type_min_max_value',
}


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
            if "disease" in trial_attr_meta and (
                self.disease_code is None
                or not disease_attr_applies(trial_attr_meta["disease"], self.disease_code)
            ):
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
            if "disease" in trial_attr_meta and (
                self.disease_code is None
                or not disease_attr_applies(trial_attr_meta["disease"], self.disease_code)
            ):
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
        """Match a single patient attribute against the corresponding trial
        attribute(s) and return one of `'matched'`, `'not_matched'`,
        `'unknown'`.

        Dispatch is two-layer:
        1. `custom_search=True` configs route to a per-attr handler in
           `_CUSTOM_SEARCH_NAMED_HANDLERS`, then fall back to the
           therapy-line handler (for `THERAPY_LINES_ATTRS_UNDERSCORED +
           'supportive_therapies'`), then to the generic
           `ATTR_MAPPING_TYPE_COMPUTED` handler.
        2. Everything else routes by `trial_attr_meta["type"]` through
           `_TYPE_HANDLERS`.
        Unknown types raise the same Exception messages the
        pre-refactor megamethod did (#28).
        """
        meta = self.mapping[patient_info_attr]
        prior_therapy = self.patient_info_attr.get_value('prior_therapy')
        ctx = _AttrMatchCtx(
            name=patient_info_attr,
            value=self.patient_info_attr.get_value(patient_info_attr),
            is_blank=self.is_patient_info_attr_blank(patient_info_attr),
            meta=meta,
            has_no_prior_therapy=prior_therapy in ["None"],
        )

        if meta.get("custom_search") is True:
            method_name = _CUSTOM_SEARCH_NAMED_HANDLERS.get(patient_info_attr)
            if method_name is not None:
                return getattr(self, method_name)(ctx)
            if patient_info_attr in _THERAPY_LINES_FALLBACK_ATTRS:
                return self._match_therapy_lines_attr(ctx)
            if meta["type"] == ATTR_MAPPING_TYPE_COMPUTED:
                return self._match_computed_attr(ctx)
            raise Exception(f'type "{meta["type"]}" is not supported for user_attr "{patient_info_attr}"')

        method_name = _TYPE_HANDLERS.get(meta["type"])
        if method_name is not None:
            return getattr(self, method_name)(ctx)
        raise Exception(f'type "{meta["type"]}" is not supported')

    # ── Shared therapy-matching helpers ──────────────────────────────
    # Promoted from inline closures in the pre-refactor megamethod —
    # used by both `_match_therapy_lines_attr` and downstream tests.

    def _match_therapy_things(self, values, required_list, excluded_list, has_no_prior_therapy):
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

    def _match_therapy_related_things(self, values, has_no_prior_therapy):
        results = []
        res = self._match_therapy_things(values, self.trial.therapies_required, self.trial.therapies_excluded, has_no_prior_therapy)
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

        res = self._match_therapy_things(component_codes, self.trial.therapy_components_required, self.trial.therapy_components_excluded, has_no_prior_therapy)
        if res == 'not_matched':
            return res
        results.append(res)

        res = self._match_therapy_things(therapy_types, self.trial.therapy_types_required, self.trial.therapy_types_excluded, has_no_prior_therapy)
        if res == 'not_matched':
            return res
        results.append(res)

        if 'unknown' in results:
            return 'unknown'

        return 'matched'

    # ── custom_search=True named handlers ────────────────────────────

    def _match_pre_existing_condition_categories(self, ctx):
        trial_attr_value = getattr(self.trial, ctx.trial_attr_name)
        if trial_attr_value is None or trial_attr_value == []:
            return 'matched'
        elif ctx.is_blank:
            return 'unknown'
        elif len(get_overlap(ctx.value, trial_attr_value)) > 0:
            return 'not_matched'
        else:
            return 'matched'

    def _match_stem_cell_transplant_history(self, ctx):
        trial_attr_sct_history_required = getattr(self.trial, 'stem_cell_transplant_history_required')
        trial_attr_sct_history_excluded = getattr(self.trial, 'stem_cell_transplant_history_excluded')

        # See sct_value_is_none() — tolerates both storage shapes
        # ('None' bare string from signal cleanup; ['None'] list
        # from the multiselect) and whitespace/casing variants
        # (#4333, #4340).
        user_has_no_sct = sct_value_is_none(ctx.value)
        if not trial_attr_sct_history_required and not trial_attr_sct_history_excluded:
            return 'matched'

        if ctx.is_blank:
            return 'unknown'

        if trial_attr_sct_history_required and user_has_no_sct:
            return 'not_matched'

        if not trial_attr_sct_history_excluded:
            return 'matched'
        elif _sct_has_mapped_items(ctx.value, trial_attr_sct_history_excluded):
            return 'not_matched'
        else:
            return 'matched'

    def _match_concomitant_medications(self, ctx):
        concomitant_medications_excluded = getattr(self.trial, 'concomitant_medications_excluded')
        concomitant_medications_washout_period_duration = getattr(self.trial, 'concomitant_medications_washout_period_duration')

        user_has_no_cm = str(ctx.value).lower() == 'none'
        if not concomitant_medications_excluded:
            return 'matched'

        if ctx.is_blank:
            return 'unknown'

        if concomitant_medications_washout_period_duration and user_has_no_cm:
            return 'matched'

        if not _concomitant_has_mapped_items(ctx.value, concomitant_medications_excluded):
            return 'matched'

        if not concomitant_medications_washout_period_duration:
            return 'not_matched'

        concomitant_medication_date = self.patient_info_attr.get_value('concomitant_medication_date')
        if not concomitant_medication_date:
            return 'not_matched'

        current_washout_period_in_days = (dt.date.today() - concomitant_medication_date).days
        return 'matched' if concomitant_medications_washout_period_duration < current_washout_period_in_days else 'not_matched'

    def _match_stage(self, ctx):
        if self.trial.stages == []:
            return 'matched'
        elif ctx.is_blank:
            return 'unknown'
        elif len(get_overlap([ctx.value], self.trial.stages)) > 0:
            return 'matched'
        else:
            return 'not_matched'

    def _match_disease(self, ctx):
        trial_attr_value = getattr(self.trial, ctx.trial_attr_name)
        if trial_attr_value is None or trial_attr_value == '':
            return 'matched'
        elif ctx.is_blank or ctx.value == '':
            return 'unknown'
        elif str(ctx.value).lower() in str(trial_attr_value).lower():
            return 'matched'
        else:
            return 'not_matched'

    def _match_plasma_cell_leukemia(self, ctx):
        trial_attr_no_pcl_required = getattr(self.trial, 'no_plasma_cell_leukemia_required')
        trial_attr_pcl_required = getattr(self.trial, 'plasma_cell_leukemia_required')

        if trial_attr_no_pcl_required is not True and trial_attr_pcl_required is not True:
            return 'matched'

        if ctx.value is None:
            return 'unknown'

        if ctx.value is True and trial_attr_no_pcl_required is not True:
            return 'matched'
        elif ctx.value is False and trial_attr_pcl_required is not True:
            return 'matched'
        else:
            return 'not_matched'

    def _match_progression(self, ctx):
        trial_attr_active_required = getattr(self.trial, 'disease_progression_active_required')
        trial_attr_smoldering_required = getattr(self.trial, 'disease_progression_smoldering_required')

        if trial_attr_active_required is not True and trial_attr_smoldering_required is not True:
            return 'matched'

        # "Unknown" is sent from the UI as an empty string (see ValueOptions.progressions)
        if ctx.value is None or ctx.value == '':
            return 'unknown'

        if ctx.value == 'active' and trial_attr_smoldering_required is not True:
            return 'matched'
        elif ctx.value == 'smoldering' and trial_attr_active_required is not True:
            return 'matched'
        else:
            return 'not_matched'

    def _match_last_treatment(self, ctx):
        trial_attr_value = getattr(self.trial, 'washout_period_duration')
        if trial_attr_value is None:
            return 'matched'

        if ctx.has_no_prior_therapy:
            return 'matched'

        if ctx.value is None:
            return 'unknown'

        current_washout_period_in_days = (dt.date.today() - ctx.value).days

        return 'matched' if trial_attr_value < current_washout_period_in_days else 'not_matched'

    def _match_treatment_refractory_status(self, ctx):
        trial_attr_not_refractory_required = getattr(self.trial, 'not_refractory_required')
        trial_attr_refractory_required = getattr(self.trial, 'refractory_required')

        if trial_attr_not_refractory_required is not True and trial_attr_refractory_required is not True:
            return 'matched'

        if not ctx.value:
            return 'unknown'

        #  "notRefractory": "Not Refractory (progression halted)",
        #  "primaryRefractory": "Primary Refractory",
        #  "secondaryRefractory": "Secondary Refractory",
        #  "multiRefractory": "Multi-Refractory",
        if ctx.value in ['notRefractory', 'Not Refractory (progression halted)']:
            return 'matched' if trial_attr_refractory_required is not True else 'not_matched'
        elif trial_attr_not_refractory_required is not True:
            return 'matched'
        else:
            return 'not_matched'

    def _match_tumor_grade(self, ctx):
        trial_attr_value_min = getattr(self.trial, 'tumor_grade_min')
        trial_attr_value_max = getattr(self.trial, 'tumor_grade_max')

        value = ctx.value
        if not ctx.is_blank:
            if isinstance(value, int) or str(value).isdigit():
                value = int(value)
            else:
                from trials.services.value_options import ValueOptions
                mapping = {}
                for k, v in ValueOptions().tumor_grades().items():
                    mapping[v.lower()] = k

                tumor_grade_val = str(value).lower()
                value = mapping.get(tumor_grade_val, None)

        if trial_attr_value_min is None and trial_attr_value_max is None:
            return 'matched'
        elif ctx.is_blank:
            return 'unknown'
        elif trial_attr_value_min is not None and value < trial_attr_value_min:
            return 'not_matched'
        elif trial_attr_value_max is not None and value > trial_attr_value_max:
            return 'not_matched'
        else:
            return 'matched'

    def _match_flipi_score_options(self, ctx):
        trial_attr_value_min = getattr(self.trial, 'flipi_score_min')
        trial_attr_value_max = getattr(self.trial, 'flipi_score_max')

        value = ctx.value
        if not ctx.is_blank:
            value = PatientInfoFlipyScore.scope_by_options(value)

        if trial_attr_value_min is None and trial_attr_value_max is None:
            return 'matched'
        elif ctx.is_blank:
            return 'unknown'
        elif trial_attr_value_min is not None and value < trial_attr_value_min:
            return 'not_matched'
        elif trial_attr_value_max is not None and value > trial_attr_value_max:
            return 'not_matched'
        else:
            return 'matched'

    def _match_prior_therapy(self, ctx):
        # "None", "One line", "Two lines", "More than two lines of therapy"
        raw_value = str(ctx.value).lower()

        if raw_value == 'none':
            value = 0
        elif raw_value == 'one line':
            value = 1
        elif raw_value == 'two lines':
            value = 2
        elif raw_value == 'more than two lines of therapy':
            value = 3
        else:
            value = None
            ctx.is_blank = True

        trial_attr_value_min = getattr(self.trial, 'therapy_lines_count_min')
        trial_attr_value_max = getattr(self.trial, 'therapy_lines_count_max')

        if trial_attr_value_min is None and trial_attr_value_max is None:
            return 'matched'
        elif ctx.is_blank:
            return 'unknown'
        elif trial_attr_value_min is not None and value < trial_attr_value_min:
            return 'not_matched'
        elif trial_attr_value_max is not None and value > trial_attr_value_max:
            return 'not_matched'
        else:
            return 'matched'

    def _match_genetic_mutations(self, ctx):
        trial_mutation_genes_required = getattr(self.trial, 'mutation_genes_required')
        trial_mutation_variants_required = getattr(self.trial, 'mutation_variants_required')
        trial_mutation_origins_required = getattr(self.trial, 'mutation_origins_required')
        trial_mutation_interpretations_required = getattr(self.trial, 'mutation_interpretations_required')
        pi_genes = GeneticMutations.mutation_genes(ctx.value)
        pi_variants = GeneticMutations.mutation_variants(ctx.value)
        pi_origins = GeneticMutations.mutation_origins(ctx.value)
        pi_interpretations = GeneticMutations.mutation_interpretations(ctx.value)

        if trial_mutation_genes_required == [] and trial_mutation_variants_required == [] and trial_mutation_origins_required == [] and trial_mutation_interpretations_required == []:
            return 'matched'
        elif ctx.is_blank:
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

    # ── custom_search fallbacks ─────────────────────────────────────

    def _match_therapy_lines_attr(self, ctx):
        if ctx.name == 'first_line_therapy':  # calc things for just one line of therapy
            therapies = self.patient_info_attr.get_user_therapies()
            if len(therapies) == 0:
                therapies = None
            return self._match_therapy_related_things(therapies, ctx.has_no_prior_therapy)
        else:
            return 'matched'

    def _match_computed_attr(self, ctx):
        matching_results = []

        for trial_subattr_name, uvalue_function in ctx.meta["uvalue_function"].items():
            matching_result = self._match_computed_subattr(trial_subattr_name, uvalue_function, ctx.is_blank)
            matching_results.append(matching_result)

        if 'not_matched' in matching_results:
            return 'not_matched'
        if 'unknown' in matching_results:
            return 'unknown'
        return 'matched'

    def _match_computed_subattr(self, trial_subattr_name, uvalue_func, is_blank):
        # Naming convention: trial attrs ending in `_excluded` are
        # restriction lists (patient must NOT be in them); anything
        # else (typically `_required`) is the inclusion list.
        is_exclude = '_excluded' in trial_subattr_name

        tr_attr_value = getattr(self.trial, trial_subattr_name)
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
            elif is_blank:
                return 'unknown'
            else:
                return 'matched'
        else:
            if len(get_overlap(uvalue, tr_attr_value)) > 0:
                return 'matched'
            elif is_blank:
                return 'unknown'
            else:
                return 'not_matched'

    # ── type-keyed handlers ──────────────────────────────────────────

    def _match_type_value(self, ctx):
        trial_attr_value = getattr(self.trial, ctx.trial_attr_name)
        if trial_attr_value is None:
            return 'matched'
        elif ctx.is_blank:
            return 'unknown'
        elif ctx.value == trial_attr_value:
            return 'matched'
        else:
            return 'not_matched'

    def _match_type_str_value(self, ctx):
        trial_attr_value = getattr(self.trial, ctx.trial_attr_name)
        if trial_attr_value is None or trial_attr_value == '':
            return 'matched'
        elif ctx.is_blank or ctx.value == '':
            return 'unknown'
        elif str(ctx.value).lower() == str(trial_attr_value).lower():
            return 'matched'
        else:
            return 'not_matched'

    def _match_type_bool_restriction(self, ctx):
        under_user_control = "under_user_control" in ctx.meta and ctx.meta["under_user_control"] is True
        trial_attr_value = getattr(self.trial, ctx.trial_attr_name)
        if trial_attr_value is None:
            trial_attr_value = False
        value = False if ctx.value is None else ctx.value
        if value is True:
            return 'matched'
        elif value == trial_attr_value:
            return 'matched'
        elif under_user_control:
            return 'unknown'
        else:
            return 'not_matched'

    def _match_type_inversed_bool_restriction(self, ctx):
        trial_attr_value = getattr(self.trial, ctx.trial_attr_name)
        if trial_attr_value is None:
            trial_attr_value = False
        value = False if ctx.value is None else ctx.value

        if value is False:
            return 'matched'
        elif value == trial_attr_value:
            return 'not_matched'
        else:
            return 'matched'

    def _match_type_min_value(self, ctx):
        if 'attr_min' in ctx.meta:
            attr_min_name = ctx.meta["attr_min"]
        else:
            attr_min_name = f'{ctx.meta["attr"]}_min'

        trial_attr_value_min = getattr(self.trial, attr_min_name)

        if trial_attr_value_min is None:
            return 'matched'
        elif ctx.is_blank:
            return 'unknown'
        elif trial_attr_value_min is not None and ctx.value < trial_attr_value_min:
            return 'not_matched'
        else:
            return 'matched'

    def _match_type_max_value(self, ctx):
        if 'attr_max' in ctx.meta:
            attr_max_name = ctx.meta["attr_max"]
        else:
            attr_max_name = f'{ctx.meta["attr"]}_max'

        trial_attr_value_max = getattr(self.trial, attr_max_name)

        if trial_attr_value_max is None:
            return 'matched'
        elif ctx.is_blank:
            return 'unknown'
        elif trial_attr_value_max is not None and ctx.value > trial_attr_value_max:
            return 'not_matched'
        else:
            return 'matched'

    def _match_type_min_max_value(self, ctx):
        if 'attr_min' in ctx.meta:
            attr_min_name = ctx.meta["attr_min"]
        else:
            attr_min_name = f'{ctx.meta["attr"]}_min'
        if 'attr_max' in ctx.meta:
            attr_max_name = ctx.meta["attr_max"]
        else:
            attr_max_name = f'{ctx.meta["attr"]}_max'

        trial_attr_value_min = getattr(self.trial, attr_min_name)
        trial_attr_value_max = getattr(self.trial, attr_max_name)

        abs_vals_match_res = min_max_match(trial_attr_value_min, trial_attr_value_max, ctx.value, ctx.is_blank)
        if "uln_attr_min" in ctx.meta and "uln_attr_max" in ctx.meta:
            trial_attr_value_uln_min = getattr(self.trial, ctx.meta["uln_attr_min"])
            trial_attr_value_uln_max = getattr(self.trial, ctx.meta["uln_attr_max"])
            user_attr_value_uln = self.patient_info_attr.get_uln_value(ctx.name)
            user_attr_value_uln_is_blank = ctx.is_blank or user_attr_value_uln is None
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


# Module-level helpers used by SCT and concomitant-medications handlers.
# Pre-refactor these were inline closures (two functions both named
# `has_mapped_items`) — promoting to module scope avoids the
# closure-shadowing footgun.

def _sct_has_mapped_items(pi_vals, excluded_list):
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


def _concomitant_has_mapped_items(pi_vals, excluded_list):
    if not isinstance(pi_vals, (list, tuple)):
        pi_vals = [pi_vals]

    if pi_vals == ['None']:
        return False

    for item in pi_vals:
        if item in excluded_list:
            return True

    return False
