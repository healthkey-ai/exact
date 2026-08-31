import itertools
import datetime as dt
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Optional

from exact_matching.therapy_match_profile import THERAPY_MATCH_PROFILE

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from trials.models import Trial
    from trials.services.patient_info.patient_info import PatientInfo

# Per-attribute match outcomes. `attr_match_status` always returns one of
# these three literal strings (it raises on unsupported config types
# rather than returning a fourth tag). `min_max_match` is the exception:
# it returns `None` when neither bound is set, signalling "no constraint".
AttrMatchStatus = Literal['matched', 'not_matched', 'unknown']

# Aggregate per-trial outcomes (`trial_match_status` returns one).
TrialMatchStatus = Literal['eligible', 'potential', 'not_eligible']

from exact_matching.enums import PriorTherapyLines
from exact_matching.patient_info.configs import (
    USER_TO_TRIAL_ATTRS_MAPPING,
    THERAPY_LINES_ATTRS_UNDERSCORED,
    ATTR_MAPPING_TYPE_COMPUTED, SCT_HISTORY_EXCLUDED_MAPPING,
    sct_value_is_none,
)
from exact_matching.patient_info.genetic_mutations import GeneticMutations
from exact_matching.patient_info.patient_info_attributes import PatientInfoAttributes
from exact_matching.patient_info.patient_info_flipi_score import PatientInfoFlipyScore
from exact_matching.utils import disease_attr_applies, get_overlap


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
    'supportive_therapies': '_match_supportive_therapies',
}

# Therapy-line attrs fall back to a single handler after the named
# dispatch misses (these were originally a `patient_info_attr in [...]`
# branch in the megamethod).
_THERAPY_LINES_FALLBACK_ATTRS = frozenset(THERAPY_LINES_ATTRS_UNDERSCORED)

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


def min_max_match(
    min_val: Optional[Any],
    max_val: Optional[Any],
    value: Any,
    value_is_blank: bool,
    sane_range: Optional[tuple] = None,
    equal_bounds_are_ceiling: bool = False,
) -> Optional[AttrMatchStatus]:
    """Return `None` when both bounds are absent (no constraint), otherwise
    the standard per-attr match outcome. `min_val`/`max_val`/`value` are
    annotated `Any` because callers pass mixed numeric types (ints, floats,
    Decimals) plus date/datetime objects depending on the attr — uniform
    comparison via `<` / `>` is the only contract.

    `sane_range=(low, high)`: a stored threshold outside the attribute's canonical
    band is treated as no constraint (mid-migration bi-scaled thresholds), so the
    matcher agrees with the queryset's sane_range guard instead of labelling an
    admitted trial not_matched. (#4840.)

    `equal_bounds_are_ceiling` (cb#4863): when a pair holds the SAME number in both
    columns, extraction wrote a one-sided ceiling twice, so read it as a ceiling and
    drop the vacuous floor rather than demanding an exact match. Must stay in step
    with `eligible_for_min_max_value`'s flag on the search side, or search and trial
    details disagree about the same trial. Passed for ULN pairs only; default False =
    no-op. Ported from CB (the wider guarded/not_evaluated reporting there is a
    separate divergence not carried here).
    """
    if (equal_bounds_are_ceiling
            and min_val is not None and max_val is not None and min_val == max_val):
        min_val = None
    if min_val is None and max_val is None:
        return None
    # Blank must be handled BEFORE the sane_range nullification below: otherwise an
    # out-of-band threshold collapses both bounds to None and wrongly reports
    # matched/no-constraint, disagreeing with the SQL classification (a blank attr
    # against a constraint is 'potential').
    if value_is_blank:
        return 'unknown'
    if sane_range is not None:
        low, high = sane_range
        if min_val is not None and not (low <= min_val <= high):
            min_val = None
        if max_val is not None and not (low <= max_val <= high):
            max_val = None
    if min_val is None and max_val is None:
        return None
    elif min_val is not None and value < min_val:
        return 'not_matched'
    elif max_val is not None and value > max_val:
        return 'not_matched'
    else:
        return 'matched'


def _resolve_omop_concepts(concept_id_values, release_id=None):
    """Resolve OMOP concept_id strings to ``[{code, title, vocab}]`` from the
    release-pinned local vocab mirror (#252 / ADR 0002).

    Reads the mirror ``concept`` table for the pinned release — explicit
    ``release_id`` overrides the request pin, which falls back to the active
    release. This is **presentation** (the trial detail ``omopConcepts`` block),
    so it fails **soft**: an unresolved concept_id (or no active release at all)
    still returns its ``code`` with ``title``/``vocab`` = ``None``, exactly as
    before — a missing title never affects eligibility.
    """
    if not concept_id_values:
        return []
    # django.db.Error is the base of the whole DB-API exception tree, incl.
    # InterfaceError ("connection already closed"), which is a sibling of
    # DatabaseError — not a subclass — so it must be caught here too, else a
    # dropped mirror connection would 500 instead of degrading to code-only.
    from django.db import Error as DBError

    from vocab_mirror.models import MirrorConcept
    from vocab_mirror.release_context import active_pinned_release

    rid = release_id if release_id is not None else active_pinned_release()
    by_id = {}
    if rid is not None:
        cids = [int(v) for v in concept_id_values if str(v).isdigit()]
        try:
            by_id = {
                c['concept_id']: c
                for c in MirrorConcept.objects.filter(release_id=rid, concept_id__in=cids)
                .values('concept_id', 'concept_name', 'vocabulary_id')
            }
        except DBError:
            # Presentation must never break the response — a mirror DB hiccup
            # degrades to code-only titles, not a 500.
            logger.warning('vocab mirror title lookup failed; returning code-only',
                           exc_info=True)
            by_id = {}
    out = []
    for v in concept_id_values:
        code = int(v) if str(v).isdigit() else v
        c = by_id.get(code) if isinstance(code, int) else None
        out.append({
            'code': code,
            'title': c['concept_name'] if c else None,
            'vocab': c['vocabulary_id'] if c else None,
        })
    return out


class UserToTrialAttrMatcher:
    def __init__(self, trial: 'Trial', patient_info: 'PatientInfo', data=None) -> None:
        self.trial = trial
        self.patient_info = patient_info
        self.mapping = USER_TO_TRIAL_ATTRS_MAPPING
        self.patient_info_attr = PatientInfoAttributes(patient_info)
        self.disease_code: Optional[str] = self.get_disease_code_from_trial(trial)
        # Matcher data-access port (E1.2b): therapy regimen/component/category
        # reads go through this. Default = EXACT's Django models (1:1); a host
        # like CB injects its own. See exact_matching.data_port.
        if data is None:
            from exact_matching.data_port import DjangoMatcherData
            data = DjangoMatcherData()
        self._data = data

    def get_disease_code_from_trial(self, trial: 'Trial') -> Optional[str]:
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

    def trial_match_status(self) -> TrialMatchStatus:
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

    def trial_match_score(self) -> int:
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

    def match_score_and_status(self) -> tuple[int, 'TrialMatchStatus']:
        """Single-pass equivalent of (trial_match_score(), trial_match_status()).

        Walks the attr mapping once instead of twice — callers that need both
        (the detail serializer) avoid the duplicate pass. Results are identical to
        calling the two methods separately; no caching, so it still reflects the
        current patient state on every call.
        """
        eligible_count = 0
        all_count = 0
        has_not_matched = False
        has_unknown = False
        for attr, trial_attr_meta in self.mapping.items():
            if "disease" in trial_attr_meta and (
                self.disease_code is None
                or not disease_attr_applies(trial_attr_meta["disease"], self.disease_code)
            ):
                continue
            status = self.attr_match_status(attr)
            all_count += 1
            if status == 'not_matched':
                has_not_matched = True
            elif status == 'matched':
                eligible_count += 1
            elif status == 'unknown':
                has_unknown = True

        if has_not_matched:
            return 0, 'not_eligible'
        if all_count == 0:
            return 0, 'eligible'
        score = int(float(eligible_count) * 100 / float(all_count))
        return score, ('potential' if has_unknown else 'eligible')

    def is_patient_info_attr_blank(self, patient_info_attr: str) -> bool:
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
        from exact_matching.therapy_match_profile import omop_therapy_enabled, omop_therapy_types_enabled

        therapy_codes = self.patient_info_attr.get_user_therapies()

        mismatch_status = self.therapy_related_things_mismatch_status()

        # Build the patient-side display maps keyed by whatever the trial columns hold
        # per the active profile, so match_required/excluded overlap correctly. Regimen
        # keys: concept_ids under OMOP, codes legacy. Component/type keys: OMOP (Phase P,
        # #234) — components are consumer-supplied concept_ids (get_user_therapy_component_ids),
        # types are consumer-supplied class concept_ids (#285 folded); legacy — the CB graph walk.
        # The regimen/component/category data access lives behind the data port
        # (E1.2b); DjangoMatcherData reproduces the previous inline logic 1:1.
        omop = omop_therapy_enabled()
        therapies, therapy_components_to_therapy, therapy_types_to_therapy = (
            self._data.build_therapy_display_maps(
                therapy_codes, self.patient_info_attr.get_user_therapy_component_ids, omop,
                get_class_ids=self.patient_info_attr.get_user_therapy_type_ids,
                omop_types=omop_therapy_types_enabled())
        )

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

        # FAIL-CLOSED types (#285/#286): under OMOP-types a required-type miss is a
        # hard not_matched (never 'unknown'), and the patient's class ids are
        # release-validated so the rendered status agrees with the verdict. Stale /
        # invalid keys are dropped from the required/excluded type map (so a stale
        # required id can't render 'matched'); if the patient carries any unvalidated
        # id, an excluded-type constraint renders not_matched (conservative, parity
        # with _match_omop_type_things). See type_release_gate.
        type_mismatch_status = mismatch_status
        types_map = therapy_types_to_therapy
        types_excluded_conservative = False
        if omop_therapy_types_enabled():
            type_mismatch_status = 'not_matched'
            raw_class_ids = self.patient_info_attr.get_user_therapy_type_ids()
            if raw_class_ids is not None:
                # Gate 1 release passed explicitly (this seam holds the attr), parity
                # with the verdict; Gate 2 per-concept validation as before.
                from trials.services.omop.type_release_gate import resolve_type_validation
                validated, has_unvalidated = resolve_type_validation(
                    raw_class_ids,
                    patient_release_id=self.patient_info_attr.get_user_therapy_release_id())
                types_map = {k: v for k, v in therapy_types_to_therapy.items() if k in validated}
                types_excluded_conservative = has_unvalidated

        out = {
            "therapiesRequired": match_required(getattr(self.trial, THERAPY_MATCH_PROFILE.therapies_required), therapies, mismatch_status),
            "therapiesExcluded": match_excluded(getattr(self.trial, THERAPY_MATCH_PROFILE.therapies_excluded), therapies),
            "therapyTypesRequired": match_required(getattr(self.trial, THERAPY_MATCH_PROFILE.therapy_types_required), types_map, type_mismatch_status),
            "therapyTypesExcluded": match_excluded(getattr(self.trial, THERAPY_MATCH_PROFILE.therapy_types_excluded), types_map),
            "therapyComponentsRequired": match_required(getattr(self.trial, THERAPY_MATCH_PROFILE.therapy_components_required), therapy_components_to_therapy, mismatch_status),
            "therapyComponentsExcluded": match_excluded(getattr(self.trial, THERAPY_MATCH_PROFILE.therapy_components_excluded), therapy_components_to_therapy),
        }
        # excluded: never drop an unvalidated id → conservative not_matched (parity).
        if types_excluded_conservative and getattr(self.trial, THERAPY_MATCH_PROFILE.therapy_types_excluded):
            out['therapyTypesExcluded']['status'] = 'not_matched'

        # OMOP code + title per criterion (additive). Resolve the TRIAL's required/excluded
        # concept_ids to OMOP names via OmopConcept for regimen + component. Types are OMOP
        # class concept_ids too (#285) but are NOT yet added to this omopConcepts display
        # block — a display-parity follow-up (behaviour unchanged from before the fold).
        if omop_therapy_enabled():
            for key, col in (
                ('therapiesRequired', THERAPY_MATCH_PROFILE.therapies_required),
                ('therapiesExcluded', THERAPY_MATCH_PROFILE.therapies_excluded),
                ('therapyComponentsRequired', THERAPY_MATCH_PROFILE.therapy_components_required),
                ('therapyComponentsExcluded', THERAPY_MATCH_PROFILE.therapy_components_excluded),
            ):
                out[key]['omopConcepts'] = _resolve_omop_concepts(getattr(self.trial, col))

        return out

    def attr_match_status(self, patient_info_attr: str) -> AttrMatchStatus:
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
        # A NULL therapy column means "no list constraint", same as []. Coalesce so
        # NULL short-circuits to 'matched' (symmetric with the SQL potential count's
        # NULL-as-empty handling, #4832) and so the len()/overlap below never see
        # None (a legacy NULL column would otherwise raise). null=True columns:
        # therapies_/therapy_types_/therapy_components_{required,excluded}.
        required_list = required_list or []
        excluded_list = excluded_list or []
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

    def _match_omop_type_things(self, type_values, required_list, excluded_list, has_no_prior_therapy):
        """OMOP drug-class TYPE overlap under the OMOP flag (#285, types folded in) with
        #286 per-concept release validation applied ASYMMETRICALLY.

        ``type_values`` is the patient's RAW class concept_ids: ``None`` = unknown,
        ``[]`` = known-empty, a list = the ids. Required uses only ids validated at
        the pinned vocab-mirror release (stale dropped → fail-closed); excluded never
        drops an unvalidated id (dropping would re-open the exclusion = fail-OPEN), so
        a trial that excludes types is not_matched when the patient carries any
        unvalidated id. See trials.services.omop.type_release_gate.
        """
        if required_list == [] and excluded_list == []:
            return 'matched'
        # #285: unknown patient class ids (None). A required type is a hard
        # not_matched; otherwise preserve the prior 'unknown' vs (no-prior →)
        # 'matched' semantics — there are no concrete ids to validate.
        if type_values is None:
            if required_list:
                return 'not_matched'
            if not has_no_prior_therapy:
                return 'unknown'
            return 'matched'
        # Concrete patient class ids ([] or a list) → #286 per-concept validation, plus
        # Gate 1 release-consistency (release passed explicitly — this seam holds the attr).
        from trials.services.omop.type_release_gate import resolve_type_validation
        validated, has_unvalidated = resolve_type_validation(
            type_values, patient_release_id=self.patient_info_attr.get_user_therapy_release_id())
        # excluded: never drop an unvalidated id → conservatively reject.
        if excluded_list and has_unvalidated:
            return 'not_matched'
        if len(get_overlap(sorted(validated), excluded_list)) > 0:
            return 'not_matched'
        # required: only validated ids count (stale dropped → fail-closed).
        if len(required_list) > 0 and len(get_overlap(sorted(validated), required_list)) == 0:
            return 'not_matched'
        return 'matched'

    def _match_therapy_related_things(self, values, has_no_prior_therapy):
        results = []
        res = self._match_therapy_things(values, getattr(self.trial, THERAPY_MATCH_PROFILE.therapies_required), getattr(self.trial, THERAPY_MATCH_PROFILE.therapies_excluded), has_no_prior_therapy)
        if res == 'not_matched':
            return res
        results.append(res)

        # Component + type (class) values. OMOP mode (Phase P, #234): components are the
        # consumer-supplied pre-expanded concept_ids (get_user_therapy_component_ids); types
        # are the consumer's pre-expanded class concept_ids (#285 folded types into OMOP).
        # Legacy: derived from the regimen via the CB graph. See #197 / therapy_graph.
        from exact_matching.therapy_match_profile import omop_therapy_enabled, omop_therapy_types_enabled
        # OMOP only: read the consumer-supplied components. Gated so the legacy path
        # does no OMOP-specific work (byte-identical to CB). Under OMOP (#285, types folded
        # in) also read the consumer's pre-expanded drug-class concept_ids for types.
        patient_component_ids = (self.patient_info_attr.get_user_therapy_component_ids()
                                 if omop_therapy_enabled() else None)
        patient_class_ids = (self.patient_info_attr.get_user_therapy_type_ids()
                             if omop_therapy_types_enabled() else None)
        component_codes, therapy_types = self._data.derive_component_and_type_values(
            values, patient_component_ids, patient_class_ids)

        res = self._match_therapy_things(component_codes, getattr(self.trial, THERAPY_MATCH_PROFILE.therapy_components_required), getattr(self.trial, THERAPY_MATCH_PROFILE.therapy_components_excluded), has_no_prior_therapy)
        if res == 'not_matched':
            return res
        results.append(res)

        type_required = getattr(self.trial, THERAPY_MATCH_PROFILE.therapy_types_required)
        type_excluded = getattr(self.trial, THERAPY_MATCH_PROFILE.therapy_types_excluded)
        # OMOP-types (#285/#286): class-concept_id overlap, FAIL-CLOSED on unknown
        # patient classes, with per-concept release validation applied asymmetrically
        # (see _match_omop_type_things). Legacy path is byte-identical to before.
        if omop_therapy_types_enabled():
            res = self._match_omop_type_things(therapy_types, type_required, type_excluded, has_no_prior_therapy)
        else:
            res = self._match_therapy_things(therapy_types, type_required, type_excluded, has_no_prior_therapy)
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

        # "Unknown" is sent from the UI as an empty string (see
        # ValueOptions.progressions). `is_blank` also covers a value outside the
        # closed domain (#5026) — an unrecognised value is not an answer, and the
        # SQL filter already treats it as one it cannot act on, so returning
        # 'not_matched' for it manufactured false negatives.
        if ctx.is_blank:
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

    def _match_supportive_therapies(self, ctx):
        """Enforce the trial's supportive_therapies_required/excluded pair (#4449).

        Mirrors the search-side eligible_for_supportive_therapies: no requirement
        -> matched; patient hasn't answered -> unknown; required with no overlap or
        excluded with overlap -> not_matched. Supportive codes still feed
        first_line_therapy via get_user_therapies(), so the legacy lines behaviour
        is unchanged.
        """
        supportive_required = getattr(self.trial, THERAPY_MATCH_PROFILE.supportive_therapies_required)
        supportive_excluded = getattr(self.trial, THERAPY_MATCH_PROFILE.supportive_therapies_excluded)
        if not supportive_required and not supportive_excluded:
            return 'matched'
        codes = self.patient_info_attr.get_supportive_therapy_codes()
        if ctx.is_blank or not codes:
            return 'unknown'
        if len(supportive_required) > 0 and len(get_overlap(codes, supportive_required)) == 0:
            return 'not_matched'
        if len(supportive_excluded) > 0 and len(get_overlap(codes, supportive_excluded)) > 0:
            return 'not_matched'
        return 'matched'

    def _match_therapy_lines_attr(self, ctx):
        if ctx.name == 'first_line_therapy':  # calc things for just one line of therapy
            therapies = self.patient_info_attr.get_user_therapies()
            if len(therapies) == 0:
                therapies = None
            return self._match_therapy_related_things(therapies, ctx.has_no_prior_therapy)
        else:
            return 'matched'

    def _match_computed_attr(self, ctx):
        # "Named OR" criteria attrs (e.g. high-risk MCL) need count/sufficient/
        # excluded semantics that the generic per-subattr overlap can't express.
        if ctx.meta.get("criteria_count_match"):
            return self._match_criteria_count(ctx.meta)

        matching_results = []

        for trial_subattr_name, uvalue_function in ctx.meta["uvalue_function"].items():
            matching_result = self._match_computed_subattr(trial_subattr_name, uvalue_function, ctx.is_blank)
            matching_results.append(matching_result)

        if 'not_matched' in matching_results:
            return 'not_matched'
        if 'unknown' in matching_results:
            return 'unknown'
        return 'matched'

    def _match_criteria_count(self, trial_attr_meta):
        """Three-valued match for "named OR" criteria attributes (e.g. high-risk MCL).

        Distinguishes a true unknown (source data for a required criterion was
        never entered) from a confirmed "none" (sources entered, criterion
        absent). See #4399. The verdict combines up to three rule sets:

        - required (inclusion) with an optional per-trial minimum count; a null
          / absent min_count means "any" (effectively 1).
        - sufficient_any (#4402): any single one of these criteria satisfies
          inclusion on its own, regardless of min_count. ORed with the required
          rule.
        - excluded (#4401): a patient with any excluded criterion is gated out;
          an undeterminable excluded criterion keeps the trial Potential. ANDed
          with the inclusion decision.

        Each rule is evaluated three-valued; the aggregate follows the generic
        computed matcher precedence — not_matched wins, then unknown, else
        matched.
        """
        derived_csv = trial_attr_meta["criteria_derived"](self.patient_info)
        derived = {c.strip() for c in (derived_csv or '').split(',') if c.strip()}

        def rule_status(codes, threshold):
            """Three-valued status for "patient meets >= threshold of codes"."""
            if not codes:
                return None
            unknown_codes = trial_attr_meta["criteria_unknown_codes"](self.patient_info_attr, codes)
            matched = sum(1 for c in codes if c in derived)
            unknown = sum(1 for c in codes if c not in derived and c in unknown_codes)
            if matched >= threshold:
                return 'matched'
            if matched + unknown >= threshold:
                return 'unknown'
            return 'not_matched'

        statuses = []

        # Inclusion: required (>= min_count) OR sufficient_any (>= 1).
        min_count_attr = trial_attr_meta.get("criteria_min_count_attr")
        min_count = getattr(self.trial, min_count_attr, None) if min_count_attr else None
        if not min_count or min_count < 1:
            min_count = 1
        required = getattr(self.trial, trial_attr_meta["criteria_required_attr"], None) or []

        sufficient_attr = trial_attr_meta.get("criteria_sufficient_any_attr")
        sufficient_any = getattr(self.trial, sufficient_attr, None) or [] if sufficient_attr else []

        inclusion_rules = [s for s in (rule_status(required, min_count), rule_status(sufficient_any, 1)) if s is not None]
        if inclusion_rules:
            if 'matched' in inclusion_rules:
                statuses.append('matched')
            elif 'unknown' in inclusion_rules:
                statuses.append('unknown')
            else:
                statuses.append('not_matched')

        # Exclusion (any one excluded criterion gates the patient out).
        excluded_attr = trial_attr_meta.get("criteria_excluded_attr")
        excluded = getattr(self.trial, excluded_attr, None) or [] if excluded_attr else []
        excl_status = rule_status(excluded, 1)
        if excl_status == 'matched':
            statuses.append('not_matched')
        elif excl_status == 'unknown':
            statuses.append('unknown')

        if 'not_matched' in statuses:
            return 'not_matched'
        if 'unknown' in statuses:
            return 'unknown'
        return 'matched'

    def high_risk_mcl_criteria_breakdown(self):
        """Per-criterion explainability for the high-risk MCL attribute (#4408).

        Returns the aggregate verdict plus, for each of the required / excluded /
        sufficient_any lists, the per-criterion status: 'matched' (patient has
        it), 'unknown' (its source data is missing) or 'not_matched' (confirmed
        absent). Titles are intentionally omitted — the UI maps codes to titles
        via the high-risk-mcl-criteria options. Returns None for a trial that
        gates on no high-risk criteria.
        """
        meta = self.mapping['high_risk_mcl_criteria']
        required = getattr(self.trial, meta['criteria_required_attr'], None) or []
        excluded = getattr(self.trial, meta['criteria_excluded_attr'], None) or []
        sufficient_any = getattr(self.trial, meta['criteria_sufficient_any_attr'], None) or []
        if not (required or excluded or sufficient_any):
            return None

        derived_csv = meta['criteria_derived'](self.patient_info)
        derived = {c.strip() for c in (derived_csv or '').split(',') if c.strip()}

        def code_status(code, is_exclude):
            """Per-criterion status in eligibility terms (consistent with the
            aggregate verdict). For an excluded criterion, presence is
            disqualifying so it reads not_matched, and confirmed absence reads
            matched — the inverse of an inclusion criterion."""
            if code in derived:
                return 'not_matched' if is_exclude else 'matched'
            unknown = meta['criteria_unknown_codes'](self.patient_info_attr, [code])
            if code in unknown:
                return 'unknown'
            return 'matched' if is_exclude else 'not_matched'

        def breakdown(codes, is_exclude=False):
            return [{'code': c, 'status': code_status(c, is_exclude)} for c in codes]

        min_count_attr = meta.get('criteria_min_count_attr')
        min_count = getattr(self.trial, min_count_attr, None) if min_count_attr else None
        if not min_count or min_count < 1:
            min_count = 1

        return {
            'aggregate': self.attr_match_status('high_risk_mcl_criteria'),
            'minCount': min_count,
            'matchedCount': sum(1 for c in required if c in derived),
            'required': breakdown(required),
            'excluded': breakdown(excluded, is_exclude=True),
            'sufficientAny': breakdown(sufficient_any),
        }

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
        elif ctx.is_blank:
            # Blank/unresolved (e.g. a computed bool like meets_crab whose inputs
            # aren't in yet) vs a trial that requires it: unknown, not a hard fail.
            # The SQL path already treats this as potential (filter_by_patient_info
            # skips blank attrs; potential_attrs_to_check counts them), and a
            # definite False (not blank) still falls through to not_matched.
            # (#4832 reconcile.)
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

        abs_vals_match_res = min_max_match(trial_attr_value_min, trial_attr_value_max, ctx.value, ctx.is_blank, sane_range=ctx.meta.get("sane_range"))
        if "uln_attr_min" in ctx.meta and "uln_attr_max" in ctx.meta:
            trial_attr_value_uln_min = getattr(self.trial, ctx.meta["uln_attr_min"])
            trial_attr_value_uln_max = getattr(self.trial, ctx.meta["uln_attr_max"])
            user_attr_value_uln = self.patient_info_attr.get_uln_value(ctx.name)
            user_attr_value_uln_is_blank = ctx.is_blank or user_attr_value_uln is None
            # ULN is the only pair read with equal-bounds as a ceiling (cb#4863) —
            # keep in step with the search-side flag; the absolute pair above does not.
            uln_vals_match_res = min_max_match(trial_attr_value_uln_min, trial_attr_value_uln_max, user_attr_value_uln, user_attr_value_uln_is_blank, equal_bounds_are_ceiling=True)
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
