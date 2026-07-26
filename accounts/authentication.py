"""DRF authentication backends for the house OIDC Identity model.

``PartnerAuthentication`` delegates to pluggable token providers configured
in ``PARTNER_AUTH_PROVIDERS``.  Each provider first gets a lightweight
``can_handle()`` check (unverified JWT payload inspection — no secrets, no
external calls) before the real ``verify()`` is invoked.  Verified tokens are
cached (Django cache, keyed by ``SHA256(token)[:32]``) for up to
``AUTH_TOKEN_CACHE_TTL`` seconds so repeated requests with the same Bearer
token skip ``provider.verify()`` and the DB lookup.

Unlike promop, EXACT does **not** own OMOP Person/PatientInfo, so the
identity is resolved (get-or-create) but no patient row is provisioned.
"""
from __future__ import annotations

import hashlib
import hmac
import logging

from django.conf import settings
from django.core.cache import cache as django_cache
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .models import Identity
from .providers import get_providers
from .providers.base import TokenClaims, decode_jwt_unverified

logger = logging.getLogger(__name__)


def _token_cache_key(token: str) -> str:
    digest = hashlib.sha256(token.encode()).hexdigest()[:32]
    return f"auth:partner:{digest}"


class PartnerAuthentication(BaseAuthentication):
    """Verify a partner bearer token (e.g. Firebase ID token) → Identity."""

    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.startswith("Bearer "):
            return None

        token = header[7:]

        cached = self._from_cache(token)
        if cached is not None:
            return cached

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

            identity = self._get_or_create_identity(claims)
            self._to_cache(token, identity.pk, claims)
            return (identity, claims)

        return None

    def authenticate_header(self, request):
        return "Bearer"

    @staticmethod
    def _from_cache(token: str):
        data = django_cache.get(_token_cache_key(token))
        if data is None:
            return None
        try:
            identity = Identity.objects.get(pk=data["pk"])
        except Identity.DoesNotExist:
            return None
        claims = TokenClaims(**data["claims"])
        return (identity, claims)

    @staticmethod
    def _to_cache(token: str, identity_pk: int, claims: TokenClaims):
        django_cache.set(
            _token_cache_key(token),
            {
                "pk": identity_pk,
                "claims": {
                    "issuer": claims.issuer,
                    "sub": claims.sub,
                    "email": claims.email,
                    "name": claims.name,
                    "raw": claims.raw,
                },
            },
            timeout=getattr(settings, "AUTH_TOKEN_CACHE_TTL", 60),
        )

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

        if not hmac.compare_digest(header[7:], secret):
            return None

        identity, created = Identity.objects.get_or_create(
            issuer=self.SERVICE_ISSUER, sub=self.SERVICE_SUB,
        )
        if created:
            identity.set_unusable_password()
            identity.save(update_fields=["password"])

        return (identity, "service-token")

    def authenticate_header(self, request):
        return "Bearer"
