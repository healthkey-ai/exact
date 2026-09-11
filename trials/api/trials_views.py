import logging
import re
from typing import TYPE_CHECKING, Optional

from django.db.models import F, Prefetch, QuerySet
from rest_framework import viewsets, filters, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import serializers
from rest_framework.exceptions import APIException
from rest_framework.views import APIView

from trials.api.pagination import TrialsPagination
from trials.api.trials_serializers import TrialSerializer, TrialDetailsSerializer
from trials.models import Trial, Location, LocationTrial, PreferredCountry, State
from trials.services.blank_attribute_records_count import BlankAttributeRecordsCount
from trials.services.patient_info.resolve import resolve_patient_info
from trials.services.study_preferences import StudyPreferences, study_preferences_from_query_params
from trials.services.value_options import ValueOptions

logger = logging.getLogger(__name__)

#: A trial id as it may appear in a JSON string: ASCII digits, nothing else,
#: and at most as many as a bigint can hold. `str.isdigit()` is not
#: equivalent — see `TrialsViewSet._trial_id`.
#:
#: The length bound is not cosmetic: Python refuses to parse an integer past
#: a digit limit (4300 by default), so an unbounded match sends a 5000-digit
#: string into `int()` and the `ValueError` escapes the view as a 500 — the
#: same failure the ASCII match was added to close.
_TRIAL_ID_RE = re.compile(r'\d{1,19}', re.ASCII)

#: The largest value the `id` column can hold (`BigAutoField` -> signed
#: bigint). Anything above it is not merely a miss: PostgreSQL types the
#: constant as `numeric`, which coerces the column in the `IN` comparison and
#: gives up the primary-key index — so 500 such ids turn a bookmarks lookup
#: into a table scan.
_MAX_TRIAL_ID = 2 ** 63 - 1

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

    def _resolve_patient_info(self) -> Optional['PatientInfo']:
        # Resolve at most once per request: get_queryset and
        # get_serializer_context both call this, and `search` can hit it
        # several times. Re-resolving re-runs the CTOMOP round-trip, so a
        # slow upstream multiplies per request (#159, #160). The holder is a
        # 1-tuple so a legitimately-resolved None is still cached.
        holder = getattr(self.request, '_exact_patient_info', None)
        if holder is not None:
            return holder[0]

        data = getattr(self.request, 'data', None)
        has_inline = isinstance(data, dict) and bool(data.get('patient_info'))

        try:
            patient_info = resolve_patient_info(self.request)
        except APIException:
            # Already an HTTP-meaningful response (e.g. PermissionDenied from
            # the person_id IDOR gate, #150) — let DRF render it as-is.
            raise
        except Exception:
            # Don't swallow into a silent None: that would run the matcher with
            # no patient context and return an unfiltered/unscored trial list
            # that looks valid — dangerous in a clinical matcher (#156).
            logger.exception('Failed to build patient_info from request payload')
            # Only a supplied inline payload that fails to build is client error
            # (400). The CTOMOP fetch returns None on network failure (handled in
            # CtomopClient), so an exception on the person_id path is a real
            # server/upstream bug — let it propagate as a 500 rather than masking
            # it as a misleading 400.
            if has_inline:
                raise serializers.ValidationError(
                    {'patient_info': 'Could not build patient context from the supplied payload.'}
                )
            raise

        self.request._exact_patient_info = (patient_info,)
        return patient_info

    def _resolve_study_preferences(self) -> StudyPreferences:
        return study_preferences_from_query_params(self.request.query_params)

    def _reject_user_scoped_search_type(self, search_type):
        """`?type=favorites` / `?type=my_trials` have no meaning here yet.

        Both name a per-user relation to a trial, and EXACT holds no per-user
        state: the search types were inherited from CB, where `add_favorite()`
        / `add_for_participation()` annotate the queryset from `UserTrial`.
        Neither annotation exists here, so what the two types did instead was:

        - `favorites`: `queryset.filter(favorite=True)` against a field the
          Trial model does not have — a FieldError surfacing as a 500.
        - `my_trials`: no narrowing at all. `filtered_trials` routes both
          through `filter_for_admin`, which skips the eligibility filter, so
          the response was the whole corpus presented as the patient's
          registered trials — wrong in a way nothing on screen reveals.

        This rejection is permanent, not a placeholder. The real path has
        since landed and is a different shape: the favorites list lives in
        PROMOP and comes down as a bounded `trial_ids` filter, which narrows
        inside the queryset (`_resolve_trial_ids` below; the federated UI's
        Favorites tab uses it and never sends this type). Accepting
        `?type=favorites` would mean accepting a search type that narrows
        nothing — the plausible wrong answer this exists to prevent. So the
        400 stays, and names the path that works.

        `not_eligible` goes too, for the same reason wearing different
        clothes: it matches no branch in `add_potential_attrs_count`, so it
        fell through to no narrowing at all and returned the eligible +
        potential set — the exact inverse of its name. It was in the API
        docs, so a client could be asking for it today and rendering
        "trials you do not qualify for" over trials the patient does.

        `all` is deliberately left alone. It reaches the same admin branch
        and returns the same unnarrowed corpus, but that corpus is what it
        names — a caller asking for every trial gets every trial. The
        others promise a subset they cannot deliver.
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

    #: The most bookmarks one request may filter by. Every id becomes part
    #: of an `IN (...)`, so an unbounded list is a request that expands into
    #: thousands of clauses — and a caller with that many bookmarks needs a
    #: different feature, not a longer query.
    MAX_TRIAL_IDS = 500

    def _resolve_trial_ids(self):
        """The `trial_ids` filter from the request body, or None.

        This is how the federated UI's Favorites tab works: the bookmarks
        live in PROMOP, which knows nothing about matching, so the ids come
        down here and the narrowing happens inside the queryset — the only
        place that can sort and paginate them alongside the match scores,
        and produce a total that agrees with what it listed.

        Body, not query string: the list can be long and the patient payload
        is already travelling in the body on this path.

        `None` means "no such filter". An EMPTY LIST does not: it means the
        caller asked for their bookmarks and has none, so the honest answer
        is no trials. Treating `[]` as absent would answer an empty
        Favorites tab with the entire corpus.
        """
        # In the query string it is a mistake, and a quiet one: ignored, the
        # request answers a bookmarks question with the whole corpus — the
        # same wrong answer `[]` is guarded against, through another door.
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

        ids = []
        for value in raw:
            ids.append(self._trial_id(value))
        return ids

    @staticmethod
    def _trial_id(value):
        """One id, or a 400.

        Strict on purpose — every loose parse here turns into a filter the
        caller did not ask for:

        - `True` is an `int` in Python, so `int(True)` is trial 1.
        - `int(3.5)` is 3, so a float silently becomes a DIFFERENT trial
          rather than being refused.
        - `str.isdigit()` is true for characters `int()` then refuses —
          `'²'`, `'³'` — so guarding with it and converting afterwards
          raises `ValueError` out of the view as a 500, not a 400.
        - It is also true for fullwidth digits, where `int('１２３')` does
          succeed and quietly yields a different trial than the literal
          text the caller sent.

        Hence an explicit ASCII-digits match rather than a character
        predicate. Numeric strings are accepted because JSON from a browser
        routinely carries ids that way; a negative one is refused because a
        primary key is never negative, and admitting a sign is what let
        `'--5'` through the old guard.
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
        """Refuse an id the `id` column could not hold.

        Not pedantry about a value that simply would not match: above the
        bigint range PostgreSQL types the constant as `numeric`, coerces the
        column to compare, and stops using the primary-key index. A list of
        500 of those is a table scan wearing the shape of a bookmarks
        lookup.

        Negative is refused for its own reason — a primary key is never
        negative, and admitting a sign is what let `'--5'` past an earlier
        version of this guard.
        """
        if 0 <= number <= _MAX_TRIAL_ID:
            return number
        raise serializers.ValidationError(
            {'trial_ids': [f'{original!r} is not a trial id.']}
        )

    def get_queryset(self, patient_info=None):
        # Before `_resolve_patient_info`, which can be a round trip to
        # PROMOP: a malformed body should not pay for that to be told it is
        # malformed.
        trial_ids = self._resolve_trial_ids()

        if patient_info is None:
            patient_info = self._resolve_patient_info()

        queryset = Trial.objects.all()
        study_prefs = self._resolve_study_preferences()
        search_type = self.request.query_params.get('type', None)
        # Only where `type` is actually consumed. It reaches `filtered_trials`
        # from `list` and `count` as well as `search`, so all three are
        # covered — but `retrieve` ignores the parameter entirely, and
        # rejecting it there would turn a working detail page into a 400 for
        # any UI that carries the tab it came from in the query string.
        if self.action in ('list', 'count', 'search'):
            self._reject_user_scoped_search_type(search_type)

        if trial_ids is not None:
            # Before the matcher, not after: narrowing first means the
            # scores, the ordering and `itemsTotalCount` are all computed
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
            queryset = queryset.order_by('-match_score', '-posted_date', 'id')

        if self.action == 'search':
            counts = self._trials_counts(queryset, patient_info)
            # Annotated but NOT narrowed by `type`, kept for the tab counts.
            # `with_potential_attrs_count` both annotates and applies the
            # eligible/potential filter, so counting the response's own
            # queryset would count an already-narrowed set: on the Potential
            # tab the Eligible badge would read 0. The tab bar has to be
            # told about the whole matched corpus, not the tab it is on.
            self._tab_counts_source = queryset.with_potential_attrs_count(
                patient_info, None, counts,
            )
            self._tab_counts_patient_info = patient_info
            self._tab_counts_search_type = search_type
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
            queryset = queryset.order_by(*order)

        if self.action in ['list', 'search']:
            queryset = queryset.prefetch_related(
                Prefetch('locationtrial_set',
                         queryset=LocationTrial.objects.select_related('location'))
            )

        return queryset

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
            'counts': {},
            'template': template,
            'search_type': search_type,
            'explain': self.request.query_params.get('explain', '').lower() == 'true',
        })
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
        if patient_info is not None:
            from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
            data['highRiskMclCriteriaBreakdown'] = (
                UserToTrialAttrMatcher(trial=instance, patient_info=patient_info)
                .high_risk_mcl_criteria_breakdown()
            )
        return Response(data)

    def get_paginated_response(self, data, extra_keys=None):
        assert self.paginator is not None
        return self.paginator.get_paginated_response(data, extra_keys=extra_keys)

    @action(methods=['get'], detail=False)
    def count(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        return Response({'count': queryset.count()})

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
            tab_counts = self._tab_counts()
            extra_keys = {} if tab_counts is None else {'tabCounts': tab_counts}
            return self.get_paginated_response(serializer.data, extra_keys=extra_keys)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def _tab_counts(self) -> Optional[dict]:
        """Totals for the tab bar, or None when they would be a lie.

        Deliberately computed here rather than at a `/trials/counts/`
        endpoint of its own. Counts are only meaningful under a patient
        context, and the only way to send one is a POST body — the
        `?person_id=` path is gated off outside DEBUG. A GET-only counts
        endpoint would answer every federated caller with
        `patient_info=None`, where `potential_attrs_count` collapses to
        `num_nonnulls(NULL)`: `potential` structurally zero and every row
        in the corpus labelled eligible, with nothing in the response to
        say so.

        Two cases return None instead of a plausible-looking number, for
        that same reason:

        - **No patient context.** Nothing has been judged, so calling the
          corpus `eligible` would assert a clinical verdict nobody made.
          `GET /trials/search/` can be called this way, so moving the
          counts onto it does not by itself fix what killed the endpoint.
        - **`?type=all`.** That routes through `filter_for_admin`, which
          skips the eligibility filter entirely; the rows are the corpus
          the caller asked for, but a per-row verdict was never computed.
          Listing them is honest, counting them as eligible is not.

        Otherwise the source is the annotated-but-not-type-narrowed
        queryset (`get_queryset`), run through the same `filter_queryset`
        the listed rows went through — so `?search=`, which DRF applies
        outside the matcher, lands on both — and the counts describe the
        whole matched corpus regardless of which tab is active.
        """
        source = getattr(self, '_tab_counts_source', None)
        if source is None:
            return None
        if getattr(self, '_tab_counts_patient_info', None) is None:
            return None
        if getattr(self, '_tab_counts_search_type', None) == 'all':
            return None

        source = self.filter_queryset(source)
        # `potential_attrs_count` is `num_nonnulls(...)`, never NULL, so the
        # two buckets partition the source exactly and the second is
        # arithmetic rather than a third pass over the corpus.
        total = source.count()
        eligible = source.filter(potential_attrs_count=0).count()
        return {'eligible': eligible, 'potential': total - eligible}

    @action(methods=['post'], detail=False, url_path='search/match',
            url_name='search-match')
    def search_match(self, request, *args, **kwargs):
        """POST alias for `search`, carrying `patient_info` in the body.

        The existing `match` alias routes to `list`, whose ordering is fixed
        at `-match_score, -posted_date, id`; only `search` reads `?sort=` and
        carries the tab counts. So a host holding an inline `patient_info` payload —
        the federated remote's default path, since GET-with-body is forbidden
        by the Fetch spec and dropped by axios's XHR adapter — could reach
        neither sorting nor counts at all.

        Binds `self.action = 'search'` so every `self.action == 'search'`
        branch in `get_queryset` fires, exactly as `match` does for `list`.
        """
        self.action = 'search'
        return self.search(request, *args, **kwargs)


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


class NormalizeCtomopRowView(APIView):
    """POST endpoint that exposes `normalize_ctomop_row` to authenticated
    callers. Takes a raw CTOMOP `patient_info` row in the body and
    returns the same row with EXACT-shaped values for the fields that
    differ between systems (receptor statuses → codes, TNM strings →
    short codes, therapy-line outcomes → IDs, refractory status labels,
    lab-value fallbacks, etc.).

    Exists so the federation dev harness (and any other client that
    fetches CTOMOP rows browser-side) can run the same normalization
    the server-side `?person_id=` resolver applies. Without this, an
    inline-fetch caller's `patient_info` reaches the matcher with raw
    CTOMOP labels and a meaningful subset of fields silently reads as
    "unknown" — closes the limitation documented in PR #117.

    Same auth + token model as `/trials/`: `IsAuthenticated`, DRF
    Token. The function is pure / side-effect-free; the caller already
    holds the patient row from their own session-authenticated CTOMOP
    fetch so this endpoint doesn't widen the IDOR surface tracked in
    #108.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, *args, **kwargs):
        from trials.services.patient_info.ctomop_adapter import normalize_ctomop_row

        raw = request.data
        if not isinstance(raw, dict):
            return Response(
                {'detail': 'Body must be a JSON object representing one CTOMOP patient_info row.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # `normalize_ctomop_row` mutates its argument in place; copy
        # first so we never alter caller-owned state. (`dict(raw)` is
        # a shallow copy — fine because the function only rewrites
        # top-level keys plus the `genetic_mutations` items, which the
        # function itself defensively copies via `m = dict(m)`.)
        normalized = normalize_ctomop_row(dict(raw))
        return Response(normalized)
