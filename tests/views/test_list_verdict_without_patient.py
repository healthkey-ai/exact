"""The list stops calling every trial `eligible` when nobody asked about a
patient (#456).

`TrialSerializer.to_representation` set the verdict from `attrs_to_fill_in`
alone — an expression with no patient in it. With no patient context that list
is empty, so every trial in the corpus came back `eligible`: a word the reader
takes to mean "this patient qualifies", about somebody the request never
named. It is a wire-level defect: nothing in the shipped remote reads a list
row's `matchingType` today (grouping is server-side, via the `?type=` tab
param), so this is about what EXACT asserts to any client, not about a group
heading that was observed to be wrong.

`null` instead — where the detail endpoint's no-patient branch already points,
though it does not reach it on this base: every patient-less detail request
raises `'NoneType' object has no attribute 'prior_therapy'` first, which is
#455 and not yet merged. So the cross-endpoint agreement cannot be asserted
here; it belongs to whichever of the two lands second.

Two neighbouring defects are deliberately out of scope, both filed:

* with a patient, the verdict was a constant `eligible` — `counts` never
  reached the serializer context, so `attributesToFillIn` was always empty,
  even for a trial the same response's `tabCounts` called potential. Fixed
  since, in #464; the last test here reaches that branch through the real
  request now rather than through a monkeypatch.
* a `patient_info` payload EXACT recognises no key of resolves to a blank
  PatientInfo rather than None, and is still answered `eligible` — by this
  endpoint and by the detail one. The fix belongs at the resolver and is
  blocked on #455 (#466).
"""
import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from tests.factories import TrialFactory
from tests.views.test_search_api_phase0 import potential_trial


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='verdict-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


@pytest.mark.django_db
class TestTheListVerdictWithoutAPatient:
    def test_it_claims_nothing(self, authed_client):
        TrialFactory(disease='multiple myeloma')
        TrialFactory(disease='breast cancer')

        rows = authed_client.get('/trials/search/').data['results']

        assert rows, 'the list should still answer — this is the public-browsing path'
        for row in rows:
            assert row['matchingType'] is None, row['studyId']

    def test_on_the_plain_list_endpoint_too(self, authed_client):
        """`/trials/` and `/trials/search/` are different actions reaching the
        same serializer; the remote calls both."""
        TrialFactory(disease='multiple myeloma')

        rows = authed_client.get('/trials/').data['results']

        assert rows
        assert {r['matchingType'] for r in rows} == {None}

    def test_an_empty_patient_info_payload_is_nobody_too(self, authed_client):
        """The boundary of what this fix covers.

        `{'patient_info': {}}` is falsy, so the resolver already reads it as no
        patient. A payload with keys EXACT does not recognise carries exactly
        as much information about a patient — none — and is NOT read that way:
        it becomes a blank PatientInfo and is answered `eligible`, here and on
        the detail endpoint. That is #466, whose fix belongs at the resolver
        and is blocked on #455.
        """
        TrialFactory(disease='multiple myeloma')

        rows = authed_client.post(
            '/trials/search/match/', {'patient_info': {}}, format='json',
        ).data['results']

        assert rows
        assert {r['matchingType'] for r in rows} == {None}


@pytest.mark.django_db
class TestWithAPatientNothingChanges:
    """Non-vacuity. A guard that simply stopped emitting a verdict would pass
    every assertion above."""

    def test_a_matching_trial_is_still_eligible(self, authed_client):
        TrialFactory(disease='multiple myeloma')

        rows = authed_client.post(
            '/trials/search/match/',
            {'patient_info': {'disease': 'multiple myeloma', 'patientAge': 45}},
            format='json',
        ).data['results']

        assert rows
        assert {r['matchingType'] for r in rows} == {'eligible'}

    def test_and_the_potential_branch_still_answers_potential(self, authed_client):
        """The other arm of the ternary.

        It used to be unreachable by any request — the view computed the
        blank-attribute `counts` and handed the serializer a literal `{}`, so
        `attrs_to_fill_in` came back empty for every trial and only the first
        branch was ever taken. This test reached it by monkeypatching counts
        into the context. #464 made the real path deliver them, so the
        stand-in is gone: real view, real queryset, real counts.
        """
        potential_trial()
        rows = authed_client.post(
            '/trials/search/match/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        ).data['results']

        assert rows
        assert rows[0]['attributesToFillIn'], 'the fixture stopped being unanswerable'
        assert {r['matchingType'] for r in rows} == {'potential'}
