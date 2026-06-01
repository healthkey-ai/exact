from django.urls import path, include
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework import permissions
from rest_framework.routers import DefaultRouter

from trials.api.graph_view import TrialsGraphViewSet
from trials.api.trials_views import (
    CountriesViewSet,
    FormSettingsViewSet,
    LocationsViewSet,
    NormalizeCtomopRowView,
    TrialsViewSet,
)

# The API contract (Swagger / ReDoc) is gated behind authentication so a
# deployed instance doesn't serve its full endpoint catalogue to anonymous
# callers. `public=False` further restricts the generated schema to endpoints
# the requesting user can actually reach.
schema_view = get_schema_view(
    openapi.Info(
        title="Exact — Trial Search API",
        default_version='v1',
        description="Patient-facing clinical trial search and matching engine.",
    ),
    public=False,
    permission_classes=[permissions.IsAuthenticated],
)

app_name = 'trials'

router = DefaultRouter()
router.register(r'trials', TrialsViewSet, basename='trials-v1')
router.register(r'trials-graph', TrialsGraphViewSet, basename='trials-graph-v1')
router.register(r'countries', CountriesViewSet, basename='countries-v1')
router.register(r'locations', LocationsViewSet, basename='locations-v1')
router.register(r'form-settings', FormSettingsViewSet, basename='form-settings-v1')

urlpatterns = [
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
    path('redoc/', schema_view.with_ui('redoc', cache_timeout=0), name='schema-redoc'),
    # POST /normalize-ctomop-row/ — pipes a raw CTOMOP patient_info row
    # through `normalize_ctomop_row`. Used by the federation dev harness
    # to ensure inline-fetched patients are normalized before the matcher
    # sees them (closes the "unknown" fallthrough documented in #117).
    path(
        'normalize-ctomop-row/',
        NormalizeCtomopRowView.as_view(),
        name='normalize-ctomop-row',
    ),
    path('', include(router.urls)),
]
