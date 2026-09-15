"""Patient-resolution error handling + memoization (#156, #159, #160).

#156: a supplied-but-unbuildable patient payload must surface as a 400, not be
swallowed into a silent None that runs the matcher with no patient context
(which returns an unfiltered/unscored trial list that looks valid).

#159/#160: patient context must be resolved at most once per request, even
though both get_queryset and get_serializer_context need it.
"""
import pytest
from unittest.mock import patch

from django.test import override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from tests.factories import TrialFactory


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='resolve-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


@pytest.mark.django_db
class TestPatientResolveErrors:
    def test_unbuildable_inline_payload_returns_400(self, authed_client):
        # A non-dict patient_info can't be built -> 400, not a silent 200 with
        # an unfiltered list.
        resp = authed_client.post(
            '/trials/match/', {'patient_info': 'not-a-dict'}, format='json'
        )
        assert resp.status_code == 400
        assert 'patient' in str(resp.data).lower()

    def test_patient_info_resolved_once_per_request(self, authed_client):
        TrialFactory(disease='Multiple Myeloma')
        with patch(
            'trials.api.trials_views.resolve_patient_info', return_value=None
        ) as mock_resolve:
            resp = authed_client.get('/trials/')
        assert resp.status_code == 200
        assert mock_resolve.call_count == 1

    def test_non_inline_resolve_error_is_not_masked_as_400(self, authed_client):
        # When no inline payload was supplied (e.g. the person_id/PROMOP path),
        # an unexpected build error is a real server/upstream bug and must NOT
        # be relabeled as a client 400 — it should surface as a 500 (#156).
        authed_client.raise_request_exception = False
        with patch(
            'trials.api.trials_views.resolve_patient_info',
            side_effect=RuntimeError('promop adapter blew up'),
        ):
            resp = authed_client.get('/trials/')
        assert resp.status_code == 500

    @override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=False)
    def test_person_id_lookup_returns_403_when_gate_off(self, authed_client):
        # IDOR gate (#150): with the person_id path disabled, a ?person_id=
        # request is rejected (403), not silently served as a no-patient list.
        TrialFactory(disease='Multiple Myeloma')
        resp = authed_client.get('/trials/?person_id=123')
        assert resp.status_code == 403

    @override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=True)
    def test_unfetchable_person_id_returns_502_not_the_whole_corpus(self, authed_client):
        """The gate's other half (#448): with the path *enabled*, a person_id
        whose patient can't be fetched — upstream down, or no usable credential
        after a botched secret rotation — must not answer 200 with every trial
        in the corpus. That is the #156 failure mode arriving through the
        person_id door instead of the inline one."""
        TrialFactory(disease='Multiple Myeloma')
        with patch(
            'trials.services.patient_info.promop_client.PromopClient.fetch_patient',
            return_value=None,
        ) as mock_fetch:
            resp = authed_client.get('/trials/?person_id=9001')
        assert mock_fetch.called
        assert resp.status_code == 502
        assert 'patient' in str(resp.data).lower()

    @override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=True)
    @pytest.mark.parametrize('bad_id', ['abc', '0', '-1', '1.5', '', '٣', ' 9001'])
    def test_malformed_person_id_in_the_query_is_400_and_never_reaches_promop(
            self, authed_client, bad_id):
        """A person_id the *client* got wrong must not be reported as an
        upstream failure: 502 would blame a PROMOP that was never called, and
        any client or LB that retries 5xx would retry a permanently
        unsatisfiable request forever."""
        TrialFactory(disease='Multiple Myeloma')
        with patch(
            'trials.services.patient_info.promop_client.requests.get',
        ) as mock_get:
            resp = authed_client.get(f'/trials/?person_id={bad_id}')
        assert resp.status_code == 400
        assert 'person_id' in str(resp.data).lower()
        mock_get.assert_not_called()

    @override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=True)
    @pytest.mark.parametrize('bad_id', [0, -1, 1.5, True, False, '1.5', 'abc', '', None])
    def test_a_json_body_person_id_is_checked_by_type_not_coerced(
            self, authed_client, bad_id):
        """A JSON body carries real types, and `int()` truncates rather than
        refusing: `int(1.5)` and `int(True)` are both 1, so these would have
        fetched and matched patient 1 — a different, real patient. And `0` is
        falsy, so reading the field with `or` made it look like no patient was
        named at all: a whole-corpus 200, past a gate that only fires when a
        person_id is present."""
        TrialFactory(disease='Multiple Myeloma')
        with patch(
            'trials.services.patient_info.promop_client.requests.get',
        ) as mock_get:
            resp = authed_client.post(
                '/trials/match/', {'person_id': bad_id}, format='json')
        assert resp.status_code == 400
        mock_get.assert_not_called()

    @override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=True)
    @pytest.mark.parametrize('bad_id', [
        '9' * 5000,                      # CPython refuses int() past 4300 digits
        '9' * 20,                        # past bigint
        str(9223372036854775807 + 1),    # one past bigint
    ])
    def test_an_oversized_person_id_is_400_not_500(self, authed_client, bad_id):
        """The length bound is applied before `int()`, which raises rather than
        converting a string of more than 4300 digits — that ValueError would
        escape the 400 and surface as a server error for what is client input."""
        TrialFactory(disease='Multiple Myeloma')
        with patch(
            'trials.services.patient_info.promop_client.requests.get',
        ) as mock_get:
            resp = authed_client.get(f'/trials/?person_id={bad_id}')
        assert resp.status_code == 400
        mock_get.assert_not_called()

    @override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=False)
    @pytest.mark.parametrize('bad_id', [0, '', 1.5])
    def test_a_falsy_person_id_still_meets_the_gate(self, authed_client, bad_id):
        """The 403 gate fires on a person_id being *supplied*, so a falsy one
        must not slip past it into a patientless search."""
        TrialFactory(disease='Multiple Myeloma')
        resp = authed_client.post(
            '/trials/match/', {'person_id': bad_id}, format='json')
        assert resp.status_code == 403

    @override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=True)
    def test_a_repeated_person_id_takes_the_last_one(self, authed_client):
        """QueryDict is last-wins, so `?person_id=1&person_id=2` reads patient 2.
        Unchanged behaviour, pinned because it decides *whose record* is
        returned: switching to `getlist` or `.get()` semantics would silently
        change that, and nothing else in the suite would notice."""
        TrialFactory(disease='Multiple Myeloma')
        with patch(
            'trials.services.patient_info.promop_client.PromopClient.fetch_patient',
            return_value={'person_id': 2, 'disease': 'multiple myeloma'},
        ) as mock_fetch:
            resp = authed_client.get('/trials/?person_id=1&person_id=2')
        assert resp.status_code == 200
        assert mock_fetch.call_args.args[0] == '2'

    @override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=False)
    @pytest.mark.parametrize('query', ['person_id=', 'person_id'])
    def test_an_empty_query_person_id_still_meets_the_gate(self, authed_client, query):
        """The falsy-id gate test covers the JSON body; this is the query-string
        half. An empty value is the key being present, so it is a named patient
        and the disabled path must refuse it rather than browse."""
        TrialFactory(disease='Multiple Myeloma')
        resp = authed_client.get(f'/trials/?{query}')
        assert resp.status_code == 403

    @override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=True)
    @pytest.mark.parametrize('good_id', [9001, '9001'])
    def test_a_well_formed_person_id_still_resolves(self, authed_client, good_id):
        """The strictness must not cost the shapes real callers send: the
        federation host sends a string, the dev harness a JSON integer."""
        TrialFactory(disease='Multiple Myeloma')
        with patch(
            'trials.services.patient_info.promop_client.PromopClient.fetch_patient',
            return_value={'person_id': 9001, 'disease': 'multiple myeloma'},
        ) as mock_fetch:
            resp = authed_client.post(
                '/trials/match/', {'person_id': good_id}, format='json')
        assert resp.status_code == 200
        assert mock_fetch.call_args.args[0] == good_id

    @override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=True)
    def test_unfetchable_person_id_costs_one_upstream_round_trip(self, authed_client):
        """The failure is memoized like a success: get_queryset and
        get_serializer_context both resolve, and an unreachable PROMOP must not
        cost a timeout per call site (#159/#160)."""
        TrialFactory(disease='Multiple Myeloma')
        with patch(
            'trials.services.patient_info.promop_client.PromopClient.fetch_patient',
            return_value=None,
        ) as mock_fetch:
            resp = authed_client.get('/trials/?person_id=9001')
        assert resp.status_code == 502
        assert mock_fetch.call_count == 1

    @override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=True)
    @pytest.mark.parametrize('accept', ['application/json', 'text/html'])
    def test_the_error_survives_the_browsable_renderer(self, authed_client, accept):
        """Same cause must produce the same status to every client.

        On a POST route the browsable renderer builds a raw-data form, which
        clones the request and re-enters the view — *during* rendering, after
        handle_exception has already turned the failure into a 502. Re-raising
        there escapes rendering and Django reports a 500 instead. (A GET-only
        route never builds that form, so it does not exercise this.)"""
        TrialFactory(disease='Multiple Myeloma')
        with patch(
            'trials.services.patient_info.promop_client.PromopClient.fetch_patient',
            return_value=None,
        ) as mock_fetch:
            resp = authed_client.post(
                '/trials/match/', {'person_id': 9001}, format='json',
                HTTP_ACCEPT=accept,
            )
        assert resp.status_code == 502
        assert mock_fetch.call_count == 1

    @override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=True)
    def test_no_person_id_is_still_a_patientless_search(self, authed_client):
        """The complement: the 502 above is about a patient who was *named*.
        A request that names nobody is public browsing and still answers 200."""
        TrialFactory(disease='Multiple Myeloma')
        resp = authed_client.get('/trials/')
        assert resp.status_code == 200

    @override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=False)
    def test_inline_match_still_works_when_person_id_gate_off(self, authed_client):
        # The gate must not affect the inline path the real host uses.
        TrialFactory(disease='Multiple Myeloma')
        resp = authed_client.post(
            '/trials/match/', {'patient_info': {'disease': 'multiple myeloma'}}, format='json'
        )
        assert resp.status_code == 200
