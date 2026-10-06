"""DRF authentication backends for the house OIDC Identity model.

``PartnerAuthentication`` delegates to pluggable token providers configured
in ``PARTNER_AUTH_PROVIDERS``.  Each provider first gets a lightweight
``can_handle()`` check (unverified JWT payload inspection — no secrets, no
external calls) before the real ``verify()`` is invoked.

Every request is verified. There is deliberately no cache of verification
results: one used to hold the identity and claims for ``AUTH_TOKEN_CACHE_TTL``
seconds keyed by ``SHA256(token)[:32]``, and on a hit the token itself was
never looked at again — so an **expired** token kept working until the entry
aged out (#404). It bought only the provider round trip, not the database
lookup, which ran on the cached path too.

Unlike promop, EXACT does **not** own OMOP Person/PatientInfo, so the
identity is resolved (get-or-create) but no patient row is provisioned.

Both backends reject a deactivated ``Identity`` (``is_active=False``) — see
``_reject_if_inactive``.

What removing the cache does NOT close: with
``FIREBASE_SKIP_REVOCATION_CHECK`` set, the provider calls
``verify_id_token(check_revoked=False)``, which validates signature, issuer,
audience and ``exp`` but not whether the token was revoked. So a revoked token
is still accepted until it expires, and that is a configuration decision rather
than a code one — tracked as #410, not here.
"""
from __future__ import annotations

import hmac
import logging

from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import Identity
from .providers import get_providers
from .providers.base import TokenClaims, decode_jwt_unverified

logger = logging.getLogger(__name__)


def _reject_if_inactive(identity: Identity) -> Identity:
    """Raise for a deactivated Identity, mirroring DRF's own backends.

    ``IsAuthenticated`` only consults ``is_authenticated``, which
    ``AbstractBaseUser`` hardcodes to ``True`` — so without this check an
    Identity deactivated in the admin keeps full API access. DRF's built-in
    ``TokenAuthentication``/``BasicAuthentication`` reject inactive users
    (rest_framework/authentication.py); these house backends must match, or
    ``is_active`` is a control that silently does nothing.
    """
    if not identity.is_active:
        raise AuthenticationFailed("User inactive or deleted.")
    return identity


class PartnerAuthentication(BaseAuthentication):
    """Verify a partner bearer token (e.g. Firebase ID token) → Identity."""

    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.startswith("Bearer "):
            return None

        token = header[7:]

        providers = get_providers()
        if not providers:
            return None

        unverified = decode_jwt_unverified(token)

        for provider in providers:
            if not provider.can_handle(token, unverified):
                continue

            try:
                claims = provider.verify(token)
            except AuthenticationFailed:
                raise
            except Exception:
                logger.warning(
                    "partner_auth: %s.verify failed", type(provider).__name__
                )
                continue

            if claims is None:
                continue

            identity = _reject_if_inactive(self._get_or_create_identity(claims))
            return (identity, claims)

        return None

    def authenticate_header(self, request):
        return "Bearer"

    @staticmethod
    def _get_or_create_identity(claims: TokenClaims) -> Identity:
        identity, created = Identity.objects.get_or_create_from_claims(claims)
        if created:
            identity.set_unusable_password()
            identity.save(update_fields=["password"])
            logger.info(
                "partner_auth: provisioned identity %d (%s|%s)",
                identity.pk, claims.issuer, claims.sub,
            )
        return identity


class ServiceTokenAuthentication(BaseAuthentication):
    """Authenticate service-to-service calls via a pre-shared Bearer token."""

    SERVICE_ISSUER = "urn:service"
    SERVICE_SUB = "exact-service"

    def authenticate(self, request):
        secret = getattr(settings, "SERVICE_AUTH_TOKEN", "").strip()
        if not secret:
            return None

        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.startswith("Bearer "):
            return None

        # Compare bytes, not str. `hmac.compare_digest` raises TypeError when
        # either str argument is non-ASCII, and Django hands the header over
        # latin-1-decoded, so any byte in 0x80-0xFF arrives here as a non-ASCII
        # str. `Authorization: Bearer <0xE9>` was therefore an anonymous 500 --
        # the same defect as #405, one authenticator earlier and needing no JWT
        # shape at all, since this class runs first in DEFAULT_AUTHENTICATION_CLASSES.
        #
        # `latin-1` and not `utf-8`, because it is the *inverse* of what the
        # server did: gunicorn (util.py: `str(b, 'latin1')`) and Django's ASGI
        # handler both decode header bytes latin-1, so re-encoding latin-1
        # reconstructs the bytes the client actually sent. Encoding utf-8 here
        # double-encodes them, and a non-ASCII SERVICE_AUTH_TOKEN would then
        # never match -- a silent, permanent 401 with nothing in the log. DRF
        # uses the same inverse (`HTTP_HEADER_ENCODING = 'iso-8859-1'`).
        #
        # The secret is encoded utf-8 because that is how `os.environ` decoded
        # it, with `surrogateescape` so env bytes that were not valid UTF-8
        # cannot raise here on every request.
        try:
            presented = header[7:].encode("latin-1")
        except UnicodeEncodeError:
            # Unreachable from the wire -- latin-1 covers every byte -- but a
            # test client can put an arbitrary str into META directly, and this
            # method must stay total.
            return None

        if not hmac.compare_digest(
            presented, secret.encode("utf-8", "surrogateescape")
        ):
            return None

        identity, created = Identity.objects.get_or_create(
            issuer=self.SERVICE_ISSUER, sub=self.SERVICE_SUB,
        )
        if created:
            identity.set_unusable_password()
            identity.save(update_fields=["password"])

        # Deactivating the service Identity is the kill switch for the shared
        # service token; without this it would have no effect. Note it must be
        # *deactivated*, not deleted — `get_or_create` above would silently
        # re-provision a fresh, active row on the very next request.
        return (_reject_if_inactive(identity), "service-token")

    def authenticate_header(self, request):
        return "Bearer"
