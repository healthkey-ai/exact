"""`manage.py changepassword` with a `sub` shared across issuers (#700).

Django's command looks the user up by a bare `sub`, which #700 made unique
only within an issuer. EXACT replaces it with one that resolves the name the
way login does: scoped to `urn:local`, the only identities with passwords.
"""
from __future__ import annotations

from unittest import mock

import pytest
from django.core.management import call_command, get_commands
from django.core.management.base import CommandError

from accounts.management.commands import changepassword
from accounts.models import Identity


def test_accounts_command_replaces_django_contrib_auth_one():
    """Guards the INSTALLED_APPS order: with `accounts` after
    `django.contrib.auth`, auth's command would silently win again."""
    assert get_commands()['changepassword'] == 'accounts'


@pytest.mark.django_db
@mock.patch.object(changepassword.Command, '_get_pass', return_value='n3w-Pa55-phrase!')
def test_changes_the_local_identity_when_an_external_one_shares_its_sub(_):
    external = Identity.objects.create(issuer='https://issuer-a.example', sub='shared')
    external.set_unusable_password()
    external.save()
    local = Identity.objects.create(issuer='urn:local', sub='shared')
    local.set_password('old-Pa55-phrase!')
    local.save()

    call_command('changepassword', 'shared', stdout=mock.Mock())

    local.refresh_from_db()
    external.refresh_from_db()
    assert local.check_password('n3w-Pa55-phrase!')
    assert not external.has_usable_password()


@pytest.mark.django_db
@mock.patch.object(changepassword.Command, '_get_pass', return_value='n3w-Pa55-phrase!')
def test_an_external_only_sub_is_not_a_user_with_a_password(_):
    external = Identity.objects.create(issuer='https://issuer-a.example', sub='external-only')
    external.set_unusable_password()
    external.save()

    with pytest.raises(CommandError, match="user 'external-only' does not exist"):
        call_command('changepassword', 'external-only', stdout=mock.Mock())

    external.refresh_from_db()
    assert not external.has_usable_password()
