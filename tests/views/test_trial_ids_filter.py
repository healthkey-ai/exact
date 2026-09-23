"""`trial_ids` — how the federated UI's Favorites tab narrows the match.

The bookmarks live in PROMOP, which knows nothing about matching. The ids
come down in the request body and the narrowing happens inside EXACT's
queryset, because that is the only place that can sort and paginate them
alongside the match scores and produce a total that agrees with the rows it
listed. Phase 2 of docs/federated-ui-parity-plan.md.
"""
import csv
import io

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
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='trial-ids-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


MM = {'disease': 'multiple myeloma'}

#: The same patient, carrying an age. A trial with `age_low_limit=65` is then
#: an ELIGIBILITY failure for them — `eligible_for_min_max_value` drops it —
#: rather than a disease mismatch, which `filter_by_patient_info` also applies
#: but which is scoping, not a verdict. #568 is about the first kind, so the
#: tests that describe it use this patient.
YOUNG_MM = {'disease': 'multiple myeloma', 'patient_age': 40}


def post(client, ids=None, path='/trials/search/match/', patient=MM, **extra):
    body = {'patient_info': patient, **extra}
    if ids is not None:
        body['trial_ids'] = ids
    return client.post(path, body, format='json')


@pytest.mark.django_db
class TestNarrowing:
    def test_keeps_only_the_ids_asked_for(self, authed_client):
        wanted = TrialFactory(disease='Multiple Myeloma')
        TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [wanted.id])
        assert response.status_code == 200
        assert [t['trialId'] for t in response.data['results']] == [wanted.id]

    def test_the_total_agrees_with_the_narrowed_set(self, authed_client):
        """Narrowing has to happen before the matcher, or `itemsTotalCount`
        counts a corpus the response does not list."""
        wanted = TrialFactory(disease='Multiple Myeloma')
        for _ in range(3):
            TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [wanted.id])
        assert response.data['itemsTotalCount'] == 1

    def test_the_tab_counts_describe_the_narrowed_set_too(self, authed_client):
        eligible = TrialFactory(disease='Multiple Myeloma')
        TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [eligible.id])
        counts = response.data['tabCounts']
        assert counts['eligible'] + counts['potential'] == 1

    def test_an_id_that_does_not_match_the_patient_is_listed_and_marked(self, authed_client):
        """It used to be dropped, on the reasoning that the filter narrows and
        does not override. What that produced on screen was a Favorites tab
        whose badge read 1 over the words "No trials found" — the trial
        unreachable from the only tab that counts it (#568). These ids are
        bookmarks the reader made by hand, so they are listed; the mark is
        what stops one reading as a match."""
        bc = TrialFactory(disease='Breast Cancer')
        mm = TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [bc.id, mm.id])
        verdicts = {t['trialId']: t['matchingType'] for t in response.data['results']}
        assert verdicts == {bc.id: 'not_eligible', mm.id: 'eligible'}

    def test_sorting_still_applies_within_the_narrowed_set(self, authed_client):
        small = TrialFactory(disease='Multiple Myeloma', enrollment_count=10)
        large = TrialFactory(disease='Multiple Myeloma', enrollment_count=900)
        response = post(
            authed_client, [small.id, large.id],
            path='/trials/search/match/?sort=enrollment',
        )
        ids = [t['trialId'] for t in response.data['results']]
        assert ids.index(large.id) < ids.index(small.id)

    def test_it_also_applies_to_the_detail_endpoint_path(self, authed_client):
        """`match_detail` shares `get_queryset`. A caller sending both an id
        in the URL and a `trial_ids` list that excludes it must get a 404,
        not a trial the filter said to leave out."""
        trial = TrialFactory(disease='Multiple Myeloma')
        other = TrialFactory(disease='Multiple Myeloma')
        response = authed_client.post(
            f'/trials/{trial.id}/match/',
            {'patient_info': MM, 'trial_ids': [other.id]},
            format='json',
        )
        assert response.status_code == 404


@pytest.mark.django_db
class TestEmptyIsNotAbsent:
    def test_an_empty_list_returns_nothing(self, authed_client):
        """The case that matters. `[]` means "my bookmarks, of which there
        are none" — answering it with the whole corpus would show a reader
        every trial under a Favorites tab they have not used."""
        TrialFactory(disease='Multiple Myeloma')
        TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [])
        assert response.status_code == 200
        assert response.data['results'] == []
        assert response.data['itemsTotalCount'] == 0

    def test_omitting_the_key_is_no_filter_at_all(self, authed_client):
        TrialFactory(disease='Multiple Myeloma')
        TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client)
        assert response.data['itemsTotalCount'] == 2


@pytest.mark.django_db
class TestValidation:
    def test_a_list_longer_than_the_cap_is_refused(self, authed_client):
        """Every id becomes part of an `IN (...)`. Unbounded, this is a
        request that expands into thousands of clauses."""
        TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, list(range(1, 502)))
        assert response.status_code == 400
        assert 'trial_ids' in response.data

    def test_the_cap_itself_is_accepted(self, authed_client):
        trial = TrialFactory(disease='Multiple Myeloma')
        ids = [trial.id] + list(range(10_000, 10_499))
        assert len(ids) == 500
        assert post(authed_client, ids).status_code == 200

    @pytest.mark.parametrize('payload', ['1,2,3', 42, {'id': 1}])
    def test_a_non_list_is_refused(self, authed_client, payload):
        response = post(authed_client, payload)
        assert response.status_code == 400

    @pytest.mark.parametrize('bad', ['abc', None, 3.5, [1]])
    def test_an_unparseable_id_is_refused(self, authed_client, bad):
        response = post(authed_client, [1, bad])
        assert response.status_code == 400

    def test_a_boolean_is_not_a_trial_id(self, authed_client):
        """`True` is an `int` in Python and would silently become trial 1."""
        response = post(authed_client, [True])
        assert response.status_code == 400

    def test_numeric_strings_are_accepted(self, authed_client):
        """JSON from a browser often carries ids as strings."""
        trial = TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [str(trial.id)])
        assert [t['trialId'] for t in response.data['results']] == [trial.id]

    def test_the_rejected_favorites_type_now_names_the_real_path(self, authed_client):
        TrialFactory(disease='Multiple Myeloma')
        response = authed_client.get('/trials/search/?type=favorites')
        assert response.status_code == 400
        assert 'trial_ids' in str(response.data['type'])


@pytest.mark.django_db
class TestIdsThatLookNumericButAreNot:
    """`str.isdigit()` is not "is an integer literal".

    Guarding with it and converting afterwards is the bug: for some
    characters the guard passes and `int()` then raises, which leaves the
    view as a 500 rather than the 400 every other bad id gets.
    """

    @pytest.mark.parametrize('value', ['²', '³', '¹'])
    def test_a_superscript_digit_is_a_400_not_a_500(self, authed_client, value):
        response = post(authed_client, [value])
        assert response.status_code == 400

    @pytest.mark.parametrize('value', ['--5', '- 5', '+5'])
    def test_a_malformed_sign_is_refused(self, authed_client, value):
        response = post(authed_client, [value])
        assert response.status_code == 400

    def test_fullwidth_digits_are_refused(self, authed_client):
        """`int('１２３')` succeeds and yields 123 — a different trial from
        the literal text the caller sent."""
        assert post(authed_client, ['１２３']).status_code == 400

    def test_a_negative_id_is_refused(self, authed_client):
        """A primary key is never negative, and admitting a sign is what let
        `'--5'` through."""
        assert post(authed_client, [-1]).status_code == 400
        assert post(authed_client, ['-1']).status_code == 400

    def test_an_absurdly_long_digit_string_is_a_400(self, authed_client):
        """Python refuses to parse an int past a digit limit, which would
        have escaped as a 500 too."""
        assert post(authed_client, ['9' * 5000]).status_code == 400

    def test_the_largest_id_the_column_can_hold_is_just_a_miss(self, authed_client):
        TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [2 ** 63 - 1])
        assert response.status_code == 200
        assert response.data['itemsTotalCount'] == 0

    @pytest.mark.parametrize('value', [2 ** 63, 10 ** 18 * 10, '9999999999999999999'])
    def test_an_id_beyond_the_bigint_range_is_refused(self, authed_client, value):
        """Not pedantry about a value that would not match anyway: above the
        bigint range PostgreSQL types the constant as `numeric`, coerces the
        `id` column to compare it, and gives up the primary-key index. A
        list of 500 of those is a table scan in the shape of a bookmarks
        lookup."""
        assert post(authed_client, [value]).status_code == 400


@pytest.mark.django_db
class TestQueryStringIsNotTheChannel:
    def test_trial_ids_in_the_query_string_is_refused(self, authed_client):
        """Ignored, it answers a bookmarks question with the whole corpus —
        the same wrong answer an empty list is guarded against, reached
        through a different door. The body is the channel."""
        TrialFactory(disease='Multiple Myeloma')
        response = authed_client.get('/trials/search/?trial_ids=1,2')
        assert response.status_code == 400
        assert 'trial_ids' in response.data

    def test_the_body_still_works_with_a_query_string_present(self, authed_client):
        trial = TrialFactory(disease='Multiple Myeloma')
        response = post(
            authed_client, [trial.id], path='/trials/search/match/?sort=distance'
        )
        assert response.status_code == 200


@pytest.mark.django_db
class TestSavedTrialsTheMatcherWouldDrop:
    """#568 — a bookmark the patient no longer qualifies for.

    The badge over these tabs counts what the reader saved, which stays true
    whether or not those trials still match. The list used to answer the
    narrower question, so the two contradicted each other on one screen. Now
    the list answers the same question the badge does, and says per row where
    the matcher stands.
    """

    def test_the_total_counts_every_saved_trial(self, authed_client):
        """The number under the tab and the rows beneath it are the same set.
        This is the contradiction #568 is about, measured."""
        failing = TrialFactory(disease='Multiple Myeloma', age_low_limit=65)
        ok = TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [failing.id, ok.id], patient=YOUNG_MM)
        assert response.data['itemsTotalCount'] == 2

    def test_a_saved_tab_holding_only_a_failing_trial_is_not_empty(self, authed_client):
        """The reported case exactly: one bookmark, and the patient is too
        young for it. Favorites read "1" over "No trials found"."""
        failing = TrialFactory(disease='Multiple Myeloma', age_low_limit=65)
        TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [failing.id], patient=YOUNG_MM)
        assert [t['trialId'] for t in response.data['results']] == [failing.id]
        assert response.data['results'][0]['matchingType'] == 'not_eligible'

    def test_a_disease_mismatch_is_listed_too(self, authed_client):
        """`filter_by_patient_info` also carries `disease__iexact`, which is
        scoping rather than a verdict — and skipping it is how a bookmark for
        another disease comes back. Kept as its own case so the widening's one
        non-eligibility consequence is stated, not discovered."""
        bc = TrialFactory(disease='Breast Cancer')
        mm = TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [bc.id, mm.id])
        verdicts = {t['trialId']: t['matchingType'] for t in response.data['results']}
        assert verdicts == {bc.id: 'not_eligible', mm.id: 'eligible'}

    def test_the_tab_counts_still_describe_only_who_qualifies(self, authed_client):
        """The widening must not leak into the verdict counts.

        `potential_attrs_count` asks whether the patient LEFT A FIELD BLANK,
        never whether they conflict, so a trial the eligibility filter threw
        out scores 0 and would be counted as `eligible` — a verdict nobody
        produced, arriving through the door `_tab_counts` already closed for
        `?type=all`. The counts are taken from the set before widening; this
        is the test that fails if that is ever simplified away.
        """
        failing = TrialFactory(disease='Multiple Myeloma', age_low_limit=65)
        ok = TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [failing.id, ok.id], patient=YOUNG_MM)
        counts = response.data['tabCounts']
        assert counts['eligible'] + counts['potential'] == 1

    def test_the_corpus_search_still_drops_it(self, authed_client):
        """Nothing widens without a `trial_ids` list. A patient asking which
        trials match them must not be handed one that does not."""
        TrialFactory(disease='Multiple Myeloma', age_low_limit=65)
        ok = TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, patient=YOUNG_MM)
        assert [t['trialId'] for t in response.data['results']] == [ok.id]

    def test_an_explicit_verdict_type_still_narrows(self, authed_client):
        """`?type=eligible` names a verdict. Answering it with rows that hold
        the opposite one would be a different question answered."""
        failing = TrialFactory(disease='Multiple Myeloma', age_low_limit=65)
        ok = TrialFactory(disease='Multiple Myeloma')
        response = post(
            authed_client, [failing.id, ok.id], patient=YOUNG_MM,
            path='/trials/search/match/?type=eligible',
        )
        assert [t['trialId'] for t in response.data['results']] == [ok.id]

    def test_a_matching_saved_trial_keeps_its_own_verdict(self, authed_client):
        """The mark is per row, not per response: widening one row must not
        relabel the others."""
        failing = TrialFactory(disease='Multiple Myeloma', age_low_limit=65)
        ok = TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [failing.id, ok.id], patient=YOUNG_MM)
        verdicts = {t['trialId']: t['matchingType'] for t in response.data['results']}
        assert verdicts[ok.id] == 'eligible'

    def test_nothing_is_marked_when_every_saved_trial_matches(self, authed_client):
        """The common case pays nothing and reads exactly as before."""
        first = TrialFactory(disease='Multiple Myeloma')
        second = TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [first.id, second.id])
        verdicts = [t['matchingType'] for t in response.data['results']]
        assert verdicts == ['eligible', 'eligible']

    def test_a_marked_row_sinks_under_the_reader_s_sort(self, authed_client):
        """`?sort=enrollment` would have put the failing trial first — it has
        the larger enrolment. A row the patient cannot join does not lead a
        list the reader is scanning for one they can."""
        failing = TrialFactory(disease='Multiple Myeloma', age_low_limit=65,
                               enrollment_count=900)
        ok = TrialFactory(disease='Multiple Myeloma', enrollment_count=10)
        response = post(
            authed_client, [failing.id, ok.id], patient=YOUNG_MM,
            path='/trials/search/match/?sort=enrollment',
        )
        assert [t['trialId'] for t in response.data['results']] == [ok.id, failing.id]

    def test_the_sort_still_orders_the_rows_that_match(self, authed_client):
        """Sinking the marked ones must not flatten the order of the rest."""
        failing = TrialFactory(disease='Multiple Myeloma', age_low_limit=65)
        small = TrialFactory(disease='Multiple Myeloma', enrollment_count=10)
        large = TrialFactory(disease='Multiple Myeloma', enrollment_count=900)
        response = post(
            authed_client, [failing.id, small.id, large.id], patient=YOUNG_MM,
            path='/trials/search/match/?sort=enrollment',
        )
        assert [t['trialId'] for t in response.data['results']] == [
            large.id, small.id, failing.id,
        ]

    def test_it_sinks_under_the_default_order_too(self, authed_client):
        """The default order is `-match_score`, and that score reads 100 on a
        trial the patient conflicts with — so without this the failing
        bookmark led the list, ranked by a number the response does not even
        send. Measured on `/trials/match/`, whose ordering is fixed."""
        failing = TrialFactory(disease='Multiple Myeloma', age_low_limit=65)
        ok = TrialFactory(disease='Multiple Myeloma')
        response = post(
            authed_client, [failing.id, ok.id], patient=YOUNG_MM,
            path='/trials/match/',
        )
        assert [t['trialId'] for t in response.data['results']] == [ok.id, failing.id]

    def test_the_list_alias_answers_the_same_way(self, authed_client):
        """`/trials/match/` and `/trials/search/match/` are two doors onto one
        saved set. A bookmark visible through one and missing through the
        other is the same contradiction wearing a different URL."""
        failing = TrialFactory(disease='Multiple Myeloma', age_low_limit=65)
        ok = TrialFactory(disease='Multiple Myeloma')
        response = post(
            authed_client, [failing.id, ok.id], patient=YOUNG_MM,
            path='/trials/match/',
        )
        assert response.status_code == 200
        verdicts = {t['trialId']: t['matchingType'] for t in response.data['results']}
        assert verdicts == {failing.id: 'not_eligible', ok.id: 'eligible'}

    def test_the_detail_path_still_refuses_an_id_outside_the_list(self, authed_client):
        """The widening is about a LIST of saved trials. `match_detail` never
        ran the eligibility filter to begin with, so a `trial_ids` list that
        excludes the id in the URL still answers 404 rather than quietly
        serving the trial with a mark on it."""
        trial = TrialFactory(disease='Multiple Myeloma')
        other = TrialFactory(disease='Breast Cancer')
        response = authed_client.post(
            f'/trials/{trial.id}/match/',
            {'patient_info': MM, 'trial_ids': [other.id]},
            format='json',
        )
        assert response.status_code == 404

    def test_the_all_matching_case_builds_no_second_queryset(self, authed_client):
        """The widening is for the tab that needs it and nothing else.

        When every saved trial still matches there is nothing to add back, and
        the request must not pay for a second queryset to discover that.
        Asserted as a comparison rather than an absolute count, so an
        unrelated query somewhere else does not fail this for the wrong
        reason — what is pinned is that the common case is the cheaper one.
        """
        matching = [TrialFactory(disease='Multiple Myeloma') for _ in range(2)]
        failing = TrialFactory(disease='Breast Cancer')

        # Through the router, not a hard-coded alias: the trials tables live
        # on their own connection only when `TRIALS_DATABASE_URL` is set, and
        # naming `trials` outright fails the test on the configuration CI
        # actually runs.
        alias = router.db_for_read(Trial) or 'default'

        def queries_for(ids):
            with CaptureQueriesContext(connections[alias]) as ctx:
                assert post(authed_client, ids).status_code == 200
            return len(ctx)

        assert queries_for([t.id for t in matching]) < queries_for(
            [matching[0].id, failing.id]
        )

    def test_the_score_beside_the_mark_is_the_one_the_verdict_means(self, authed_client):
        """`match_score` counts which criteria could be EVALUATED and never
        compares values, so a trial the patient conflicts with scores 100 —
        measured, with a plain age conflict. Left alone that is a green pill
        arguing with the verdict beside it.

        0 rather than null, because the matcher does not treat the two as
        separable: `match_score_and_status` returns `(0, 'not_eligible')`
        unconditionally, and that is the pair the detail endpoint sends for
        the same trial. The list now says what the page it opens says.
        """
        failing = TrialFactory(disease='Multiple Myeloma', age_low_limit=65)
        ok = TrialFactory(disease='Multiple Myeloma')
        response = post(authed_client, [failing.id, ok.id], patient=YOUNG_MM)
        scores = {t['trialId']: t['matchScore'] for t in response.data['results']}
        assert scores[failing.id] == 0
        assert scores[ok.id] == 100

    def test_the_list_and_the_detail_page_agree_on_that_score(self, authed_client):
        """The card links to the detail page; handing the reader two numbers
        for one trial is the contradiction this change exists to remove, moved
        one click away."""
        failing = TrialFactory(disease='Multiple Myeloma', age_low_limit=65)
        listed = post(authed_client, [failing.id], patient=YOUNG_MM).data['results'][0]
        detail = authed_client.post(
            f'/trials/{failing.id}/match/', {'patient_info': YOUNG_MM}, format='json',
        ).data
        assert (listed['matchScore'], listed['matchingType']) == (
            detail['matchScore'], detail['matchingType']
        )

    def test_the_export_says_what_the_list_says(self, authed_client):
        """The file is the list, and this is the field the change is about.
        A CSV row reading `not_eligible,100` goes to an appointment."""
        failing = TrialFactory(disease='Multiple Myeloma', age_low_limit=65)
        response = authed_client.post(
            '/trials/export/',
            {'patient_info': YOUNG_MM, 'trial_ids': [failing.id]},
            format='json',
        )
        assert response.status_code == 200
        # `utf-8-sig`: the file opens with a BOM, which otherwise lands in
        # the first header name and hides the `Trial ID` column.
        body = b''.join(response.streaming_content).decode('utf-8-sig')
        # By column, not by substring: `100` turns up in enrolment counts and
        # scores all over a trial row.
        rows = list(csv.DictReader(io.StringIO(body)))
        row = next(r for r in rows if r['Trial ID'] == str(failing.id))
        assert row['Match'] == 'not_eligible'
        assert row['Matching score'] == '0'

    def test_the_graph_does_not_widen(self, authed_client):
        """A node cannot carry the mark — the graph buckets on `match_score`,
        which reads 100 on a conflicting trial, so a widened node would be
        filed under "fully matched". A verdict flipped, not merely unmarked.
        """
        failing = TrialFactory(disease='Multiple Myeloma', age_low_limit=65)
        ok = TrialFactory(disease='Multiple Myeloma')
        response = authed_client.post(
            '/trials-graph/graph/match/',
            {'patient_info': YOUNG_MM, 'trial_ids': [failing.id, ok.id]},
            format='json',
        )
        assert response.status_code == 200
        # On the nodes, not on the serialized blob: the response echoes the
        # patient too, and a bare id substring matches digits in there.
        assert [t['trialId'] for t in response.data['trials']] == [ok.id]

    def test_a_patientless_search_marks_nothing(self, authed_client):
        """With nobody named there is no verdict to carry, so no row can be
        marked and none is hidden either — the whole saved set comes back
        unlabelled. Two independent reasons produce that (`matchingType` is
        `None` without a patient, #456; and the eligibility filter dropped
        nothing, so there are no ids to mark), which is why this asserts the
        rows are all there as well as unlabelled.
        """
        failing = TrialFactory(disease='Multiple Myeloma', age_low_limit=65)
        ok = TrialFactory(disease='Multiple Myeloma')
        response = authed_client.post(
            '/trials/search/match/',
            {'trial_ids': [failing.id, ok.id]},
            format='json',
        )
        assert response.status_code == 200
        assert response.data['itemsTotalCount'] == 2
        assert {t['matchingType'] for t in response.data['results']} == {None}
