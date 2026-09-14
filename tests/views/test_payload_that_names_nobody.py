"""A request that meant to send a patient and was not understood says so.

Two defects, one shape. Both answered 200 and neither said anything.

* `{"patient_info": {"diseas": "myeloma"}}` — one letter out — passed the
  non-empty gate, emptied at the known-field filter, and became a BLANK
  `PatientInfo`. Not `None`, so every downstream "is there a patient?" check
  passed; the matcher found nothing standing in the way, which is what a blank
  patient means; and the answer was `eligible`, score 100, for every trial
  (#466).

* `{"patientInfo": {...}}` — the spelling `docs/api.md` documents and
  always has — was read under a key nothing parsed, so the documented request
  ran the matcher with no patient and returned the unfiltered catalog (#375).

The two want opposite-looking fixes and the same rule: a caller who sent the
key meant to describe a patient, so understanding none of it is an error;
sending no key at all is a perfectly good request and still is.
"""
import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from tests.factories import TrialFactory


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='payload-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


@pytest.fixture
def corpus(db):
    TrialFactory(disease='multiple myeloma')
    TrialFactory(disease='breast cancer')


def _post(client, body, path='/trials/search/match/'):
    return client.post(path, body, format='json')


class _request:
    """The smallest thing `resolve_patient_info` reads: `.data`, and no
    `.query_params`."""

    def __init__(self, data):
        self.data = data


@pytest.mark.django_db
class TestAPayloadThatNamesNobody:
    def test_it_is_refused_rather_than_answered(self, authed_client, corpus):
        response = _post(authed_client, {'patient_info': {'foo': 'bar'}})
        assert response.status_code == 400, (
            f'answered {response.status_code}; a payload EXACT understands no '
            'part of used to become a blank patient who qualifies for '
            'everything'
        )

    def test_the_message_names_the_keys_nobody_recognised(self, authed_client, corpus):
        """"Invalid patient_info" sends the reader back to guess which of
        forty fields was wrong."""
        response = _post(authed_client, {'patient_info': {'diseas': 'myeloma', 'agee': 60}})
        body = str(response.data)
        assert 'diseas' in body and 'agee' in body

    def test_one_recognised_field_is_enough(self, authed_client, corpus):
        """The rule is "understood nothing", not "understood everything" — a
        payload carrying one known field alongside unknown ones is a patient,
        and a client sending a field EXACT has not heard of yet must not be
        refused because of it."""
        response = _post(
            authed_client,
            {'patient_info': {'disease': 'multiple myeloma', 'not_a_field': 1}},
        )
        assert response.status_code == 200
        assert response.data['itemsTotalCount'] == 1

    def test_an_empty_object_still_means_no_patient(self, authed_client, corpus):
        """It carries the same amount of information and is NOT an error: the
        caller said "I have no patient", which is a supported search."""
        response = _post(authed_client, {'patient_info': {}})
        assert response.status_code == 200
        assert response.data['itemsTotalCount'] == 2

    def test_and_so_does_sending_no_key_at_all(self, authed_client, corpus):
        response = _post(authed_client, {})
        assert response.status_code == 200
        assert response.data['itemsTotalCount'] == 2

    def test_the_m2m_only_payload_is_a_patient(self, authed_client, corpus):
        """`pre_existing_condition_categories` is popped before the known-field
        filter, so a payload carrying only it recognises nothing by that test
        and still names a real patient."""
        response = _post(
            authed_client, {'patient_info': {'pre_existing_condition_categories': []}}
        )
        assert response.status_code == 200, (
            'an M2M-only payload was refused. It is popped before the '
            'known-field filter, so it looks empty by that test — and an '
            'empty list is the statement "none of these", not silence.'
        )

    def test_the_detail_endpoint_refuses_it_too(self, authed_client, corpus):
        """Same resolver, same answer — a defect fixed on one endpoint and not
        the other is the disagreement #456 was about."""
        trial = TrialFactory(disease='multiple myeloma')
        response = _post(
            authed_client, {'patient_info': {'foo': 'bar'}}, f'/trials/{trial.id}/match/'
        )
        assert response.status_code == 400


@pytest.mark.django_db
class TestTheSpellingTheDocsPublish:
    def test_camel_case_reaches_the_matcher(self, authed_client, corpus):
        """`docs/api.md` documents `patientInfo` and always has. Unread, it
        was not an error — so the documented request searched with no patient
        and returned everything."""
        response = _post(authed_client, {'patientInfo': {'disease': 'multiple myeloma'}})
        assert response.status_code == 200
        assert response.data['itemsTotalCount'] == 1, (
            'the documented spelling still runs the matcher with no patient'
        )

    def test_snake_case_still_wins_when_both_are_sent(self, authed_client, corpus):
        """Not a merge: one of them is the payload. The existing contract is
        the snake_case one, so it decides."""
        response = _post(
            authed_client,
            {
                'patient_info': {'disease': 'multiple myeloma'},
                'patientInfo': {'disease': 'breast cancer'},
            },
        )
        assert response.status_code == 200
        assert response.data['itemsTotalCount'] == 1
        assert response.data['results'][0]['disease'].lower() == 'multiple myeloma'

    def test_an_unrecognised_camel_payload_is_refused_under_its_own_name(
        self, authed_client, corpus,
    ):
        response = _post(authed_client, {'patientInfo': {'foo': 'bar'}})
        assert response.status_code == 400
        assert 'patientInfo' in str(response.data)


@pytest.mark.django_db
class TestNamesButNoValues:
    """The third state, and the reason two are not enough.

    A form-backed client serialising every field it knows — most of them
    `null`, because the patient has not answered yet — read the contract
    correctly and has nothing to say. Answering that 400 would be wrong, and
    answering it with a blank patient who qualifies for everything is the
    defect. It is a search without a patient, which is a thing EXACT supports.
    """

    def test_recognised_names_with_no_values_mean_no_patient(self, authed_client, corpus):
        response = _post(
            authed_client,
            {'patient_info': {'disease': None, 'patient_age': None, 'gender': ''}},
        )
        assert response.status_code == 200
        assert response.data['itemsTotalCount'] == 2, 'it narrowed as if there were a patient'
        assert {r['matchingType'] for r in response.data['results']} == {None}, (
            'a verdict was reported about a patient nobody described'
        )

    def test_an_m2m_key_with_no_value_is_the_same(self, authed_client, corpus):
        response = _post(authed_client, {'patient_info': {'pre_existing_condition_categories': None}})
        assert response.status_code == 200
        assert {r['matchingType'] for r in response.data['results']} == {None}

    def test_but_an_empty_list_is_a_statement(self, authed_client, corpus):
        """`[]` says "none of these", which is a fact about a patient. It is
        not the same as `null`, and the difference decides whether a criterion
        is judged or left unknown."""
        response = _post(
            authed_client,
            {'patient_info': {'disease': 'multiple myeloma',
                              'pre_existing_condition_categories': []}},
        )
        assert response.status_code == 200
        assert response.data['itemsTotalCount'] == 1

    def test_a_single_real_value_is_a_patient(self, authed_client, corpus):
        """Non-vacuity: a rule that answered "no patient" too eagerly would
        pass everything above."""
        response = _post(authed_client, {'patient_info': {'disease': 'multiple myeloma'}})
        assert response.data['itemsTotalCount'] == 1
        assert {r['matchingType'] for r in response.data['results']} == {'eligible'}


@pytest.mark.django_db
class TestTheMessageIsUsableAndBounded:
    def test_it_quotes_the_spelling_the_caller_typed(self, authed_client, corpus):
        """Listing the snake_cased forms undoes the point of listing them:
        `patientAgee` came back as `patient_agee`, a string the caller never
        wrote, sending them looking for a field that does not exist under
        either spelling."""
        response = _post(authed_client, {'patient_info': {'patientAgee': 60}})
        assert response.status_code == 400
        assert 'patientAgee' in str(response.data)
        assert 'patient_agee' not in str(response.data)

    def test_it_does_not_reflect_unbounded_input(self, authed_client, corpus):
        """`pagination.py` made this call two files away for `?limit=` — "don't
        echo unbounded user input back in the error body". Same shape here: a
        payload with thousands of junk keys produced a 69 kB response, and one
        very long key a 200 kB one."""
        response = _post(
            authed_client, {'patient_info': {f'junk_{i}': 1 for i in range(5000)}}
        )
        assert response.status_code == 400
        assert len(str(response.data)) < 2000, len(str(response.data))

    def test_a_single_enormous_key_is_clipped(self, authed_client, corpus):
        response = _post(authed_client, {'patient_info': {'k' * 200_000: 1}})
        assert response.status_code == 400
        assert len(str(response.data)) < 2000, len(str(response.data))

    def test_it_still_says_how_many_were_dropped(self, authed_client, corpus):
        response = _post(
            authed_client, {'patient_info': {f'junk_{i}': 1 for i in range(50)}}
        )
        assert 'more' in str(response.data)


@pytest.mark.django_db
class TestCamelCaseInnerKeys:
    """The docs promise inner keys work in either spelling. For the two M2M
    keys that was false: they were popped and recognised BEFORE the snake_case
    conversion, so the documented spelling was refused — and its value
    silently dropped in a mixed payload. EXACT's own serializer emits this key
    camelCased, so a client echoing the projection back used the form that
    failed."""

    def test_the_camel_m2m_key_is_recognised(self, authed_client, corpus):
        response = _post(
            authed_client, {'patient_info': {'preExistingConditionCategories': []}}
        )
        assert response.status_code == 200

    def test_and_its_value_actually_arrives(self):
        """The half a status code cannot see, and the half that mattered.

        The first version of this fix made the camel spelling stop being
        REFUSED and left it being dropped: the pops name the snake_case keys
        and ran before the conversion, so the value vanished with a 200. A
        test asserting only the status passed against exactly that.
        """
        from trials.models import PreExistingConditionCategory
        from trials.services.patient_info.resolve import _build_in_memory

        category = PreExistingConditionCategory.objects.create(title='Cardiac Issues')

        from_camel = _build_in_memory({'preExistingConditionCategories': [category.id]})
        from_snake = _build_in_memory({'pre_existing_condition_categories': [category.id]})
        camel_ids = [c.id for c in from_camel._pre_existing_condition_categories]
        snake_ids = [c.id for c in from_snake._pre_existing_condition_categories]
        assert camel_ids == [category.id], (
            'the camelCase spelling arrived as nothing; its value is being '
            'dropped between the pop and the field filter'
        )
        assert camel_ids == snake_ids

    def test_a_camelcase_inner_key_reaches_the_column(self):
        """An INNER key, end to end, for a scalar field.

        The version this replaces varied only the OUTER key and duplicated a
        test two classes up — which is why the `concomitantMedications`
        regression was invisible to the suite: the value was being dropped
        between the pop and the field filter, and nothing asserted a
        camelCased inner key ever arrived.
        """
        from trials.services.patient_info.resolve import _build_in_memory

        camel = _build_in_memory({'concomitantMedications': 'Warfarin'})
        snake = _build_in_memory({'concomitant_medications': 'Warfarin'})
        assert camel.concomitant_medications == 'Warfarin'
        assert camel.concomitant_medications == snake.concomitant_medications


@pytest.mark.django_db
class TestTheSharedBuilderIsNotAffected:
    """`_build_in_memory` is called by the CTOMOP adapter and by
    `explain_trial_match` as well as by a request. A sparse CTOMOP row must not
    answer its caller 400 about a `patient_info` key their request never
    contained — and must not bypass the view's deliberate choice to let a
    `person_id` failure surface as a 500 rather than be masked as a 400."""

    def test_a_sparse_ctomop_row_still_builds(self):
        from trials.services.patient_info.ctomop_adapter import (
            build_patient_info_from_ctomop_row,
        )

        patient = build_patient_info_from_ctomop_row({'person_id': 9003})
        assert patient is not None

    def test_the_builder_takes_an_unrecognised_dict_without_raising(self):
        from trials.services.patient_info.resolve import _build_in_memory

        assert _build_in_memory({'not_a_field': 1}) is not None


@pytest.mark.django_db
class TestRecognisedMeansWeWillActuallyReadIt:
    """The set has to be exactly what `_build_in_memory` keeps, and being wrong
    either way puts the defect back.

    Too narrow and a field EXACT does use is answered 400. Too wide and a
    payload naming only something we DROP passes the check and produces the
    blank patient again, one step further along — which is what happened when
    the set was built from every attribute on the instance.
    """

    def test_every_recognised_name_survives_the_filter(self):
        """Behaviourally, across every recognised name, not by recomputing the
        implementation's own expression.

        An earlier version asserted `_known_attribute_names()` equalled the
        same set comprehension the function uses — which passes against any
        implementation computing the same thing, including a wrong one. This
        sends a value of the right shape for each field and checks it arrives:
        the property that matters, and the one that would have caught
        `concomitant_medications` being popped into an attribute nobody reads.
        """
        import datetime

        from trials.services.patient_info.patient_info import PatientInfo
        from trials.services.patient_info.resolve import (
            M2M_PAYLOAD_KEYS,
            _build_in_memory,
            _known_attribute_names,
        )

        by_type = {
            'BooleanField': True, 'IntegerField': 1, 'BigIntegerField': 1,
            'SmallIntegerField': 1, 'PositiveIntegerField': 1,
            'FloatField': 1.5, 'DecimalField': 1.5, 'JSONField': [],
            'DateField': datetime.date(2020, 1, 1),
            'DateTimeField': datetime.datetime(2020, 1, 1),
        }
        from trials.services.patient_info.normalize import RECOMPUTED_ATTRIBUTES

        fields = {f.name: f for f in PatientInfo._meta.get_fields()}
        names = _known_attribute_names()
        assert len(names) > 50, 'the set collapsed; this assertion is vacuous'

        # The recomputed set is skipped, not because it is inconvenient but
        # because `normalize_patient_info` deliberately overwrites those from
        # the inputs it was given (#449) — a sent value not surviving them is
        # the documented contract, not a loss. The register is the same one
        # `uoverwritten` is built from, so this cannot drift from it.
        lost, checked = [], 0
        for name in sorted(names):
            if name in M2M_PAYLOAD_KEYS or name == 'id':
                continue
            if name in RECOMPUTED_ATTRIBUTES:
                continue
            field = fields.get(name)
            if field is None:
                lost.append(f'{name} (not a field at all)')
                continue
            value = by_type.get(type(field).__name__, 'x')
            try:
                patient = _build_in_memory({name: value})
            except Exception as exc:
                raise AssertionError(f'{name}: {type(exc).__name__}: {exc}') from exc
            checked += 1
            if getattr(patient, name, None) != value:
                lost.append(name)

        assert checked > 50, checked
        assert not lost, (
            f'recognised but not kept: {lost}. A payload naming only one of '
            'these passes the gate and produces the blank patient again.'
        )

    def test_a_payload_naming_only_something_we_drop_is_refused(self, authed_client, corpus):
        """`geo_point` is an attribute on the instance and is what the distance
        filter reads — but it is not in `_FIELDS`, so a caller-supplied one is
        discarded and EXACT computes its own from the country and postal code.
        A payload naming only it tells us nothing we will use."""
        response = _post(authed_client, {'patient_info': {'geo_point': {'lat': 1, 'lon': 2}}})
        assert response.status_code == 400

    def test_but_the_coordinates_we_do_read_are_a_patient(self, authed_client, corpus):
        """Non-vacuity, and the line between the two: `latitude` and
        `longitude` ARE model fields, and `_normalize_geo_point` builds the
        point from them."""
        response = _post(
            authed_client, {'patient_info': {'latitude': 51.5, 'longitude': -0.1}}
        )
        assert response.status_code == 200


@pytest.mark.django_db
class TestConcomitantMedicationsIsAColumn:
    """It was popped as though it were many-to-many. It is not.

    `PatientInfo.concomitant_medications` is a `TextField` with a column, and
    both the matcher (`_match_concomitant_medications`, via `ctx.value`) and
    the queryset (`_filter_concomitant_medications`, via
    `patient_info.concomitant_medications`) read it AS a column. Nothing
    anywhere reads the `_concomitant_medications` attribute the pop created.

    So the pop only ever deleted the value. Silently, and asymmetrically: the
    snake_case spelling — the one the docs call canonical — was popped before
    the camelCase conversion and lost, while `concomitantMedications` escaped
    the pop by accident and set the column. Moving the conversion earlier made
    both spellings lose it, which is how this surfaced.
    """

    def test_the_value_reaches_the_column(self):
        from trials.services.patient_info.resolve import _build_in_memory

        patient = _build_in_memory({'concomitant_medications': 'Warfarin'})
        assert patient.concomitant_medications == 'Warfarin'

    def test_from_either_spelling(self):
        from trials.services.patient_info.resolve import _build_in_memory

        camel = _build_in_memory({'concomitantMedications': 'Warfarin'})
        snake = _build_in_memory({'concomitant_medications': 'Warfarin'})
        assert camel.concomitant_medications == snake.concomitant_medications == 'Warfarin'

    def test_and_the_matcher_can_see_it(self, authed_client, corpus):
        """Through a request, since the column being set is only interesting
        because something downstream reads it."""
        response = _post(
            authed_client,
            {'patient_info': {'disease': 'multiple myeloma',
                              'concomitant_medications': 'Warfarin'}},
        )
        assert response.status_code == 200
        assert response.data['itemsTotalCount'] == 1


@pytest.mark.django_db
class TestAnEmptyInlineObjectEndsResolution:
    """`{}` under the key is the caller's statement, not their silence.

    Treated as absent, it fell through to the `person_id` branch — so
    `{"patient_info": {}, "person_id": 7}` did a person lookup, contradicting
    both the documented meaning of the empty object and the inline-first
    precedence the resolver's own docstring states.
    """

    def test_it_does_not_fall_through_to_person_id(self):
        """Asserted on the lookup NOT happening, not on the status code.

        Through the API the two behave identically here — the `person_id`
        branch degrades to `None` when the fetch fails, so the request answers
        200 either way and a status assertion is vacuous. I wrote that version
        first and a mutation showed it passing against the old code.
        """
        from unittest.mock import patch

        from trials.services.patient_info.resolve import resolve_patient_info

        request = _request({'patient_info': {}, 'person_id': 7})
        with patch(
            'trials.services.patient_info.resolve._resolve_from_ctomop'
        ) as lookup:
            assert resolve_patient_info(request) is None
        lookup.assert_not_called()

    def test_a_null_payload_is_the_same(self):
        from unittest.mock import patch

        from trials.services.patient_info.resolve import resolve_patient_info

        request = _request({'patient_info': None, 'person_id': 7})
        with patch(
            'trials.services.patient_info.resolve._resolve_from_ctomop'
        ) as lookup:
            assert resolve_patient_info(request) is None
        lookup.assert_not_called()

    def test_but_no_key_at_all_still_reaches_it(self):
        """Non-vacuity, and the line this fix draws: absent is not the same as
        present-and-empty. Without this the two assertions above would hold for
        an implementation that never looked up a person at all."""
        from unittest.mock import patch

        from trials.services.patient_info.resolve import resolve_patient_info

        request = _request({'person_id': 7})
        with patch(
            'trials.services.patient_info.resolve._resolve_from_ctomop'
        ) as lookup:
            lookup.return_value = None
            resolve_patient_info(request)
        lookup.assert_called_once()


@pytest.mark.django_db
class TestTheTwoQuestionsCompose:
    """A recognised-but-empty key must not disarm the refusal.

    The first version asked "is anything recognised?" first, so one such key
    was enough to skip the 400 entirely — and then the all-empty branch
    swallowed the request into "no patient": the whole catalog, 200, nothing
    said. Straight back to #375, through the very state added to be kind to
    form-backed clients.

    And the combination is the likely one, not a curiosity: the client that
    serialises every field it knows, most of them null, is exactly the client
    most likely to carry a misspelling among them.
    """

    def test_an_empty_recognised_key_does_not_excuse_an_unreadable_one(
        self, authed_client, corpus,
    ):
        response = _post(
            authed_client,
            {'patient_info': {'patient_age': None, 'diseas': 'multiple myeloma'}},
        )
        assert response.status_code == 400, (
            f'answered {response.status_code} with '
            f'{response.data.get("itemsTotalCount")} trials — the misspelled '
            'field was forgiven because an empty one sat beside it'
        )

    def test_nor_does_a_whole_null_form_around_one(self, authed_client, corpus):
        response = _post(
            authed_client,
            {'patient_info': {'gender': '', 'disease': None, 'patient_age': None,
                              'stagee': 'III', 'patientAgee': 60}},
        )
        assert response.status_code == 400
        assert 'stagee' in str(response.data)

    def test_but_one_real_value_forgives_an_unknown_key(self, authed_client, corpus):
        """The other direction, and the one that must not become strict: a
        client sending a field EXACT has not heard of yet, beside ones it has,
        described a patient."""
        response = _post(
            authed_client,
            {'patient_info': {'disease': 'multiple myeloma', 'not_a_field_yet': 1}},
        )
        assert response.status_code == 200
        assert response.data['itemsTotalCount'] == 1

    def test_and_a_wholly_recognised_empty_form_is_still_no_patient(
        self, authed_client, corpus,
    ):
        response = _post(
            authed_client, {'patient_info': {'disease': None, 'patient_age': None}}
        )
        assert response.status_code == 200
        assert {r['matchingType'] for r in response.data['results']} == {None}


@pytest.mark.django_db
class TestWhitespaceIsNotAnAnswer:
    def test_a_field_holding_spaces_is_an_empty_field(self, authed_client, corpus):
        """Building a patient whose disease is `'   '` narrows the corpus on
        it, and finds nothing, for a reason nothing on screen explains."""
        response = _post(authed_client, {'patient_info': {'disease': '   '}})
        assert response.status_code == 200
        assert {r['matchingType'] for r in response.data['results']} == {None}

    def test_but_false_and_zero_are_answers(self):
        """Non-vacuity in the direction that matters clinically: `0` is a
        measurement and `False` is a finding. Neither is silence."""
        from trials.services.patient_info.resolve import _says_something

        assert _says_something(False)
        assert _says_something(0)
        assert _says_something([])
        assert not _says_something(None)
        assert not _says_something('')
        assert not _says_something('   ')


@pytest.mark.django_db
class TestPreExistingConditionCategoriesByCode:
    """`docs/api.md:68` publishes codes; the lookup only ever took ids.

    The mismatch was invisible while the camelCase spelling was dropped before
    reaching the lookup — the documented request lost the value in silence.
    Reaching it, codes raised `Field 'id' expected a number but got
    'cardiacIssues'`, which the view turned into a 400 naming neither the key
    nor the reason. `code` is unique on the model, so both readings are
    unambiguous.
    """

    def _category(self, code, title):
        from trials.models import PreExistingConditionCategory

        return PreExistingConditionCategory.objects.create(code=code, title=title)

    def test_by_code_which_is_what_the_docs_show(self):
        from trials.services.patient_info.resolve import _build_in_memory

        category = self._category('cardiacIssues', 'Cardiac Issues')
        patient = _build_in_memory({'preExistingConditionCategories': ['cardiacIssues']})
        assert [c.id for c in patient._pre_existing_condition_categories] == [category.id]

    def test_by_id_which_is_what_worked(self):
        from trials.services.patient_info.resolve import _build_in_memory

        category = self._category('pulmonaryDisease', 'Pulmonary Disease')
        patient = _build_in_memory({'pre_existing_condition_categories': [category.id]})
        assert [c.id for c in patient._pre_existing_condition_categories] == [category.id]

    def test_and_a_mixture(self):
        from trials.services.patient_info.resolve import _build_in_memory

        by_code = self._category('cardiacIssues', 'Cardiac Issues')
        by_id = self._category('pulmonaryDisease', 'Pulmonary Disease')
        patient = _build_in_memory(
            {'preExistingConditionCategories': ['cardiacIssues', by_id.id]}
        )
        assert sorted(c.id for c in patient._pre_existing_condition_categories) == sorted(
            [by_code.id, by_id.id]
        )

    def test_an_unknown_code_is_not_a_crash(self):
        """It resolves to no category, the way an unknown id always did."""
        from trials.services.patient_info.resolve import _build_in_memory

        patient = _build_in_memory({'preExistingConditionCategories': ['notACode']})
        assert patient._pre_existing_condition_categories == []

    def test_a_numeric_string_is_an_id_not_a_code(self):
        """Form encoding turns every value into a string, and `pk__in`
        accepted `"12"` before this change. Reading it as a code would drop it
        silently — the failure this whole file is about."""
        from trials.services.patient_info.resolve import _build_in_memory

        category = self._category('cardiacIssues', 'Cardiac Issues')
        patient = _build_in_memory(
            {'preExistingConditionCategories': [str(category.id)]}
        )
        assert [c.id for c in patient._pre_existing_condition_categories] == [category.id]

    def test_a_boolean_is_not_a_primary_key(self):
        """`bool` is an `int` in Python, so `true` would be looked up as pk 1
        and select whichever category happens to hold it."""
        from trials.services.patient_info.resolve import _build_in_memory

        self._category('cardiacIssues', 'Cardiac Issues')
        patient = _build_in_memory({'preExistingConditionCategories': [True, False]})
        assert patient._pre_existing_condition_categories == []


    def test_the_documented_example_reaches_the_api(self, authed_client, corpus):
        self._category('cardiacIssues', 'Cardiac Issues')
        self._category('pulmonaryDisease', 'Pulmonary Disease')
        response = _post(
            authed_client,
            {'patient_info': {
                'disease': 'multiple myeloma',
                'preExistingConditionCategories': ['cardiacIssues', 'pulmonaryDisease'],
            }},
        )
        assert response.status_code == 200, response.data

@pytest.mark.django_db
class TestBothOuterSpellingsAtOnce:
    def test_a_usable_payload_wins_over_an_empty_one(self, authed_client, corpus):
        """`{"patient_info": null, "patientInfo": {...}}` is a caller sending
        both spellings. Answering "no patient" because the first is empty
        would discard the one they filled in."""
        response = _post(
            authed_client,
            {'patient_info': None, 'patientInfo': {'disease': 'multiple myeloma'}},
        )
        assert response.status_code == 200
        assert response.data['itemsTotalCount'] == 1

    def test_between_two_usable_ones_snake_case_still_decides(self, authed_client, corpus):
        """Non-vacuity for the rule above, and the existing contract."""
        response = _post(
            authed_client,
            {'patient_info': {'disease': 'multiple myeloma'},
             'patientInfo': {'disease': 'breast cancer'}},
        )
        assert response.data['itemsTotalCount'] == 1
        assert response.data['results'][0]['disease'].lower() == 'multiple myeloma'
