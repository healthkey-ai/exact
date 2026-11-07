import csv
import io as _io
import logging
import re
from typing import TYPE_CHECKING, Optional

from django.db.models import Case, F, IntegerField, Prefetch, QuerySet, Value, When
from django.http import StreamingHttpResponse
from django.utils import timezone
from django.utils.functional import SimpleLazyObject, cached_property
from rest_framework import viewsets, filters, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import serializers
from rest_framework.exceptions import APIException
from rest_framework.views import APIView

from trials.api.pagination import TrialsPagination, count_over_keys
from trials.api.utils import csv_safe_cell
from trials.api.trials_serializers import TrialSerializer, TrialDetailsSerializer
from trials.models import Trial, Location, LocationTrial, PreferredCountry, State
from trials.services.blank_attribute_records_count import BlankAttributeRecordsCount
from trials.services.patient_info.resolve import resolve_patient_info
from trials.services.study_preferences import StudyPreferences, study_preferences_from_query_params
from trials.services.value_options import ValueOptions

logger = logging.getLogger(__name__)

# A trial id is ASCII digits only (see TrialsViewSet._trial_id) and fits the
# bigint `id` column; anything wider makes PostgreSQL cast the column to
# numeric and lose the primary-key index.
_TRIAL_ID_RE = re.compile(r'\d{1,19}', re.ASCII)
_MAX_TRIAL_ID = 2 ** 63 - 1


#: Excel refuses a cell past 32,767 characters, and a trial with several
#: hundred sites — ordinary — can produce one. Cut well short of the limit so
#: the suffix explaining the cut fits inside it too.
EXPORT_CELL_LIMIT = 30_000


def _clip(text: str) -> str:
    """One cell, short enough for a spreadsheet to accept.

    Applies to scalars as much as to joined lists: `brief_title` and
    `official_title` are unbounded `TextField`s, and one long enough takes the
    whole file down with it. Saying the cell was cut matters for the same
    reason the export's own sentinel does — silently shorter reads as
    genuinely shorter.
    """
    if len(text) <= EXPORT_CELL_LIMIT:
        return text
    dropped = len(text) - EXPORT_CELL_LIMIT
    return text[:EXPORT_CELL_LIMIT] + f' … and {dropped} more characters'


def _export_cell(value):
    """One serialized field, as a CSV cell.

    Lists are joined rather than written as their Python repr: `phase` and
    `location` arrive as arrays, and `['PHASE2']` in a spreadsheet column is
    not something a reader can do anything with. `None` is an empty cell, not
    the string "None" — a spreadsheet reads the latter as data.

    Free text goes through `csv_safe_cell`; numbers are handed back as numbers
    so the spreadsheet can still sum them, and so a negative one is not quoted
    into text by its leading `-`.
    """
    if value is None:
        return ''
    if isinstance(value, (list, tuple)):
        # Each element neutralised before the join, not the joined string: a
        # payload in the SECOND location would otherwise sail through.
        parts = [str(csv_safe_cell(v)) for v in value if v is not None]
        joined = '; '.join(parts)
        if len(joined) <= EXPORT_CELL_LIMIT:
            return joined
        # Cut, and SAYING it was cut. A cell that Excel refuses takes the whole
        # file down with it; one that quietly ends mid-list reads as a trial
        # with fewer sites than it has.
        kept: list[str] = []
        length = 0
        for part in parts:
            if length + len(part) + 2 > EXPORT_CELL_LIMIT:
                break
            kept.append(part)
            length += len(part) + 2
        return '; '.join(kept) + f'; … and {len(parts) - len(kept)} more'

    if isinstance(value, bool):
        return 'yes' if value else 'no'
    if isinstance(value, (int, float)):
        return value
    # Clipped BEFORE neutralising, so the apostrophe a formula-looking value
    # gains is not what pushes the cell over the limit.
    return csv_safe_cell(_clip(value if isinstance(value, str) else str(value)))


def _export_mcl_cell(trial, patient_info, evaluation=None) -> str:
    """The high-risk MCL rule, flattened into one cell.

    Only for a trial that names criteria — the matcher is built per row here,
    and constructing one for every trial in an export to be told "no criteria"
    is a cost paid on the disease's whole corpus. The three lists are plain
    columns, so the question is answered before the matcher exists.

    Under typed matching the answer comes from the request evaluation the list
    already ran, never from a second legacy matcher that could disagree with
    the screen (#692).
    """
    if evaluation is not None:
        breakdown = evaluation.high_risk_breakdown(trial.pk)
        if not breakdown:
            return ''
        parts = [f"verdict={breakdown['status']}"]
        if breakdown.get('knownViolation'):
            parts.append('known violation')
        for item in breakdown.get('fields', ()):
            parts.append(f"{item['field']}={item['status']}")
        return ' | '.join(parts)
    if patient_info is None:
        return ''
    if not any((
        trial.high_risk_mcl_criteria_required,
        trial.high_risk_mcl_criteria_excluded,
        trial.high_risk_mcl_criteria_sufficient_any,
    )):
        return ''
    from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
    breakdown = UserToTrialAttrMatcher(trial=trial, patient_info=patient_info).high_risk_mcl_criteria_breakdown()
    if not breakdown:
        return ''
    required = breakdown.get('required') or []
    sufficient = breakdown.get('sufficientAny') or []
    excluded = breakdown.get('excluded') or []

    def inclusion(code_status):
        # The server reports in ELIGIBILITY terms and this file has no legend,
        # so the words are the reader's: `matched` here means they have it.
        return {
            'matched': 'you have this',
            'not_matched': 'you do not have this',
            'unknown': 'not known from your data',
        }.get(code_status['status'], code_status['status'])

    def exclusion(code_status):
        # The inverse, which is the whole reason this is not one shared map:
        # on an excluded criterion `matched` means CONFIRMED ABSENT, so
        # printing the raw word tells the reader the opposite of the truth.
        return {
            'matched': 'you are clear of this',
            'not_matched': 'you have this — it rules this trial out',
            'unknown': 'not known from your data',
        }.get(code_status['status'], code_status['status'])

    parts = [f"verdict={breakdown['aggregate']}"]
    if required:
        # "Either" when the trial also names alternatives, because the server
        # ORs the two inclusion rules — calling it a requirement would tell a
        # patient who qualifies through the alternatives that they failed
        # something.
        head = 'either' if sufficient else 'requires'
        parts.append(
            f"{head} at least {breakdown.get('minCount', 1)} of these "
            f"(you have {breakdown.get('matchedCount', 0)}): "
            + ', '.join(f"{c['code']} — {inclusion(c)}" for c in required)
        )
    if sufficient:
        head = 'or any one of these' if required else 'any one of these qualifies'
        parts.append(f"{head}: " + ', '.join(f"{c['code']} — {inclusion(c)}" for c in sufficient))
    if excluded:
        parts.append(
            'rules the trial out: '
            + ', '.join(f"{c['code']} — {exclusion(c)}" for c in excluded)
        )
    return ' | '.join(parts)


if TYPE_CHECKING:
    from trials.services.patient_info.patient_info import PatientInfo


# ---------------------------------------------------------------------------
# Inline serializers for lookup tables
# ---------------------------------------------------------------------------

class CountrySerializer(serializers.ModelSerializer):
    class Meta:
        model = PreferredCountry
        fields = ['id', 'code', 'title']


class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = ['id', 'title', 'city', 'state_id', 'country_id']

    def to_representation(self, instance):
        response = super().to_representation(instance)
        response['state'] = instance.state.title if instance.state else None
        response['country'] = instance.country.title if instance.country else None
        return response


# ---------------------------------------------------------------------------
# Trials ViewSet
# ---------------------------------------------------------------------------

class TrialsViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    default_serializer_class = TrialDetailsSerializer
    serializer_classes = {
        'list': TrialSerializer,
        'search': TrialSerializer,
        'retrieve': TrialDetailsSerializer,
    }
    filter_backends = [filters.SearchFilter]
    search_fields = ['brief_title', 'official_title']
    pagination_class = TrialsPagination

    def dispatch(self, request, *args, **kwargs):
        # Pin one vocab-mirror release for the whole request (OMOP mode only), so
        # every mirror read (title resolution) sees one generation and can never
        # straddle an activation mid-request (#252 / ADR 0002). finalize_response
        # runs inside this block, so the header sees the same context.
        from vocab_mirror.release_context import MatchingReleaseContext
        from trials.services.omop.component_category_lookup import component_lookup_request_cache
        from trials.services.omop.type_release_gate import type_validation_request_cache
        from trials.services.omop.patient_release_gate import patient_release_check_request_cache
        # component_lookup_request_cache: dedup the per-trial component→type lookups
        # within this request without a process-global cache that could go stale in
        # web workers after a sync-job rebuild (ADR 0002 guard #8, #266).
        # type_validation_request_cache: same, for the #286 per-concept class-id
        # release validation (one mirror query per class-id set per request).
        # patient_release_check_request_cache: same, for the #286 Gate 1 aggregate
        # patient-release check (one decision per request; observe-only until enforced).
        with MatchingReleaseContext() as ctx, component_lookup_request_cache(), \
                type_validation_request_cache(), patient_release_check_request_cache():
            self._release_ctx = ctx
            response = super().dispatch(request, *args, **kwargs)
        # A streaming response outlives this block: the generator body runs
        # when the server consumes it, after the release pin and the request
        # caches have been reset. Re-enter the SAME release id for the stream,
        # so the CSV is built against the generation its header names instead
        # of whatever is active by then (#692).
        if getattr(response, 'streaming', False):
            response.streaming_content = self._streaming_response_with_context(
                response.streaming_content,
                ctx,
                (
                    component_lookup_request_cache,
                    type_validation_request_cache,
                    patient_release_check_request_cache,
                ),
            )
        return response

    @staticmethod
    def _streaming_response_with_context(content, release_ctx, cache_factories):
        """Run a streaming body inside the request's release pin and caches."""
        from contextlib import ExitStack

        def stream():
            with ExitStack() as stack:
                if release_ctx is not None:
                    stack.enter_context(release_ctx)
                for factory in cache_factories:
                    stack.enter_context(factory())
                yield from content

        return stream()

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        ctx = getattr(self, '_release_ctx', None)
        if ctx is not None and ctx.header:
            from vocab_mirror.release_context import RELEASE_HEADER
            response[RELEASE_HEADER] = ctx.header
        return response

    def _resolve_patient_info(self) -> Optional['PatientInfo']:
        # Resolve at most once per request: get_queryset and
        # get_serializer_context both call this, and `search` can hit it
        # several times. Re-resolving re-runs the PROMOP round-trip, so a
        # slow upstream multiplies per request (#159, #160). The holder is a
        # 1-tuple so a legitimately-resolved None is still cached.
        #
        # Memoize on the underlying HttpRequest, not on the DRF Request:
        # `clone_request` (the browsable renderer builds forms through it)
        # hands the view a *different* Request wrapping the same HttpRequest,
        # so a DRF-Request memo misses on every clone — measured at three
        # PROMOP round-trips for one browser GET.
        store = getattr(self.request, '_request', self.request)
        holder = getattr(store, '_exact_patient_info', None)
        if holder is not None:
            if isinstance(holder[0], APIException):
                # A failure is memoized too, so an unreachable PROMOP costs one
                # timeout per request rather than one per call site — but it is
                # NOT re-raised. Reaching this branch means the exception has
                # already been through handle_exception and become the response;
                # the only caller left is DRF's browsable renderer, which clones
                # the request and re-enters the view to build its form *during*
                # rendering. A raise there escapes response rendering and Django
                # turns the designed 502 into a 500. The status is already
                # decided, so tell the renderer what it is actually asking —
                # there is no patient — and let the real error render.
                logger.debug(
                    'patient context already failed this request (%s); '
                    'returning None to the second caller',
                    type(holder[0]).__name__,
                )
                return None
            return holder[0]

        data = getattr(self.request, 'data', None)
        has_inline = isinstance(data, dict) and bool(data.get('patient_info'))

        try:
            patient_info = resolve_patient_info(self.request)
        except APIException as exc:
            # Already an HTTP-meaningful response (e.g. PermissionDenied from
            # the person_id IDOR gate #150, or PatientContextUnavailable when a
            # named patient can't be fetched #448) — let DRF render it as-is.
            store._exact_patient_info = (exc,)
            raise
        except Exception:
            # Don't swallow into a silent None: that would run the matcher with
            # no patient context and return an unfiltered/unscored trial list
            # that looks valid — dangerous in a clinical matcher (#156).
            logger.exception('Failed to build patient_info from request payload')
            # Only a supplied inline payload that fails to build is client error
            # (400). The person_id path raises its own HTTP-meaningful errors
            # (400 for a malformed id, 502 when PROMOP can't supply the patient
            # — #448), all of them APIException and so already re-raised above;
            # anything still arriving here from that path is a real
            # server/upstream bug — let it propagate as a 500 rather than
            # masking it as a misleading 400.
            if has_inline:
                raise serializers.ValidationError(
                    {'patient_info': 'Could not build patient context from the supplied payload.'}
                )
            raise

        store._exact_patient_info = (patient_info,)
        return patient_info

    def _resolve_study_preferences(self) -> StudyPreferences:
        return study_preferences_from_query_params(self.request.query_params)

    #: The most bookmarks one request may filter by. Every id becomes part of
    #: an `IN (...)`, so an unbounded list expands into thousands of clauses.
    MAX_TRIAL_IDS = 500

    def _reject_user_scoped_search_type(self, search_type):
        """EXACT holds no per-user trial state; these types cannot narrow.

        `favorites` would filter a `favorite` field the Trial model does not
        have (a FieldError surfacing as 500), and `my_trials` / `not_eligible`
        match no branch, so they return the whole corpus under a name that
        promises a subset. The federated UI's Favorites tab sends a bounded
        `trial_ids` list instead; `all` stays allowed because the unnarrowed
        corpus is what it names.
        """
        if search_type in ('favorites', 'my_trials'):
            raise serializers.ValidationError({
                'type': [
                    f"'{search_type}' is not supported: EXACT stores no per-user "
                    f"trial state. POST the ids to filter by as `trial_ids` "
                    f"instead — see /trials/search/match/."
                ]
            })
        if search_type == 'not_eligible':
            raise serializers.ValidationError({
                'type': [
                    "'not_eligible' is not supported: the queryset has no such "
                    "narrowing, and the value used to return the eligible and "
                    "potential trials instead — the inverse of what it names."
                ]
            })

    def _resolve_trial_ids(self):
        """The `trial_ids` filter from the request body, or None.

        The federated UI's Favorites/Registered tabs keep their ids in PROMOP
        and send them here; the narrowing must happen before counting,
        sorting and pagination so the total agrees with the rows.

        `None` means no filter. An EMPTY LIST means the caller has no saved
        trials, so the honest answer is no trials — not the whole corpus.
        """
        if 'trial_ids' in self.request.query_params:
            raise serializers.ValidationError({
                'trial_ids': [
                    'send trial_ids in the request body, not the query string '
                    '— see POST /trials/search/match/.'
                ]
            })

        data = getattr(self.request, 'data', None)
        if not isinstance(data, dict) or 'trial_ids' not in data:
            return None

        raw = data.get('trial_ids')
        if not isinstance(raw, (list, tuple)):
            raise serializers.ValidationError(
                {'trial_ids': ['must be a list of trial ids.']}
            )
        if len(raw) > self.MAX_TRIAL_IDS:
            raise serializers.ValidationError({
                'trial_ids': [
                    f'at most {self.MAX_TRIAL_IDS} ids may be sent; got {len(raw)}.'
                ]
            })
        return [self._trial_id(value) for value in raw]

    @staticmethod
    def _trial_id(value):
        """One id, or a 400.

        `True` is an int in Python and `int(3.5)` truncates, so a loose parse
        turns a boolean or a float into a DIFFERENT trial. Accept an int or an
        ASCII-digits string, and refuse a negative or out-of-bigint value.
        """
        if isinstance(value, bool) or isinstance(value, float):
            raise serializers.ValidationError(
                {'trial_ids': [f'{value!r} is not a trial id.']}
            )
        if isinstance(value, int):
            return TrialsViewSet._in_range(value, value)
        if isinstance(value, str) and _TRIAL_ID_RE.fullmatch(value.strip()):
            return TrialsViewSet._in_range(int(value), value)
        raise serializers.ValidationError(
            {'trial_ids': [f'{value!r} is not a trial id.']}
        )

    @staticmethod
    def _in_range(number, original):
        if 0 <= number <= _MAX_TRIAL_ID:
            return number
        raise serializers.ValidationError(
            {'trial_ids': [f'{original!r} is not a trial id.']}
        )

    def _distance_filter_status(self, patient_info):
        """Whether a requested radius was applied, for the response (#475).

        The queryset applies `distance` only with a patient location
        (`filter_by_study_info`); without one it falls through to the
        country/region filter, which is no filter at all when no country was
        sent. The caller who asked for trials within 50 miles then gets the
        whole corpus with a 200 and nothing to tell them so. This says it.
        `None` when no radius was asked for, so the key is absent then.
        """
        if not self._resolve_study_preferences().distance:
            return None
        if patient_info is None:
            return {'applied': False, 'reason': 'no_patient'}
        if not getattr(patient_info, 'geo_point', None):
            return {'applied': False, 'reason': 'no_patient_location'}
        return {'applied': True}

    def get_queryset(self, patient_info=None):
        # Before the patient round trip: a malformed body should not pay for a
        # PROMOP fetch to be told it is malformed.
        trial_ids = self._resolve_trial_ids()
        if self.action in ('list', 'count', 'search'):
            self._reject_user_scoped_search_type(
                self.request.query_params.get('type', None)
            )

        if patient_info is None:
            patient_info = self._resolve_patient_info()

        if self.action in ('list', 'count', 'search'):
            self._distance_filter = self._distance_filter_status(patient_info)

        from trials.services.matching.typed_runtime import resolve_evaluation
        evaluation = resolve_evaluation(self.request, patient_info)
        if evaluation is not None:
            return self._get_typed_queryset(evaluation, trial_ids)

        # trial_type is a FK serialized on every trial via StringRelatedField
        # (list, search, detail); without select_related the serializer fetches
        # it once per row — an N+1 (ported from CB perf(trials) select_related
        # trial_type). The JOIN is cheap and the `count` action ignores it, so
        # eager-load it on the base queryset.
        queryset = Trial.objects.select_related('trial_type')
        study_prefs = self._resolve_study_preferences()
        search_type = self.request.query_params.get('type', None)

        if trial_ids is not None:
            # Before the matcher: scores, ordering and the total are computed
            # over the set the caller asked about.
            queryset = queryset.filter(id__in=trial_ids)

        if self.action in ['list', 'count', 'search']:
            queryset, _ = queryset.filtered_trials(
                search_options=self.request.query_params,
                study_info=study_prefs,
                patient_info=patient_info,
                add_traces=False,
                search_type=search_type,
            )

        # A saved trial the patient no longer qualifies for is still their
        # saved trial. Dropping it made the Favorites tab read "1" over the
        # words "No trials found" (#568). It comes back here, and the
        # serializer marks it `not_eligible`; `sunk_first` keeps it below the
        # rows that still match.
        self._unmatched_saved_ids = frozenset()
        if self.action in ('list', 'search') and self._keeps_unmatched_saved(
            trial_ids, search_type,
        ):
            queryset = self._widened_saved(
                queryset, trial_ids, study_prefs, patient_info,
            )
        sunk_first = ('saved_not_matching',) if self._unmatched_saved_ids else ()

        if self.action in ['list', 'search', 'retrieve']:
            params = self.request.query_params
            try:
                benefit_weight = float(params.get('benefitWeight', 25.0))
                patient_burden_weight = float(params.get('patientBurdenWeight', 25.0))
                risk_weight = float(params.get('riskWeight', 25.0))
                distance_penalty_weight = float(params.get('distancePenaltyWeight', 25.0))
            except (TypeError, ValueError):
                benefit_weight = patient_burden_weight = risk_weight = distance_penalty_weight = 25.0
            queryset = queryset.with_goodness_score_optimized(
                benefit_weight=benefit_weight,
                patient_burden_weight=patient_burden_weight,
                risk_weight=risk_weight,
                distance_penalty_weight=distance_penalty_weight,
                geo_point=patient_info.geo_point if patient_info else None,
                recruitment_status=study_prefs.recruitment_status,
            )

        if self.action == 'retrieve':
            queryset = queryset.with_distance_optimized(
                patient_info.geo_point if patient_info else None,
                recruitment_status=study_prefs.recruitment_status,
            )

        if self.action in ('list', 'retrieve'):
            # Annotates `match_score` (and potential_attrs_count). The detail
            # page shows the Matching Score too, so `retrieve` needs this —
            # without it `TrialDetailsSerializer` reads `match_score` as None
            # and the detail page shows "N/A" for every trial.
            queryset = queryset.with_potential_attrs_count(patient_info)

        if self.action == 'list':
            queryset = queryset.order_by(
                *sunk_first, '-match_score', '-posted_date', 'id'
            )

        if self.action == 'search':
            counts = self._trials_counts(queryset, patient_info)
            queryset = queryset.with_potential_attrs_count(patient_info, search_type, counts)

            sort_by = self.request.query_params.get('sort', 'goodnessScore')
            avail_sorts = ('distance', 'status', 'phase', 'updated', 'enrollment',
                           'patientBurdenScore', 'goodnessScore', 'matchScore')
            if sort_by not in avail_sorts:
                sort_by = 'goodnessScore'

            order = []
            if sort_by == 'distance' and patient_info and patient_info.geo_point:
                if 'distance' not in queryset.query.annotations:
                    queryset = queryset.with_distance_optimized(
                        geo_point=patient_info.geo_point,
                        recruitment_status=study_prefs.recruitment_status,
                    )
                order.append(F('distance').asc(nulls_last=True))
            elif sort_by == 'status':
                queryset = queryset.with_status_code()
                order.append(F('status_code').asc(nulls_last=True))
            elif sort_by == 'phase':
                order.append(F('phase_code_min').asc(nulls_last=True))
            elif sort_by == 'updated':
                order.append(F('last_update_date').desc(nulls_last=True))
            elif sort_by == 'enrollment':
                order.append(F('enrollment_count').desc(nulls_last=True))
            elif sort_by == 'patientBurdenScore':
                order.append(F('patient_burden_score').asc(nulls_last=True))
            elif sort_by == 'matchScore':
                order.append(F('match_score').desc(nulls_last=True))
            else:  # goodnessScore
                order.append(F('goodness_score').desc(nulls_last=True))
            order.append(F('id').asc())
            queryset = queryset.order_by(*sunk_first, *order)

        if self.action in ['list', 'search']:
            queryset = queryset.prefetch_related(
                Prefetch('locationtrial_set',
                         queryset=LocationTrial.objects.select_related('location'))
            )

        return queryset

    def _get_typed_queryset(self, evaluation, trial_ids=None):
        """SQL visibility/search first, complete final evaluation second, pages last."""
        from datetime import date
        from collections import Counter
        from exact_matching.contracts import ContractError
        from exact_matching.request_evaluation import EvaluatedTrials
        from trials.services.matching.typed_runtime import attribute_counts, TypedMatchingUnavailable

        patient_info = evaluation.patient_info
        prefs = self._resolve_study_preferences()
        params = self.request.query_params
        search_type = params.get('type')
        queryset = Trial.objects.select_related('trial_type')
        if trial_ids is not None:
            queryset = queryset.filter(id__in=trial_ids)
        if self.action != 'retrieve':
            queryset, _ = queryset.candidate_trials(params, prefs, patient_info, search_type=search_type)
        # Apply DRF text search to ORM, never to an already finalized sequence.
        queryset = super().filter_queryset(queryset)
        try:
            weights = {field: float(params.get(param, 25.0)) for field, param in (
                ('benefit_weight', 'benefitWeight'), ('patient_burden_weight', 'patientBurdenWeight'),
                ('risk_weight', 'riskWeight'), ('distance_penalty_weight', 'distancePenaltyWeight'))}
        except (TypeError, ValueError):
            weights = dict.fromkeys(('benefit_weight', 'patient_burden_weight', 'risk_weight',
                                     'distance_penalty_weight'), 25.0)
        queryset = queryset.with_goodness_score_optimized(
            **weights, geo_point=patient_info.geo_point, recruitment_status=prefs.recruitment_status)
        if 'distance' not in queryset.query.annotations:
            queryset = queryset.with_distance_optimized(patient_info.geo_point, recruitment_status=prefs.recruitment_status)
        if params.get('sort') == 'status':
            queryset = queryset.with_status_code()
        queryset = queryset.prefetch_related(Prefetch(
            'locationtrial_set', queryset=LocationTrial.objects.select_related('location')))
        if self.action == 'retrieve':
            # DRF get_object still does PK lookup + object permissions; its single
            # authorized row is evaluated before serialization, even if violated.
            return queryset
        if search_type == 'favorites':
            queryset = queryset.filter(favorite=True)
        try:
            finalized = EvaluatedTrials(queryset, evaluation, search_type=search_type)
        except ContractError as error:
            raise TypedMatchingUnavailable({'code': str(error)}) from error
        self._typed_counts = attribute_counts(finalized, evaluation)
        self._typed_status_counts = dict(Counter(evaluation.result_for(row.pk).status for row in finalized))
        if self.action == 'list':
            return finalized.ordered(
                lambda row: (evaluation.result_for(row.pk).score, row.posted_date or date.min)
                if evaluation.result_for(row.pk).score is not None else None, reverse=True)
        sort = params.get('sort', 'goodnessScore')
        if sort == 'matchScore':
            return finalized.ordered(lambda row: evaluation.result_for(row.pk).score, reverse=True)
        if sort == 'distance':
            return finalized.ordered(lambda row: row.distance.m if getattr(row, 'distance', None) is not None else None)
        field, reverse = {
            'status': ('status_code', False), 'phase': ('phase_code_min', False),
            'updated': ('last_update_date', True), 'enrollment': ('enrollment_count', True),
            'patientBurdenScore': ('patient_burden_score', False),
        }.get(sort, ('goodness_score', True))
        return finalized.ordered(lambda row: getattr(row, field, None), reverse=reverse)

    def filter_queryset(self, queryset):
        from exact_matching.request_evaluation import EvaluatedTrials
        if isinstance(queryset, EvaluatedTrials):
            return queryset  # SQL search already ran before final evaluation.
        return super().filter_queryset(queryset)

    @cached_property
    def trials_counts(self):
        """How many trials leave each of the patient's blank attributes
        unanswered — what `attributesToFillIn` ranks by, and what decides
        whether a row reads `eligible` or `potential`.

        `get_serializer_context` used to put a literal `{}` here, and
        `potential_attrs_for_trial` loops over `counts.keys()`: the loop never
        ran, so `attributesToFillIn` was `[]` on every row and `matchingType`
        was the constant `eligible` — a claim about eligibility the matcher
        never made. The same trial could then read `eligible` in the list and
        `not_eligible` (or `potential`) on its detail page.

        Its own scope, not the request's queryset: the count answers "fill
        this in and N more trials can be judged", which is about the patient's
        matched corpus and not about the page. `type=all` deliberately widens
        it, as CB's `trials_counts` does — this is a port of it.
        """
        patient_info = self._resolve_patient_info()
        if patient_info is None:
            return {}

        search_type = self.request.query_params.get('type', None)
        if search_type == 'all':
            scope = Trial.objects.all()
        else:
            scope, _ = Trial.objects.all().filtered_trials(
                search_options={},
                study_info=self._resolve_study_preferences(),
                patient_info=patient_info,
                add_traces=False,
            )
        return self._trials_counts(scope, patient_info)

    #: The requested ids the eligibility filter dropped, which the serializer
    #: turns into `matchingType: not_eligible`. A frozenset so the default is
    #: a real answer ("none of them") rather than a missing attribute.
    _unmatched_saved_ids: frozenset = frozenset()

    #: Whether this view's rows can carry the `not_eligible` mark. False on the
    #: graph, which buckets its nodes on `match_score` alone and would file a
    #: widened trial under "fully matched".
    widens_saved_trials = True

    def _keeps_unmatched_saved(self, trial_ids, search_type) -> bool:
        """Whether this request lists saved trials the patient fails.

        - **This view can mark them.** See `widens_saved_trials`.
        - **Ids were asked for.** Without them this is the corpus search, and
          widening it would answer "which trials match me" with trials that
          do not.
        - **No explicit `?type=`.** `eligible` and `potential` name a verdict;
          handing back rows that hold neither answers a different question.
          `all` never reaches here — it skips the eligibility filter upstream.
        """
        return (
            self.widens_saved_trials
            and trial_ids is not None
            and search_type is None
        )

    def _widened_saved(self, matched, trial_ids, study_prefs, patient_info):
        """`matched`, plus the requested ids the eligibility filter dropped.

        Both sets are read off querysets rather than recomputed, so "not
        eligible" means exactly one thing: the filter that hides a trial is
        the same one that marks it. The difference is taken against the
        WIDENED set, not against the ids the caller sent — the two differ by
        the ids the reader's own filters dropped, and marking one of those
        `not_eligible` would blame the matcher for the reader's own choice.
        """
        matched_ids = set(matched.values_list('id', flat=True))
        if len(matched_ids) == len(set(trial_ids)):
            return matched

        widened, _ = Trial.objects.filter(id__in=trial_ids).filtered_trials(
            search_options=self.request.query_params,
            study_info=study_prefs,
            patient_info=patient_info,
            add_traces=False,
            search_type=None,
            narrow_by_patient=False,
        )
        self._unmatched_saved_ids = (
            frozenset(widened.values_list('id', flat=True)) - matched_ids
        )
        if not self._unmatched_saved_ids:
            return matched

        # Sunk to the bottom of whatever order the reader chose: every sort
        # ranks by a column the widened rows still hold, and the default one
        # is the `match_score` annotation, which reads 100 on a trial the
        # patient conflicts with — so the failing bookmark sorted first.
        return widened.annotate(
            saved_not_matching=Case(
                When(id__in=self._unmatched_saved_ids, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )

    def _trials_counts(self, queryset: QuerySet, patient_info: Optional['PatientInfo']) -> dict[str, int]:
        return BlankAttributeRecordsCount().counts(queryset, patient_info)

    def get_serializer_class(self):
        return self.serializer_classes.get(self.action, self.default_serializer_class)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        patient_info = self._resolve_patient_info()
        study_prefs = self._resolve_study_preferences()
        template = self.request.query_params.get('view', None)
        search_type = self.request.query_params.get('type', None)

        context.update({
            'patient_info': patient_info,
            'distance_units': study_prefs.distance_units,
            'recruitment_status': study_prefs.recruitment_status,
            # Lazily: the context is shared with `TrialDetailsSerializer`, which
            # never reads `counts`, so an eager value ran the corpus-wide
            # aggregate on every detail request and threw it away.
            'counts': SimpleLazyObject(lambda: self.trials_counts),
            'template': template,
            'search_type': search_type,
            'explain': self.request.query_params.get('explain', '').lower() == 'true',
            # Empty unless `get_queryset` widened a saved-ids search (#568).
            # `get_queryset` runs first on every action that builds a list, so
            # by the time a serializer asks, the answer is settled.
            'unmatched_trial_ids': self._unmatched_saved_ids,
        })
        from trials.services.matching.typed_runtime import resolve_evaluation
        holder = getattr(getattr(self.request, '_request', self.request), '_exact_typed_evaluation', None)
        evaluation = None if holder and isinstance(holder[0], APIException) else resolve_evaluation(self.request, patient_info)
        if evaluation is not None:
            context['typed_evaluation'] = evaluation
            context['counts'] = getattr(self, '_typed_counts', {})
        return context

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        data = serializer.data
        # #4408: per-criterion high-risk MCL explainability (detail view only;
        # None for trials that gate on no high-risk criteria, or without a patient).
        patient_info = self._resolve_patient_info()
        from trials.services.matching.typed_runtime import resolve_evaluation
        evaluation = resolve_evaluation(self.request, patient_info)
        if evaluation is not None:
            data['highRiskMclCriteriaBreakdown'] = evaluation.high_risk_breakdown(instance.pk)
        elif patient_info is not None:
            from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
            data['highRiskMclCriteriaBreakdown'] = (
                UserToTrialAttrMatcher(trial=instance, patient_info=patient_info)
                .high_risk_mcl_criteria_breakdown()
            )
        return Response(data)

    def get_paginated_response(self, data, extra_keys=None):
        assert self.paginator is not None
        if getattr(self, '_distance_filter', None) is not None:
            extra_keys = {**(extra_keys or {}), 'distanceFilter': self._distance_filter}
        if hasattr(self, '_typed_status_counts'):
            from exact_matching.request_evaluation import RESULT_SCHEMA
            extra_keys = {**(extra_keys or {}), 'schema': RESULT_SCHEMA,
                          'matchingCounts': self._typed_status_counts}
        return self.paginator.get_paginated_response(data, extra_keys=extra_keys)

    @action(methods=['get'], detail=False)
    def count(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        # The endpoint whose whole job is the count was the one still paying
        # for it: a country scope carries `.distinct()` from `by_location`, and
        # counting it row-wise measured 7.1s against 0.018s (#492).
        data = {'count': count_over_keys(queryset)}
        if getattr(self, '_distance_filter', None) is not None:
            data['distanceFilter'] = self._distance_filter
        if hasattr(self, '_typed_status_counts'):
            from exact_matching.request_evaluation import RESULT_SCHEMA
            data.update(schema=RESULT_SCHEMA, matchingCounts=self._typed_status_counts)
        return Response(data)

    @action(methods=['post'], detail=False, url_path='match')
    def match(self, request, *args, **kwargs):
        """POST alias for the list endpoint, identical response shape.

        Exists so the browser-side harness can carry `patient_info`
        in the request body. The Fetch spec forbids GET-with-body
        (`new Request('/trials/', { method: 'GET', body: '…' })`
        throws), and axios v1's XHR adapter silently drops the body
        on GET — both paths leave `resolve_patient_info` reading from
        an empty body and the matcher running with no patient context.
        Routing to `list` keeps the response shape stable; frontends
        that already speak `GET /trials/` keep working.
        """
        # Bind the action to 'list' so all the `self.action == 'list'`
        # branches in `get_queryset` fire — without this the queryset
        # would skip `with_potential_attrs_count` and `-match_score`
        # ordering.
        self.action = 'list'
        return self.list(request, *args, **kwargs)

    @action(methods=['post'], detail=True, url_path='match')
    def match_detail(self, request, *args, **kwargs):
        """POST alias for `retrieve`, carrying `patient_info` in the body.

        The detail endpoint needs patient context to render the eligibility
        table (`details.trialEligibilityAttributes` with the patient's
        `uvalue` / `matchingType`). `retrieve` is GET-only and GET-with-body
        is forbidden by the Fetch spec / silently dropped by axios's XHR
        adapter, so a host carrying an inline `patient_info` payload (the CB
        contract) can't reach it over GET. Mirror the list-level `match`
        action: bind to `retrieve` so `get_queryset` runs the retrieve
        annotations and `get_serializer_context` resolves the body's
        `patient_info`, then delegate. Hosts on the `?person_id=` path keep
        using plain `GET /trials/{pk}/`.
        """
        self.action = 'retrieve'
        return self.retrieve(request, *args, **kwargs)

    @action(methods=['get'], detail=False)
    def search(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(methods=['post'], detail=False, url_path='search/match',
            url_name='search-match')
    def search_match(self, request, *args, **kwargs):
        """POST alias for `search`, carrying `patient_info` in the body.

        The federated UI's default path is an inline patient payload, and the
        Fetch spec forbids GET-with-body (axios's XHR adapter also drops it),
        so the UI posts here. `match` routes to `list`, whose ordering ignores
        `?sort=`; binding `self.action = 'search'` keeps the search branches
        (sorting, tab counts) firing exactly as the GET path does.
        """
        self.action = 'search'
        return self.search(request, *args, **kwargs)

    #: What a row of the export holds, as (header, key into the serialized
    #: trial). The keys are the serializer's own, so the file says what the
    #: list said — a column that drifts from the screen is worse than a column
    #: that is missing, because nobody checks it against anything.
    EXPORT_COLUMNS = (
        ('Trial ID', 'trialId'),
        ('Study ID', 'studyId'),
        ('Title', 'briefTitle'),
        ('Official title', 'officialTitle'),
        ('Match', 'matchingType'),
        ('Matching score', 'matchScore'),
        ('Suitability score', 'goodnessScore'),
        ('Status', 'recruitingStatus'),
        ('Phase', 'phase'),
        ('Trial type', 'trialType'),
        ('Disease', 'disease'),
        ('Stage', 'stage'),
        ('Treatments', 'interventionTreatments'),
        ('Sponsor', 'sponsor'),
        ('Locations', 'location'),
        ('Distance', 'distance'),
        ('Distance units', 'distanceUnits'),
        ('Enrolment', 'enrollmentCount'),
        ('Posted', 'postedDate'),
        ('Last update', 'lastUpdateDate'),
        ('First enrolment', 'firstEnrolment'),
        ('Link', 'link'),
    )

    #: Computed per row rather than read from the serializer, and only for the
    #: disease that has them. Appended after the columns above.
    EXPORT_MCL_COLUMN = 'High-risk MCL criteria'

    #: Rows fetched per round trip while streaming. Bounds the memory the
    #: response holds at once without making a query per row.
    EXPORT_CHUNK = 200

    #: The last line of a complete file, and of one that stopped early. The
    #: status code cannot carry either — it was sent before the first row.
    #: Comma-free on purpose: a sentinel the csv writer has to quote is one
    #: nobody can grep for.
    EXPORT_END_MARKER = '# end of export —'
    EXPORT_INCOMPLETE_MARKER = '# EXPORT INCOMPLETE — this file stopped early'

    def _reject_unnarrowed_export(self):
        """Refuse an export that names nothing to export.

        A search with no patient and no filters is a page of rows; the same
        export is the entire table, uncapped, once per request. The three
        things that make it a real question — a patient, a set of ids, or an
        explicit `type=all` — are exactly the three an intentional caller
        already sends, so this refuses the accident rather than the use.
        """
        if self.request.query_params.get('type') == 'all':
            return
        if self._resolve_trial_ids() is not None:
            return
        if self._resolve_patient_info() is not None:
            return
        raise serializers.ValidationError({
            'detail': [
                'An export needs something to narrow it: a patient '
                '(`patient_info` in the body, or `?person_id=`), a `trial_ids` '
                'list, or an explicit `?type=all` for the whole catalog.'
            ]
        })

    @action(methods=['post'], detail=False, url_path='export')
    def export(self, request, *args, **kwargs):
        """The current search, as a CSV file.

        POST, like every other action that needs a patient: GET-with-body is
        forbidden by the Fetch spec and dropped by axios, and the inline
        `patient_info` payload is the federated remote's default path. The
        cost is that the browser cannot simply follow a link — the caller
        reads the body and saves it — which is the same trade already made
        for `match` and `search-match`.

        Bound to `search`, so the file is the list: the same filters, the same
        `?sort=`, the same `trial_ids` narrowing for a Favorites export, and
        the same 400 on `type=favorites` / `type=my_trials` — those name a
        search type that narrows nothing here, and a CSV headed "your
        bookmarks" holding the whole corpus is the worst place to answer a
        question with the wrong set.

        Streamed rather than assembled: an unfiltered export is the whole
        corpus, and building that in memory to hand back at once is how an
        export endpoint takes a server down. Nothing is capped — a truncated
        file that does not say it was truncated reads as a complete one, and
        this is a file someone takes to an appointment. Which is also why the
        last line says the file ended: the response headers go out before the
        first row, so a failure halfway through arrives as a 200 with a short
        file, indistinguishable from a small result. The sentinel is how a
        reader — or a script — can tell the difference.
        """
        self.action = 'search'
        self._reject_unnarrowed_export()
        queryset = self.filter_queryset(self.get_queryset())
        # Typed matching finalizes to `EvaluatedTrials`, a materialized
        # sequence with neither `select_related` nor `iterator`; the legacy
        # path still streams rows from the database. Both must export the same
        # list the screen shows (#692).
        from exact_matching.request_evaluation import EvaluatedTrials
        evaluated = isinstance(queryset, EvaluatedTrials)
        evaluation = getattr(queryset, 'evaluation', None) if evaluated else None
        if not evaluated:
            # The locations are prefetched by the `search` branch; the trial
            # type is a plain FK the serializer reads per row, which on a page
            # is one extra query and on an uncapped export is one per trial.
            queryset = queryset.select_related('trial_type')
        # One serializer for the whole stream: `to_representation` reads only
        # `self.context`, and rebuilding the field map per trial is the
        # dominant cost on a corpus-sized export.
        context = self.get_serializer_context()
        # `explain` reaches this from the query string and would run
        # `TrialMatchExplainer` for every row — three queries each, measured —
        # to produce a `matchReasons` the CSV has no column for. On the one
        # endpoint that is uncapped by design, that is the per-row-query
        # problem walking back in through a parameter.
        context['explain'] = False
        serializer = self.get_serializer_class()(context=context)

        def rows():
            buffer = _io.StringIO()
            writer = csv.writer(buffer)

            def flush():
                value = buffer.getvalue()
                buffer.seek(0)
                buffer.truncate(0)
                return value

            writer.writerow(
                [header for header, _ in self.EXPORT_COLUMNS] + [self.EXPORT_MCL_COLUMN]
            )
            # A BOM, because the audience is someone opening this in Excel —
            # which ignores `charset=utf-8` on a saved file and decodes as the
            # system codepage, turning every accented sponsor and location into
            # mojibake. `utf-8-sig` on the reading side strips it.
            yield '\ufeff' + flush()

            patient_info = context.get('patient_info')
            written = 0
            trials = queryset if evaluated else queryset.iterator(chunk_size=self.EXPORT_CHUNK)
            try:
                for trial in trials:
                    row = serializer.to_representation(trial)
                    writer.writerow(
                        [_export_cell(row.get(key)) for _, key in self.EXPORT_COLUMNS]
                        + [_export_cell(_export_mcl_cell(trial, patient_info, evaluation))]
                    )
                    written += 1
                    yield flush()
            except Exception:
                # The 200 is already spent. All that is left is to say the file
                # is short — and to log it, since the client sees a success.
                logger.exception('trials export failed after %s rows', written)
                writer.writerow([f'{self.EXPORT_INCOMPLETE_MARKER} after {written} trials'])
                yield flush()
                return
            writer.writerow([f'{self.EXPORT_END_MARKER} {written} trials'])
            yield flush()

        filename = f'trials-{timezone.now():%Y-%m-%d}.csv'
        response = StreamingHttpResponse(rows(), content_type='text/csv; charset=utf-8')
        # `filename*` as well as `filename`: the name is ASCII today, and a
        # reader whose browser honours only one of the two still gets it.
        response['Content-Disposition'] = (
            f'attachment; filename="{filename}"; filename*=UTF-8\'\'{filename}'
        )
        return response


# ---------------------------------------------------------------------------
# Lookup table ViewSets
# ---------------------------------------------------------------------------

class CountriesViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = CountrySerializer
    pagination_class = TrialsPagination
    filter_backends = [filters.SearchFilter]
    search_fields = ['title']

    def get_queryset(self):
        return PreferredCountry.objects.order_by(
            F('sort_key').asc(nulls_last=True), F('title')
        )


class LocationsViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = LocationSerializer
    pagination_class = TrialsPagination
    filter_backends = [filters.SearchFilter]
    search_fields = ['title']

    def get_queryset(self):
        qs = Location.objects.select_related('country', 'state')
        country_id = self.request.query_params.get('country_id')
        state_id = self.request.query_params.get('state_id')
        if country_id:
            qs = qs.filter(country_id=country_id)
        if state_id:
            qs = qs.filter(state_id=state_id)
        return qs.order_by('title')


class FormSettingsViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    DISEASE_NAME_TO_CODE = {
        'multiple myeloma': 'MM',
        'follicular lymphoma': 'FL',
        'breast cancer': 'BC',
        'chronic lymphocytic leukemia': 'CLL',
        'mantle cell lymphoma': 'MCL',
    }

    def list(self, request, *args, **kwargs):
        disease_param = request.query_params.get('disease', '')
        disease_code = self._normalize_disease_code(disease_param)
        value_options = ValueOptions()
        out = value_options.all_options()
        if disease_code:
            trial_types = ValueOptions.trial_types_by_disease_code(disease_code)
            out['trialType'] = {'options': ValueOptions.to_value_and_label(trial_types)}
            # Disease-aware treatment outcomes (#60 / #70 / CB #4137).
            # Without this override callers reading the union `therapyOutcome`
            # would still see IMWG-specific sCR / VGPR / MRD for BC patients.
            outcomes = value_options.therapy_outcomes_by_disease_code(disease_code)
            out['therapyOutcome'] = {'options': ValueOptions.to_value_and_label(outcomes)}
            # Per #63 / CB #4330: eight clinically disease-specific lists
            # were historically exposed as a single union to every patient.
            # Override each patient-facing union key with its per-disease
            # subset — key names match what `all_options()` already exposes
            # to the frontend (4 singular + 4 plural — preserved as-is for
            # back-compat). Trial-side `*Required` / `*Excluded` aliases
            # stay at the union (set by `trial_attributes.py` from a fresh
            # `ValueOptions().all_options()`), so trials can still require
            # any marker independent of the current patient's disease.
            disease_aware_overrides = {
                'flipiScore': value_options.flipi_scores_by_disease_code,
                'cytogenicMarkers': value_options.cytogenic_markers_by_disease_code,
                'molecularMarkers': value_options.molecular_markers_by_disease_code,
                'gelfCriteriaStatus': value_options.gelf_criteria_statuses_by_disease_code,
                'binetStages': value_options.binet_stages_by_disease_code,
                'richterTransformations': value_options.richter_transformations_by_disease_code,
                'tumorBurdens': value_options.tumor_burdens_by_disease_code,
                'diseaseActivities': value_options.disease_activities_by_disease_code,
            }
            for key, getter in disease_aware_overrides.items():
                out[key] = {'options': ValueOptions.to_value_and_label(getter(disease_code))}
        return Response(out)

    def _normalize_disease_code(self, disease_param: str) -> str:
        if not disease_param:
            return ''
        lower = disease_param.lower().strip()
        if lower.upper() in ('MM', 'BC', 'FL', 'CLL', 'MCL'):
            return lower.upper()
        return self.DISEASE_NAME_TO_CODE.get(lower, '')


class NormalizePromopRowView(APIView):
    """POST endpoint that exposes `normalize_promop_row` to authenticated
    callers. Takes a raw PROMOP `patient_info` row in the body and
    returns the same row with EXACT-shaped values for the fields that
    differ between systems (receptor statuses → codes, TNM strings →
    short codes, therapy-line outcomes → IDs, refractory status labels,
    lab-value fallbacks, etc.).

    Exists so the federation dev harness (and any other client that
    fetches PROMOP rows browser-side) can run the same normalization
    the server-side `?person_id=` resolver applies. Without this, an
    inline-fetch caller's `patient_info` reaches the matcher with raw
    PROMOP labels and a meaningful subset of fields silently reads as
    "unknown" — closes the limitation documented in PR #117.

    Same auth + token model as `/trials/`: `IsAuthenticated`, DRF
    Token. The function is pure / side-effect-free; the caller already
    holds the patient row from their own session-authenticated PROMOP
    fetch so this endpoint doesn't widen the IDOR surface tracked in
    #108.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        from trials.services.patient_info.promop_adapter import normalize_promop_row

        raw = request.data
        if not isinstance(raw, dict):
            return Response(
                {'detail': 'Body must be a JSON object representing one PROMOP patient_info row.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # `normalize_promop_row` mutates its argument in place; copy
        # first so we never alter caller-owned state. (`dict(raw)` is
        # a shallow copy — fine because the function only rewrites
        # top-level keys plus the `genetic_mutations` items, which the
        # function itself defensively copies via `m = dict(m)`.)
        normalized = normalize_promop_row(dict(raw))
        return Response(normalized)
