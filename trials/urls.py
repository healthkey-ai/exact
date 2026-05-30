from django.urls import path, include
from drf_yasg import openapi
from drf_yasg.views import get_schema_view
from rest_framework import permissions
from rest_framework.routers import DefaultRouter

from trials.api.graph_view import TrialsGraphViewSet
from trials.api.trials_views import TrialsViewSet, CountriesViewSet, LocationsViewSet, FormSettingsViewSet

schema_view = get_schema_view(
    openapi.Info(
        title="Exact — Trial Search API",
        default_version='v1',
        description="Patient-facing clinical trial search and matching engine.",
    ),
    public=True,
    permission_classes=[permissions.AllowAny],
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
    path('', include(router.urls)),
]
