from django.db.models import F, Prefetch
from rest_framework import viewsets, filters, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import serializers

from trials.api.pagination import TrialsPagination
from trials.api.trials_serializers import TrialSerializer, TrialDetailsSerializer
from trials.models import Trial, Location, PreferredCountry, State
from trials.services.patient_info.resolve import resolve_patient_info
from trials.services.study_preferences import study_preferences_from_query_params
from trials.services.value_options import ValueOptions


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
# Blank-attribute counts helper (adapted from source — no user dependency)
# ---------------------------------------------------------------------------

class _BlankAttributeRecordsCount:
    def counts(self, scope, patient_info):
        if patient_info is None:
            return {}

        from trials.services.user_to_trial_attrs_mapper import UserToTrialAttrsMapper
        sql_conditions = UserToTrialAttrsMapper().potential_attrs_to_check(patient_info)
        if not sql_conditions:
            return {}

        sql_counts = {attr: f'SUM{sql_conditions[attr]}' for attr in sql_conditions}
        out = scope.extra(select=sql_counts).values(*sql_counts.keys())
        if not out:
            return {}
        out = out[0]
        return {k: v for k, v in out.items() if v is not None}


# ---------------------------------------------------------------------------
# Trials ViewSet
# ---------------------------------------------------------------------------

class TrailsViewSet(viewsets.ReadOnlyModelViewSet):
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

    def _resolve_patient_info(self):
        try:
            return resolve_patient_info(self.request)
        except Exception:
            return None

    def _resolve_study_preferences(self):
        return study_preferences_from_query_params(self.request.query_params)

    def get_queryset(self, patient_info=None):
        if patient_info is None:
            patient_info = self._resolve_patient_info()

        queryset = Trial.objects.all()
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

        if self.action == 'list':
            queryset = queryset.with_potential_attrs_count(patient_info)
            queryset = queryset.order_by('-match_score', '-posted_date')

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
            queryset = queryset.order_by(*order)

        if self.action in ['list', 'search']:
            queryset = queryset.prefetch_related(
                Prefetch('locationtrial_set',
                         queryset=__import__('trials.models', fromlist=['LocationTrial']).LocationTrial.objects.select_related('location'))
            )

        return queryset

    def _trials_counts(self, queryset, patient_info):
        return _BlankAttributeRecordsCount().counts(queryset, patient_info)

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

    def get_paginated_response(self, data, extra_keys=None):
        assert self.paginator is not None
        return self.paginator.get_paginated_response(data, extra_keys=extra_keys)

    @action(methods=['get'], detail=False)
    def count(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        return Response({'count': queryset.count()})

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
        out = ValueOptions().all_options()
        if disease_code:
            trial_types = ValueOptions.trial_types_by_disease_code(disease_code)
            out['trialType'] = {'options': ValueOptions.to_value_and_label(trial_types)}
        return Response(out)

    def _normalize_disease_code(self, disease_param: str) -> str:
        if not disease_param:
            return ''
        lower = disease_param.lower().strip()
        if lower.upper() in ('MM', 'BC', 'FL', 'CLL'):
            return lower.upper()
        return self.DISEASE_NAME_TO_CODE.get(lower, '')
