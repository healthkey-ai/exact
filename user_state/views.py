"""The trials page's own store, one row per verified identity.

Last writer wins, deliberately. PROMOP's copy of this uses `If-Match` against
an ETag, and the cost of that design is on display in promop#1602: the
conditional headers were never added to the CORS allowlist, so no browser
completed a conditional write and saved filters silently did not save for
months. The thing being protected is a filter set, and losing one to a second
tab is a shrug. Registration interest is not a shrug, and when it lands here
this decision is worth taking again rather than inheriting.
"""
import logging

from django.conf import settings
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.authentication import PartnerAuthentication, ServiceTokenAuthentication
from user_state.models import TrialSearchPreferences, forget_identity
from user_state.serializers import TrialSearchPreferencesSerializer

logger = logging.getLogger(__name__)


class TrialSearchPreferencesViewSet(viewsets.ViewSet):
    """`me` semantics: there is no id in any of these routes.

    Not "a detail route with a permission check" — a route that cannot name
    another identity cannot be made to act on one, and that is a stronger
    guarantee than a check somebody has to remember to write. The row is
    reached through `request.user`, which `PartnerAuthentication` derived from
    a verified signature.
    """

    # Pinned rather than inherited. The project default chain puts
    # `ServiceTokenAuthentication` first, so without this a caller holding the
    # shared service token writes a row as `urn:service` — a machine
    # credential on a human-state surface, shared by every service that holds
    # it, and one `forget_identity` will never be called for. It also lets a
    # never-expiring DRF token and an admin session reach these routes, which
    # is how the docstring above came to be false about where `request.user`
    # comes from. Token auth stays only where it is already gated to dev.
    authentication_classes = (
        [PartnerAuthentication, TokenAuthentication]
        if getattr(settings, 'ENABLE_DRF_TOKEN_AUTH', False)
        else [PartnerAuthentication]
    )
    permission_classes = [IsAuthenticated]

    def _row(self):
        row, _ = TrialSearchPreferences.objects.get_or_create(identity=self.request.user)
        return row

    def list(self, request):
        # Reading does not write. An unsaved instance, so opening the trials
        # page does not create EXACT's first row about somebody who has not
        # chosen anything yet — a different consent story from the one this
        # app is for — and so a GET stays safe and idempotent, which is what
        # lets it be cached or served from a replica.
        row = (
            TrialSearchPreferences.objects.filter(identity=request.user).first()
            or TrialSearchPreferences(identity=request.user)
        )
        return Response(TrialSearchPreferencesSerializer(row).data)

    def create(self, request):
        # PUT-shaped, POST-spelled: a viewset without a lookup has no `update`
        # route to bind to, and inventing an id to satisfy the router would
        # put back the parameter this app exists to avoid.
        row = self._row()
        serializer = TrialSearchPreferencesSerializer(row, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def reset(self, request):
        row = self._row()
        row.preferences = {}
        # The wizard flag is NOT reset. "Reset my filters" is a sentence about
        # filters; being offered the wizard again because of it would be a
        # surprise, and the flag exists to make that offer once.
        row.save(update_fields=['preferences', 'updated_at'])
        return Response(TrialSearchPreferencesSerializer(row).data)


class ForgetIdentityView(APIView):
    """Erasure, called by PROMOP when it deletes a patient.

    Service token only, and a POST with a body rather than an identity in the
    path: an issuer is a URL, and URL-encoding one into a path segment is a
    class of bug nobody needs.
    """

    authentication_classes = [ServiceTokenAuthentication]
    permission_classes = [IsAuthenticated]

    def post(self, request):
        issuer = request.data.get('issuer')
        sub = request.data.get('sub')
        if not isinstance(issuer, str) or not isinstance(sub, str) or not issuer or not sub:
            return Response(
                {'detail': 'Both `issuer` and `sub` are required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        result = forget_identity(issuer, sub)
        # Logged because an erasure nobody can see happening is one nobody can
        # show happened. No payload, only what was removed.
        logger.info(
            'user_state.forget issuer=%s sub=%s removed=%s', issuer, sub, result,
        )
        return Response(result)
