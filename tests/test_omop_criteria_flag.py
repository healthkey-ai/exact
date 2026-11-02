"""Fail-closed contract for the single EXACT_OMOP_CRITERIA flag."""
import json
from types import SimpleNamespace

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from trials.services.matching.typed_runtime import (
    TypedMatchingUnavailable,
    resolve_evaluation,
)

# The session-wide reference-data seeding in tests/conftest.py needs a real test
# database. pytest-django only materialises one when a collected item asks for
# it, and a bare `-q tests/test_omop_criteria_flag.py` would otherwise seed the
# developer's live `exact` database.
pytestmark = pytest.mark.django_db


def _request():
    return SimpleNamespace()


def test_flag_off_keeps_legacy_path():
    with override_settings(
        EXACT_OMOP_CRITERIA=False,
        EXACT_TYPED_MATCHING_ENABLED=False,
        EXACT_TYPED_MATCHING_RESOLVER=None,
    ):
        assert resolve_evaluation(_request(), SimpleNamespace()) is None


def test_public_flag_on_without_resolver_fails_closed():
    with override_settings(
        EXACT_OMOP_CRITERIA=True,
        EXACT_TYPED_MATCHING_ENABLED=False,
        EXACT_TYPED_MATCHING_RESOLVER=None,
    ):
        with pytest.raises(TypedMatchingUnavailable) as raised:
            resolve_evaluation(_request(), SimpleNamespace())
    # A missing resolver is the flag's own fail-closed state, not a missing
    # patient; callers must see the documented reason code.
    assert raised.value.detail['code'] == 'typed_matching_unavailable'


def test_resolver_without_patient_reports_missing_context():
    with override_settings(
        EXACT_OMOP_CRITERIA=True,
        EXACT_TYPED_MATCHING_ENABLED=True,
        EXACT_TYPED_MATCHING_RESOLVER=lambda request, patient: None,
    ):
        with pytest.raises(TypedMatchingUnavailable) as raised:
            resolve_evaluation(_request(), None)
    assert raised.value.detail['code'] == 'trusted_context_unavailable'


def test_readiness_command_fails_closed_without_resolver(capsys):
    with override_settings(
        EXACT_OMOP_CRITERIA=True,
        EXACT_TYPED_MATCHING_ENABLED=True,
        EXACT_TYPED_MATCHING_RESOLVER=None,
    ):
        with pytest.raises(CommandError):
            call_command('omop_criteria_readiness', '--json')
    payload = json.loads(capsys.readouterr().out)
    assert payload['EXACT_OMOP_CRITERIA'] is True
    assert payload['resolver_installed'] is False
    assert payload['criteria_count'] == 55
    assert payload['ready_count'] == 0
    # The gate raises before any field runs, so therapy and language are
    # unavailable too — they are not quietly served by their own readers.
    assert {row['reader'] for row in payload['fields']} == {'typed_runtime'}
    assert all(
        row['reason'].startswith('EXACT_OMOP_CRITERIA on, resolver not installed')
        for row in payload['fields']
    )


def test_readiness_command_lists_all_55_with_resolver(capsys):
    with override_settings(
        EXACT_OMOP_CRITERIA=True,
        EXACT_TYPED_MATCHING_ENABLED=True,
        EXACT_TYPED_MATCHING_RESOLVER=object(),
        EXACT_OMOP_THERAPY=True,
        EXACT_OMOP_LANGUAGES=True,
    ):
        call_command('omop_criteria_readiness', '--json')
    payload = json.loads(capsys.readouterr().out)
    assert payload['criteria_count'] == 55
    assert payload['ready_count'] == 55
    assert payload['resolver_installed'] is True
    # The resolver replaces the whole evaluation, so every field — including
    # therapy and language — is served by the typed runtime, not the cutover
    # readers.
    assert {row['reader'] for row in payload['fields']} == {'typed_runtime'}
