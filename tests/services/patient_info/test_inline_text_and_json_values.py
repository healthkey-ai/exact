"""Text and JSON values on the inline path are a value or a 400 (#479).

#594 made a malformed number, date or boolean on the inline payload a 400
naming the field. Text and JSON columns were left out, and measured on
`cb-like-trials` they still answered the same mistake three ways:

    disease: 123 / ["multiple myeloma"]  -> 500, `.strip()` in normalize.py:483
    genetic_mutations: "{not json"       -> silently None, the value discarded
    stage: 3                             -> kept as an int on a text column

The fix sits at the boundary (`resolve.py`): `normalize.py` stays
byte-identical to CancerBot. The upstream path (adapter, batch commands) keeps
its lenient behaviour. What a payload may contain in full is #716; which of two
spellings wins is #717.
"""
from unittest.mock import MagicMock

import pytest
from django.db.models import CharField, TextField
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from accounts.models import Identity
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.resolve import (
    _TEXT_FIELDS_TAKING_A_LIST,
    MalformedPatientValue,
    _build_in_memory,
    resolve_patient_info,
)

BASE = {'disease': 'multiple myeloma'}


def inline(payload):
    request = MagicMock()
    request.data = {'patient_info': payload}
    request.query_params = {}
    return resolve_patient_info(request)


def build(extra, strict=True):
    return _build_in_memory({**BASE, **extra}, strict=strict)


@pytest.mark.django_db
class TestTextColumns:
    @pytest.mark.parametrize('value', [123, 1.5, True, {'a': 1}, ['multiple myeloma']])
    def test_a_single_valued_text_column_takes_a_string_or_nothing(self, value):
        # `disease: 123` and `disease: [...]` were a 500 from normalize.
        with pytest.raises(ValidationError) as raised:
            inline({'disease': value})
        assert 'disease' in raised.value.detail

    @pytest.mark.parametrize('value', [3, ['II'], {'s': 'II'}])
    def test_a_number_or_list_on_stage_is_named(self, value):
        with pytest.raises(MalformedPatientValue) as raised:
            build({'stage': value})
        assert raised.value.field_name == 'stage'

    def test_strings_and_null_still_go_through(self):
        pi = build({'stage': 'II', 'tumor_grade': None})
        assert pi.disease == 'multiple myeloma'
        assert pi.stage == 'II'

    def test_a_multi_valued_column_still_takes_a_list_of_strings(self):
        pi = build({'histologic_type': ['ductal', 'lobular']})
        assert pi.histologic_type == ['ductal', 'lobular']

    @pytest.mark.parametrize('value', [[1, 2], [{'a': 1}], 7, {'a': 1}])
    def test_a_multi_valued_column_refuses_anything_else(self, value):
        with pytest.raises(MalformedPatientValue) as raised:
            build({'histologic_type': value})
        assert raised.value.field_name == 'histologic_type'

    @pytest.mark.parametrize('field, extra', [
        ('cytogenic_markers', {}),
        ('molecular_markers', {}),
        ('first_line_therapy', {'prior_therapy': 'One line'}),
        ('second_line_therapy', {'prior_therapy': 'Two lines'}),
        ('later_therapy', {'prior_therapy': 'More than two lines of therapy'}),
    ])
    def test_a_list_where_the_readers_crash_on_one_is_named(self, field, extra):
        # These are read as several values too, but a list in them already
        # crashed the build (`.split` / a list in a set lookup): 500 -> 400.
        with pytest.raises(MalformedPatientValue) as raised:
            build({field: ['x', 'y'], **extra})
        assert raised.value.field_name == field

    def test_nan_on_a_text_column_is_named_before_the_numeric_scrub(self):
        # `_coerce_numerics` turns every non-finite float into None, on every
        # column; checking text first is what lets this be named at all.
        with pytest.raises(MalformedPatientValue) as raised:
            build({'stage': float('nan')})
        assert raised.value.field_name == 'stage'

    def test_the_upstream_path_is_unchanged(self):
        # Adapter and batch commands: not the reader's value, so no 400 here.
        # Pins that this change left the lenient path alone, not that an int
        # on a text column is right (#716).
        pi = build({'stage': 3}, strict=False)
        assert pi.stage == 3

    def test_it_names_the_key_the_caller_wrote(self):
        with pytest.raises(ValidationError) as raised:
            inline({'disease': 'multiple myeloma', 'histologicType': {'a': 1}})
        assert 'histologicType' in raised.value.detail

    def test_a_camel_case_m2m_list_still_goes_through(self):
        # `concomitantMedications` misses the snake_case M2M pop and reaches
        # the text check; it used to pass and still does (#717 decides more).
        build({'concomitantMedications': ['1', '2']})

    def test_a_normalized_promop_row_posted_back_inline_passes(self):
        # The federated host posts `/normalize-promop-row/`'s answer back as
        # the inline payload; the strict check must not 400 that round trip.
        from trials.services.patient_info.promop_adapter import normalize_promop_row
        row = normalize_promop_row({
            'disease': 'multiple myeloma',
            'gender': 'F',
            'patient_age': 64,
            'stage': 'II',
            # Therapy-line codes need the patient vocabulary loaded, which the
            # test DB does not seed; the lines' own shape is pinned above.
            'prior_therapy': 'None',
            'supportive_therapies': 'Zoledronic acid; Denosumab',
            'genetic_mutations': [],
            'english_speak': True,
            'english_read': True,
        })
        build(row)

    def test_the_list_set_names_only_real_text_columns(self):
        text_columns = {
            f.name for f in PatientInfo._meta.get_fields()
            if hasattr(f, 'column') and isinstance(f, (TextField, CharField))
        }
        assert _TEXT_FIELDS_TAKING_A_LIST <= text_columns


@pytest.mark.django_db
class TestJsonColumns:
    def test_a_string_that_is_not_json_is_named(self):
        # It used to become None: the caller's genetics silently dropped.
        with pytest.raises(ValidationError) as raised:
            inline({**BASE, 'genetic_mutations': '{not json'})
        assert 'genetic_mutations' in raised.value.detail

    @pytest.mark.parametrize('value', [
        '[{"gene": "TP53"}]',        # JSON
        "[{'gene': 'TP53'}]",        # Python repr, which CB stores
        [{'gene': 'TP53'}],          # already decoded
    ])
    def test_the_encodings_cb_sends_still_decode(self, value):
        pi = build({'genetic_mutations': value})
        assert isinstance(pi.genetic_mutations, list)

    @pytest.mark.parametrize('value', ['', '   '])
    def test_a_blank_string_is_still_absent(self, value):
        pi = build({'genetic_mutations': value})
        assert not pi.genetic_mutations

    def test_a_deeply_nested_string_is_a_400_not_a_500(self):
        with pytest.raises(MalformedPatientValue) as raised:
            build({'genetic_mutations': '[' * 100000})
        assert raised.value.field_name == 'genetic_mutations'

    def test_the_upstream_path_still_drops_it(self):
        pi = build({'genetic_mutations': '{not json'}, strict=False)
        assert not pi.genetic_mutations


@pytest.fixture
def authed_client(db):
    user, _ = Identity.objects.get_or_create(issuer='urn:local', sub='inline-479-tester')
    token, _ = Token.objects.get_or_create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return client


@pytest.mark.django_db
def test_over_http_it_is_a_400_naming_the_key_not_a_500(authed_client):
    response = authed_client.post(
        '/trials/match/', {'patient_info': {'disease': 123}}, format='json',
    )
    assert response.status_code == 400, response.content[:300]
    assert 'disease' in response.json()
