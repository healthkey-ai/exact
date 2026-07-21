import logging
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

    def get_queryset(self, patient_info=None):
        if patient_info is None:
            patient_info = self._resolve_patient_info()

        # trial_type is a FK serialized on every trial via StringRelatedField
        # (list, search, detail); without select_related the serializer fetches
        # it once per row — an N+1 (ported from CB perf(trials) select_related
        # trial_type). The JOIN is cheap and the `count` action ignores it, so
        # eager-load it on the base queryset.
        queryset = Trial.objects.select_related('trial_type')
        study_prefs = self._resolve_study_preferences()
        search_type = self.request.query_params.get('type', None)

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
            if search_type == 'favorites':
                queryset = queryset.filter(favorite=True)

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
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


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
