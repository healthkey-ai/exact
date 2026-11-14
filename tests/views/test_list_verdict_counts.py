"""The list verdict is derived from the counts it now actually receives (#464).

`get_serializer_context` used to pass a literal `{}` for `counts`, so
`potential_attrs_for_trial` never ran, `attributesToFillIn` was `[]` on every
row, and `matchingType` was the constant `eligible` — including for a trial the
matcher had not judged. The detail endpoint scored the same patient properly,
so one trial could read `eligible` in the list and `potential` / `not_eligible`
on its page.

These tests go through the HTTP path, because the defect was in the context
handed to the serializer, not in the matcher.
"""
import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from tests.factories import TrialFactory


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='list-verdict-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


def _row(response, trial):
    rows = [row for row in response.data['results'] if row['trialId'] == trial.id]
    assert len(rows) == 1, f'trial {trial.id} missing from {response.data["results"]}'
    return rows[0]


@pytest.mark.django_db
def test_a_blank_patient_attribute_makes_the_list_row_potential(authed_client):
    """The trial constrains ECOG; the patient says nothing about it.

    Before #464 this row read `eligible` with an empty `attributesToFillIn`,
    while the detail matcher for the same payload would not claim eligibility.
    """
    trial = TrialFactory(disease='Multiple Myeloma', ecog_performance_status_max=2)

    response = authed_client.post(
        '/trials/match/',
        {'patient_info': {'disease': 'multiple myeloma'}},
        format='json',
    )

    assert response.status_code == 200
    row = _row(response, trial)
    assert row['matchingType'] == 'potential'
    assert row['attributesToFillIn']


@pytest.mark.django_db
def test_an_answered_attribute_keeps_the_list_row_eligible(authed_client):
    """The control: once the patient answers the blank attribute, the same
    trial is eligible and there is nothing left to fill in."""
    trial = TrialFactory(disease='Multiple Myeloma', ecog_performance_status_max=2)

    response = authed_client.post(
        '/trials/match/',
        {'patient_info': {'disease': 'multiple myeloma', 'ecog_performance_status': 1}},
        format='json',
    )

    assert response.status_code == 200
    row = _row(response, trial)
    assert row['matchingType'] == 'eligible'
    assert row['attributesToFillIn'] == []


@pytest.mark.django_db
def test_a_patient_less_list_returns_no_missing_attributes(authed_client):
    """A list with no patient has no counts to compute, so the lazy context must
    answer `{}` rather than run the aggregate. The verdict itself is a separate,
    deliberately deferred question (#456): this only locks the input."""
    TrialFactory(disease='Multiple Myeloma', ecog_performance_status_max=2)

    response = authed_client.get('/trials/')

    assert response.status_code == 200
    assert response.data['results']
    for row in response.data['results']:
        assert row['attributesToFillIn'] == []


@pytest.mark.django_db
def test_list_and_detail_agree_on_a_blank_patient_attribute(authed_client):
    """The report behind this fix: one trial, one payload, two endpoints.

    The list used to answer `eligible` (empty counts) while the detail matcher
    answered `potential` for the same blank ECOG. Both must now say the same.
    """
    trial = TrialFactory(disease='Multiple Myeloma', ecog_performance_status_max=2)
    payload = {'patient_info': {'disease': 'multiple myeloma'}}

    list_response = authed_client.post('/trials/match/', payload, format='json')
    detail_response = authed_client.post(
        f'/trials/{trial.id}/match/', payload, format='json',
    )

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert _row(list_response, trial)['matchingType'] == 'potential'
    assert detail_response.data['matchingType'] == 'potential'
