"""`?trialPurpose=` takes several codes and answers with their union (#428).

The parser read it with `_str`, so a multiselect sending two purposes had one
of them silently kept — the reader would see a filter they did not ask for,
with nothing saying which half had been dropped. Ported from the `2omop` line,
where CB #4663 already landed.

These go through the view because the two halves were ported separately: a
parser that returns a list is no use if the queryset still reads it as a
scalar, and each half has its own unit tests that pass either way.
"""
import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from tests.factories import TrialFactory, TrialPurposeFactory


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='purpose-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


@pytest.fixture
def corpus(db):
    treatment = TrialPurposeFactory(code='treatment', title='Treatment')
    prevention = TrialPurposeFactory(code='prevention', title='Prevention')
    diagnostic = TrialPurposeFactory(code='diagnostic', title='Diagnostic')
    return {
        'treatment': TrialFactory(disease='multiple myeloma', purpose=treatment).id,
        'prevention': TrialFactory(disease='multiple myeloma', purpose=prevention).id,
        'diagnostic': TrialFactory(disease='multiple myeloma', purpose=diagnostic).id,
        'none': TrialFactory(disease='multiple myeloma', purpose=None).id,
    }


def _ids(client, query=''):
    response = client.get(f'/trials/search/{query}')
    assert response.status_code == 200, response.data
    return {row['trialId'] for row in response.data['results']}


@pytest.mark.django_db
class TestSeveralPurposesAtOnce:
    def test_repeated_params_are_the_union(self, authed_client, corpus):
        ids = _ids(authed_client, '?trialPurpose=treatment&trialPurpose=prevention')
        assert ids == {corpus['treatment'], corpus['prevention']}

    def test_comma_separated_means_the_same_thing(self, authed_client, corpus):
        """Not every caller can repeat a param — the remote sends the comma
        form because axios spells an array `trialPurpose[]=`, which Django's
        `getlist` does not see."""
        ids = _ids(authed_client, '?trialPurpose=treatment,prevention')
        assert ids == {corpus['treatment'], corpus['prevention']}

    def test_the_union_is_not_the_whole_corpus(self, authed_client, corpus):
        """Non-vacuity: a parser that dropped the filter entirely would make
        every assertion above pass if it returned everything."""
        ids = _ids(authed_client, '?trialPurpose=treatment,prevention')
        assert corpus['diagnostic'] not in ids
        assert corpus['none'] not in ids

    def test_one_purpose_still_behaves_as_it_did(self, authed_client, corpus):
        """The pre-#4663 contract. A caller sending a single value must not
        notice this change."""
        assert _ids(authed_client, '?trialPurpose=treatment') == {corpus['treatment']}

    def test_case_and_whitespace_do_not_matter(self, authed_client, corpus):
        ids = _ids(authed_client, '?trialPurpose=TREATMENT,%20Prevention')
        assert ids == {corpus['treatment'], corpus['prevention']}

    def test_a_repeated_code_is_one_clause_and_one_row(self, authed_client, corpus):
        """The filter must not fan the join out: `purpose` is a FK, but an OR
        of two clauses selecting the same rows is where a duplicate would come
        from if this were ever rewritten as a join."""
        response = authed_client.get('/trials/search/?trialPurpose=treatment,TREATMENT')
        rows = response.data['results']
        assert [row['trialId'] for row in rows] == [corpus['treatment']]

    def test_an_unknown_code_narrows_to_nothing(self, authed_client, corpus):
        assert _ids(authed_client, '?trialPurpose=nonexistent') == set()

    def test_an_unknown_code_alongside_a_real_one_takes_nothing_away(
        self, authed_client, corpus,
    ):
        ids = _ids(authed_client, '?trialPurpose=treatment,nonexistent')
        assert ids == {corpus['treatment']}

    def test_no_purpose_is_no_filter(self, authed_client, corpus):
        """Including the trial whose purpose was never extracted: this filter
        is strict, so it only applies when a caller asks for it."""
        assert _ids(authed_client) == set(corpus.values())
        assert _ids(authed_client, '?trialPurpose=') == set(corpus.values())

    def test_the_cap_is_enforced_on_the_query_string(self, authed_client, corpus):
        """Every code becomes another OR clause. The taxonomy has nine entries,
        so a longer list is junk, and an unvalidated query string must not turn
        into thousands of them."""
        from trials.services.study_preferences import study_preferences_from_query_params

        many = ','.join(f'code_{i}' for i in range(200))
        assert len(study_preferences_from_query_params({'trialPurpose': many}).trial_purpose) == 50
        # ...and the request still answers rather than erroring out.
        assert authed_client.get(f'/trials/search/?trialPurpose={many}').status_code == 200
