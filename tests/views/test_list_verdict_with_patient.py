"""`matchingType` on the list, WITH a patient — #464.

The sibling of `test_list_verdict_without_patient.py`. That one is about a
request that named nobody, where the honest answer is `null`. This one is about
a request that named somebody and was answered with the wrong verdict anyway.

`get_serializer_context` set `'counts': {}` as a literal, inherited from the
initial port. `potential_attrs_for_trial` loops over `counts.keys()`, so the
loop never ran: `attributesToFillIn` came back `[]` for every row of every list
response, and `matchingType` — derived from it — was the constant `eligible`.

The real numbers were computed and dropped. The same response could report
`tabCounts {eligible: 0, potential: 1}` and label that very row `eligible`; the
row served on `?type=potential` said `eligible` too. `eligible` reads as "this
patient qualifies", so the list was making a claim about a person that the
matcher had explicitly declined to make.
"""
import pytest
from django.db import connections, router
from django.test.utils import CaptureQueriesContext
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from tests.factories import TrialFactory
from trials.models import Trial


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='verdict-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


MM = {'patient_info': {'disease': 'multiple myeloma'}}


def unanswerable(**kwargs):
    """A trial this patient cannot be judged against yet.

    `ecog_performance_status_max` is a requirement the payload says nothing
    about, so one attribute is unfilled and the trial is `potential` — the
    verdict the list could not express.
    """
    return TrialFactory(disease='Multiple Myeloma', ecog_performance_status_max=2, **kwargs)


def rows(client, path='/trials/search/match/', body=MM):
    response = client.post(path, body, format='json')
    assert response.status_code == 200
    return response


@pytest.mark.django_db
class TestTheVerdictIsNoLongerConstant:
    def test_a_trial_that_cannot_be_judged_says_so(self, authed_client):
        unanswerable()
        assert rows(authed_client).data['results'][0]['matchingType'] == 'potential'

    def test_and_names_what_would_answer_it(self, authed_client):
        """The field the UI uses to ask the one question that unblocks a
        trial. It was empty on every row, which made the feature dead rather
        than absent."""
        unanswerable()
        fill_in = rows(authed_client).data['results'][0]['attributesToFillIn']
        assert fill_in
        assert any(
            'ecog' in str(entry).lower() for entry in fill_in
        ), f'expected the unanswered ECOG requirement, got {fill_in}'

    def test_the_row_agrees_with_the_bar_above_it(self, authed_client):
        """The contradiction, as one assertion. A response that counts a trial
        potential and labels it eligible is wrong on its own terms, whichever
        half a reader believes."""
        unanswerable()
        response = rows(authed_client)
        counts = response.data['tabCounts']
        verdicts = [r['matchingType'] for r in response.data['results']]
        assert counts == {'eligible': 0, 'potential': 1}
        assert verdicts == ['potential']

    def test_the_potential_tab_serves_potential_rows(self, authed_client):
        """`?type=potential` narrows to trials the matcher could not judge, and
        then the rows said `eligible`. `TrialMatches.tsx` groups cards on this
        field, so the group was unreachable from the list."""
        unanswerable()
        verdicts = [
            r['matchingType']
            for r in rows(authed_client, '/trials/search/match/?type=potential').data['results']
        ]
        assert verdicts == ['potential']

    def test_the_list_alias_answers_the_same(self, authed_client):
        """`/trials/match/` and `/trials/search/match/` render the same
        serializer. Only `search` computed the counts, so the two doors gave
        different verdicts for one trial and one patient."""
        unanswerable()
        assert rows(authed_client, '/trials/match/').data['results'][0][
            'matchingType'
        ] == 'potential'


@pytest.mark.django_db
class TestWhatTheCountIsAbout:
    """The number on a chip answers "fill this in and N more trials can be
    judged". N is about the patient's matched corpus, not about the page —
    which is why the counts are taken over a scope of their own rather than
    off the queryset the response happens to be listing.
    """

    def test_narrowing_the_page_does_not_change_the_count(self, authed_client):
        """A saved-ids search shows two of the patient's trials. "Fill in your
        ECOG and one more trial can be judged" would then be true of the page
        and false of their situation — and it is their situation they are
        being asked about.

        Spelled as a `trial_ids` body rather than as a tab, because that is
        what the Favorites tab actually sends: `?type=favorites` is refused
        by `_reject_user_scoped_search_type`."""
        a, b = unanswerable(), unanswerable()
        unanswerable()  # not saved, but still in the corpus the question is about

        whole = rows(authed_client).data['results'][0]['attributesToFillIn']
        saved = rows(
            authed_client,
            body={**MM, 'trial_ids': [a.id, b.id]},
        ).data['results'][0]['attributesToFillIn']

        assert whole and saved
        assert [e['count'] for e in whole] == [e['count'] for e in saved] == [3]

    def test_a_text_search_does_not_change_it_either(self, authed_client):
        """`?search=` is the narrowing applied OUTSIDE `get_queryset`, by DRF
        after it. That makes it the leg most likely to be re-broken by a
        change to `list` or `search`, and the one the other two tests here do
        not cover."""
        unanswerable(brief_title='Findable')
        unanswerable()
        unanswerable()

        whole = rows(authed_client).data['results'][0]['attributesToFillIn']
        searched = rows(
            authed_client, '/trials/search/match/?search=Findable',
        ).data['results'][0]['attributesToFillIn']

        assert whole and searched
        assert [e['count'] for e in whole] == [e['count'] for e in searched] == [3]

    def test_the_two_aliases_agree(self, authed_client):
        """`/trials/match/` and `/trials/search/match/` render the same
        serializer for the same patient. Taking the counts off `get_queryset`
        gave them different answers — 2 and 1 — because the two actions reach
        that queryset at different points, one before the saved-ids widening
        and one after."""
        failing = TrialFactory(
            disease='Multiple Myeloma', age_low_limit=65, ecog_performance_status_max=2,
        )
        ok = unanswerable()
        body = {
            'patient_info': {'disease': 'multiple myeloma', 'patient_age': 40},
            'trial_ids': [failing.id, ok.id],
        }
        fill_in = {}
        for path in ('/trials/match/', '/trials/search/match/'):
            listed = {t['trialId']: t['attributesToFillIn'] for t in rows(authed_client, path, body).data['results']}
            fill_in[path] = [(e['userAttributeName'], e['count']) for e in listed[ok.id]]
        assert fill_in['/trials/match/'] == fill_in['/trials/search/match/']
        assert fill_in['/trials/match/']


@pytest.mark.django_db
class TestWhoPaysForTheAggregate:
    def test_the_detail_page_does_not(self, authed_client):
        """One serializer reads `counts`; the context is shared with the one
        that does not.

        Putting the VALUE in the context ran a corpus-wide aggregate on every
        detail request and discarded it — on the page a reader opens per
        trial. It is a `SimpleLazyObject` now, so the query goes to whoever
        reads it. Asserted on the SQL rather than on a query count, because
        the detail path issues ~200 of them and a ±1 assertion there would
        break for unrelated reasons.
        """
        trial = unanswerable()
        alias = router.db_for_read(Trial) or 'default'

        def aggregates(fn):
            with CaptureQueriesContext(connections[alias]) as ctx:
                assert fn().status_code == 200
            return [q for q in ctx.captured_queries if 'SUM' in q['sql']]

        assert aggregates(
            lambda: authed_client.post(f'/trials/{trial.id}/match/', MM, format='json')
        ) == []
        # And the list still does, or the line above would pass by the field
        # never being computed at all.
        assert aggregates(lambda: authed_client.post('/trials/match/', MM, format='json'))


@pytest.mark.django_db
class TestWhatMustNotChange:
    """Non-vacuity. Making everything `potential` would pass the class above."""

    def test_a_trial_with_nothing_left_to_ask_is_eligible(self, authed_client):
        TrialFactory(disease='Multiple Myeloma')
        response = rows(authed_client)
        assert response.data['results'][0]['matchingType'] == 'eligible'
        assert response.data['results'][0]['attributesToFillIn'] == []

    def test_a_request_that_names_nobody_still_claims_nothing(self, authed_client):
        """`{}` is still the right answer for counts without a patient, and
        `matchingType` is `null` before they are consulted (#456)."""
        unanswerable()
        response = rows(authed_client, body={})
        assert response.data['results'][0]['matchingType'] is None
        assert response.data['results'][0]['attributesToFillIn'] == []

    def test_the_tab_counts_are_unchanged(self, authed_client):
        """The counts were always right; only their delivery to the serializer
        was missing. A fix that moved them would show up here."""
        unanswerable()
        TrialFactory(disease='Multiple Myeloma')
        assert rows(authed_client).data['tabCounts'] == {'eligible': 1, 'potential': 1}

    def test_both_verdicts_can_appear_in_one_response(self, authed_client):
        """Per row, not per response — the shape a reader actually sees on the
        combined tab."""
        unanswerable()
        TrialFactory(disease='Multiple Myeloma')
        verdicts = sorted(r['matchingType'] for r in rows(authed_client).data['results'])
        assert verdicts == ['eligible', 'potential']
