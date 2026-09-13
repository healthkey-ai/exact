"""`GET /trials/{id}/` with no patient context (#362, #374, #423 — one bug,
filed three times).

`resolve_patient_info` returns None for a request with no inline payload and
no resolvable `person_id`. That is the documented public-browsing path, and
the LIST endpoint answers it with unfiltered results. Only the detail endpoint
fell over, five frames down, as `'NoneType' object has no attribute
'prior_therapy'`.

The answer is the list's answer: 200, and the trial unscored. A 400 would make
this the one endpoint that refuses a request the rest of the API serves, and
the trial's own requirements are worth reading whether or not there is anyone
to compare them to.
"""
import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from tests.factories import TrialFactory


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='no-patient-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


@pytest.mark.django_db
class TestDetailWithoutPatientContext:
    def test_it_answers_rather_than_500ing(self, authed_client):
        trial = TrialFactory(disease='multiple myeloma', brief_title='A study')

        response = authed_client.get(f'/trials/{trial.id}/')

        assert response.status_code == 200
        assert response.data['briefTitle'] == 'A study'

    def test_it_says_nothing_about_a_patient_who_is_not_there(self, authed_client):
        """No score and no verdict — rather than a zero, which is the sentinel
        for a disqualification, or `eligible`, which is a claim."""
        trial = TrialFactory(disease='multiple myeloma', age_low_limit=18)

        data = authed_client.get(f'/trials/{trial.id}/').data

        assert data['matchScore'] is None
        assert data['matchingType'] is None
        for row in data['details']['trialEligibilityAttributes']:
            assert not row.get('matchingType'), row
            assert row.get('uvalue') in (None, '', [])

    def test_the_trial_side_is_all_still_there(self, authed_client):
        """The point of answering at all — and asserted as PARITY with the
        patient-carrying response, not as "some rows came back".

        The first version of this test checked that `disease` was present.
        `disease` is hardcoded into `ATTR_NAME_ALWAYS_INCLUDED`, so it survives
        by construction: the test was green precisely because it named the one
        row that cannot be dropped, while nine others were being dropped
        silently. Twelve rows became two and nothing failed.
        """
        trial = TrialFactory(
            disease='multiple myeloma',
            age_low_limit=18,
            age_high_limit=75,
            ecog_performance_status_max=2,
            platelet_count_min=75,
            hemoglobin_level_min=8,
        )

        without = authed_client.get(f'/trials/{trial.id}/').data
        with_patient = authed_client.post(
            f'/trials/{trial.id}/match/',
            {'patient_info': {'disease': 'multiple myeloma', 'patientAge': 45}},
            format='json',
        ).data

        names = lambda d: sorted(
            r['name'] for r in d['details']['trialEligibilityAttributes']
        )
        assert names(without) == names(with_patient)
        # And they are the trial's real requirements, not a single always-on row.
        assert {'age', 'ecogPerformanceStatusMax', 'plateletCountMin'} <= set(names(without))

        rows = {r['name']: r for r in without['details']['trialEligibilityAttributes']}
        assert rows['disease']['value'] == 'multiple myeloma'
        assert rows['ecogPerformanceStatusMax']['value'] == 2

    def test_the_general_rows_claim_nothing_about_a_distance(self, authed_client):
        """`get_distance_penalty` answers 0 with no patient — the scoring
        neutral for "no distance known", which a located-less patient gets too.
        Printed in a cell labelled "distance Penalty" it reads as "this patient
        is next door", about somebody who is not there."""
        trial = TrialFactory(disease='multiple myeloma')

        data = authed_client.get(f'/trials/{trial.id}/').data
        general = {r['name']: r for r in data['details']['general']}

        assert general['distancePenalty']['value'] == ''
        assert general['matchScore']['uvalue'] in (None, '')

    def test_the_therapy_criteria_are_there_too(self, authed_client):
        """`therapies()` returned an empty dict with no patient, so the one
        kind of criterion a reader browsing a trial most wants to see was
        missing from the response with no indication it had been left out.

        Nothing in that method reads the patient — the patient side of these
        rows is filled in later by the matcher."""
        trial = TrialFactory(
            disease='multiple myeloma',
            therapies_required=['vrd'],
            therapies_excluded=['dara'],
        )

        data = authed_client.get(f'/trials/{trial.id}/').data
        rows = {r['name']: r for r in data['details']['trialEligibilityAttributes']}

        assert 'therapiesRequired' in rows, sorted(rows)
        assert rows['therapiesRequired']['value'] == ['vrd']
        # And still no claim about anybody.
        assert not rows['therapiesRequired'].get('matchingType')
        assert rows['therapiesRequired'].get('uvalue') in (None, [], '')

    def test_a_trial_with_sites_still_lists_them(self, authed_client):
        """`sorted_locations_by_distance` was handed the patient's geo point
        without a guard. With nobody, the sites come back in the trial's own
        order — which is the only honest answer to "closest to whom?"."""
        from trials.models import Location, LocationTrial

        trial = TrialFactory(disease='multiple myeloma')
        for name in ('Guy\'s', 'St Thomas\''):
            location = Location.objects.create(city='London', title=name)
            LocationTrial.objects.create(trial=trial, location=location)

        data = authed_client.get(f'/trials/{trial.id}/').data
        general = {r['name']: r for r in data['details']['general']}
        assert set(general['locationsName']['value']) == {"Guy's", "St Thomas'"}

    def test_the_same_request_with_a_patient_still_scores(self, authed_client):
        """The guard must not have turned the ordinary path off."""
        trial = TrialFactory(disease='multiple myeloma', age_low_limit=18, age_high_limit=75)

        response = authed_client.post(
            f'/trials/{trial.id}/match/',
            {'patient_info': {'disease': 'multiple myeloma', 'patientAge': 45}},
            format='json',
        )

        assert response.status_code == 200
        assert response.data['matchScore'] is not None
        assert response.data['matchingType'] is not None


@pytest.mark.django_db
class TestTheMatcherSaysSoItself:
    def test_building_one_without_a_patient_is_refused(self):
        """Rather than discovered five frames down. Every question the matcher
        answers is "how does THIS patient compare", so with nobody there is no
        answer — and the caller is the one who has to decide what to render
        instead."""
        from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher

        trial = TrialFactory(disease='multiple myeloma')
        with pytest.raises(ValueError, match='needs a patient'):
            UserToTrialAttrMatcher(trial=trial, patient_info=None)

    @pytest.mark.parametrize('method', ['get_match_score', 'matching_type'])
    def test_the_model_helpers_answer_none_instead(self, method):
        trial = TrialFactory(disease='multiple myeloma')
        assert getattr(trial, method)(None) is None
