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

* with a patient, the verdict is a constant `eligible` — `counts` never reaches
  the serializer context, so `attributesToFillIn` is always empty, even for a
  trial the same response's `tabCounts` calls potential (#464). The last test
  here is the part of it this fix can already pin down.
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

    def test_and_the_potential_branch_still_answers_potential(
        self, authed_client, monkeypatch,
    ):
        """The other arm of the ternary, which no request can reach today.

        The view computes the blank-attribute `counts` and then hands the
        serializer a literal `{}` instead (#464), so `attrs_to_fill_in` comes
        back empty for every trial and only the first branch is ever taken.
        Supplying real counts — real view, real queryset, real matcher —
        exercises the arm a request cannot, so a change that collapsed the
        ternary is caught here rather than at whatever later point #464 makes
        the API path reachable. It is a stand-in for counts ARRIVING, not a
        reproduction of the view's own computation, and not the line #464
        would add.
        """
        from trials.api.trials_views import TrialsViewSet
        from trials.services.blank_attribute_records_count import BlankAttributeRecordsCount

        from trials.models import Trial

        original = TrialsViewSet.get_serializer_context

        def with_counts_the_serializer_never_receives(view):
            context = original(view)
            # Over the whole table rather than by re-running `get_queryset()`:
            # a second call re-assigns the view's `_tab_counts_source` and
            # `_tab_counts_patient_info` mid-request, so the stand-in would
            # be changing the thing under test. This is not a reproduction of
            # the view's own counts — those are taken over the pre-narrowed
            # queryset — only a supply of real ones.
            context['counts'] = BlankAttributeRecordsCount().counts(
                Trial.objects.all(), context['patient_info'],
            )
            return context

        monkeypatch.setattr(
            TrialsViewSet, 'get_serializer_context',
            with_counts_the_serializer_never_receives,
        )

        potential_trial()
        rows = authed_client.post(
            '/trials/search/match/',
            {'patient_info': {'disease': 'multiple myeloma'}},
            format='json',
        ).data['results']

        assert rows
        assert rows[0]['attributesToFillIn'], 'the fixture stopped being unanswerable'
        assert {r['matchingType'] for r in rows} == {'potential'}
