"""Identity uniqueness: `sub` is unique per issuer, not globally (#700).

The real key is the composite (issuer, sub) UniqueConstraint. A standalone
global unique on `sub` turned the first sign-in of a PROMOP user whose id an
earlier phr user already had into a 500.
"""
from __future__ import annotations

import pytest
from asgiref.sync import async_to_sync
from django.db import IntegrityError
from django.db.models import UniqueConstraint

from accounts.models import Identity


@pytest.mark.django_db
def test_same_sub_across_issuers_creates_distinct_identities():
    a, created_a = Identity.objects.get_or_create(
        issuer='healthkey-phr', sub='phr:42'
    )
    b, created_b = Identity.objects.get_or_create(
        issuer='https://promop.example', sub='phr:42'
    )
    assert created_a and created_b
    assert a.pk != b.pk


@pytest.mark.django_db
def test_same_issuer_and_sub_is_still_rejected():
    Identity.objects.create(issuer='https://promop.example', sub='dup')
    with pytest.raises(IntegrityError):
        Identity.objects.create(issuer='https://promop.example', sub='dup')


def test_composite_issuer_sub_constraint_is_the_key():
    assert Identity._meta.get_field('sub').unique is False
    composite = [
        c for c in Identity._meta.constraints
        if isinstance(c, UniqueConstraint) and c.name == 'uq_identity_issuer_sub'
    ]
    assert len(composite) == 1
    assert tuple(composite[0].fields) == ('issuer', 'sub')
    assert composite[0].condition is None


@pytest.mark.django_db
def test_natural_key_lookup_is_scoped_to_local_identities():
    """The password/admin login path does `get_by_natural_key(sub)`; with `sub`
    shared across issuers an unscoped `get` would raise MultipleObjectsReturned.
    """
    Identity.objects.create(issuer='https://issuer-a.example', sub='shared')
    Identity.objects.create(issuer='https://issuer-b.example', sub='shared')
    local = Identity.objects.create(issuer='urn:local', sub='shared')
    assert Identity.objects.get_by_natural_key('shared').pk == local.pk


@pytest.mark.django_db
def test_natural_key_lookup_never_returns_an_external_identity():
    Identity.objects.create(issuer='https://issuer-a.example', sub='external-only')
    with pytest.raises(Identity.DoesNotExist):
        Identity.objects.get_by_natural_key('external-only')


@pytest.mark.django_db
def test_async_natural_key_lookup_is_scoped_the_same_way():
    # async_to_sync runs the coroutine on this thread, inside the test's
    # transaction, so no `transaction=True` is needed.
    Identity.objects.create(issuer='https://issuer-a.example', sub='shared')
    local = Identity.objects.create(issuer='urn:local', sub='shared')
    found = async_to_sync(Identity.objects.aget_by_natural_key)('shared')
    assert found.pk == local.pk


@pytest.mark.django_db
def test_local_superuser_logs_in_despite_an_external_row_with_the_same_sub(client):
    Identity.objects.create_superuser(email='admin@example.com', password='pw-123456!')
    admin = Identity.objects.get(email='admin@example.com')
    Identity.objects.create(issuer='https://issuer-a.example', sub=admin.sub)
    assert client.login(username=admin.sub, password='pw-123456!')


@pytest.mark.django_db
def test_natural_key_round_trips_an_external_identity():
    """`dumpdata --natural-foreign` + `loaddata` resolve FKs through this pair.

    With the inherited `(sub,)` key an external identity would not resolve, or
    would resolve to a local identity sharing its subject.
    """
    Identity.objects.create(issuer='urn:local', sub='shared')
    external = Identity.objects.create(issuer='https://issuer-a.example', sub='shared')
    key = external.natural_key()
    assert key == ('https://issuer-a.example', 'shared')
    assert Identity.objects.get_by_natural_key(*key).pk == external.pk


@pytest.mark.django_db
def test_serialized_foreign_key_reattaches_to_the_external_identity():
    """End to end through Django's serializers, not just the manager method."""
    from django.contrib.admin.models import LogEntry
    from django.core import serializers

    Identity.objects.create(issuer='urn:local', sub='shared')
    external = Identity.objects.create(
        issuer='https://issuer-a.example', sub='shared', is_staff=True
    )
    entry = LogEntry.objects.create(
        user=external, object_repr='x', action_flag=1, change_message=''
    )
    dumped = serializers.serialize('json', [entry], use_natural_foreign_keys=True)
    entry.delete()
    (obj,) = serializers.deserialize('json', dumped, handle_forward_references=False)
    obj.save()
    assert LogEntry.objects.get().user_id == external.pk
