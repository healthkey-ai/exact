from django.urls import include, path
from rest_framework.routers import DefaultRouter

from user_state.views import ForgetIdentityView, TrialSearchPreferencesViewSet

app_name = 'user_state'

router = DefaultRouter()
router.register(
    r'trial-search-preferences',
    TrialSearchPreferencesViewSet,
    basename='trial-search-preferences',
)

urlpatterns = [
    path('', include(router.urls)),
    path('internal/forget/', ForgetIdentityView.as_view(), name='forget-identity'),
]
