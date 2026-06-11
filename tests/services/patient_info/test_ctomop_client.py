"""Tests for CtomopClient — the thin HTTP layer to CTOMOP's patient endpoint (#102).

All scenarios verify the same contract: `fetch_patient` either returns
the parsed JSON dict or `None`. It must never raise — the resolver
treats failures the same as a missing payload.
"""
from unittest.mock import MagicMock, patch

import pytest
import requests

from trials.services.patient_info.ctomop_client import CtomopClient


def _ok_response(json_data, status_code=200):
    resp = MagicMock()
    resp.ok = (200 <= status_code < 300)
    resp.status_code = status_code
    resp.reason = 'OK' if resp.ok else 'Bad'
    resp.json.return_value = json_data
    return resp


def _bad_json_response(status_code=200):
    resp = MagicMock()
    resp.ok = True
    resp.status_code = status_code
    resp.reason = 'OK'
    resp.json.side_effect = ValueError('not json')
    return resp


class TestCtomopClientFetch:
    def test_returns_dict_on_200(self):
        client = CtomopClient(base_url='https://ctomop.example.com/', token='tk')
        with patch('trials.services.patient_info.ctomop_client.requests.get',
                   return_value=_ok_response({'person_id': 9001, 'disease': 'MM'})) as mock_get:
            result = client.fetch_patient(9001)
        assert result == {'person_id': 9001, 'disease': 'MM'}
        # Trailing slash on base_url should be stripped.
        call_args = mock_get.call_args
        assert call_args.args[0] == 'https://ctomop.example.com/api/patient-info/9001/'
        # Bearer auth attached.
        assert call_args.kwargs['headers']['Authorization'] == 'Bearer tk'
        assert call_args.kwargs['headers']['Accept'] == 'application/json'

    def test_returns_none_when_base_unset(self):
        client = CtomopClient(base_url='', token='tk')
        with patch('trials.services.patient_info.ctomop_client.requests.get') as mock_get:
            assert client.fetch_patient(9001) is None
        # Network must not be touched when not configured.
        mock_get.assert_not_called()

    def test_returns_none_on_network_error(self):
        client = CtomopClient(base_url='https://ctomop.example.com', token='tk')
        with patch('trials.services.patient_info.ctomop_client.requests.get',
                   side_effect=requests.ConnectionError('boom')):
            assert client.fetch_patient(9001) is None

    def test_returns_none_on_timeout(self):
        client = CtomopClient(base_url='https://ctomop.example.com', token='tk')
        with patch('trials.services.patient_info.ctomop_client.requests.get',
                   side_effect=requests.Timeout('slow')):
            assert client.fetch_patient(9001) is None

    @pytest.mark.parametrize('status', [400, 401, 403, 404, 500, 503])
    def test_returns_none_on_non_ok_status(self, status):
        client = CtomopClient(base_url='https://ctomop.example.com', token='tk')
        with patch('trials.services.patient_info.ctomop_client.requests.get',
                   return_value=_ok_response({}, status_code=status)):
            assert client.fetch_patient(9001) is None

    def test_returns_none_on_malformed_json(self):
        client = CtomopClient(base_url='https://ctomop.example.com', token='tk')
        with patch('trials.services.patient_info.ctomop_client.requests.get',
                   return_value=_bad_json_response()):
            assert client.fetch_patient(9001) is None

    def test_returns_none_on_non_dict_body(self):
        """Lists, arrays, primitives — anything non-dict is not what the adapter expects."""
        client = CtomopClient(base_url='https://ctomop.example.com', token='tk')
        with patch('trials.services.patient_info.ctomop_client.requests.get',
                   return_value=_ok_response([{'foo': 'bar'}])):
            assert client.fetch_patient(9001) is None

    def test_no_auth_header_when_token_unset(self):
        client = CtomopClient(base_url='https://ctomop.example.com', token='')
        with patch('trials.services.patient_info.ctomop_client.requests.get',
                   return_value=_ok_response({'person_id': 9001})) as mock_get:
            client.fetch_patient(9001)
        assert 'Authorization' not in mock_get.call_args.kwargs['headers']

    @pytest.mark.parametrize('bad_id', [
        '../evil-endpoint',     # path traversal — would leak Bearer token
        'abc',                  # non-numeric
        '1; DROP TABLE persons',
        '1/../../admin',
        '',                     # empty
        None,                   # null
        0,                      # non-positive integer
        -1,
    ])
    def test_rejects_non_positive_integer_person_id(self, bad_id):
        """URL path injection / Bearer token leakage guard (#102 self-review):
        any person_id that isn't a positive integer is rejected without a
        network call. Important because we'd otherwise interpolate the raw
        value into the URL path and the static service token in the
        Authorization header would leak to whatever path the attacker
        crafted.
        """
        client = CtomopClient(base_url='https://ctomop.example.com', token='tk')
        with patch('trials.services.patient_info.ctomop_client.requests.get') as mock_get:
            result = client.fetch_patient(bad_id)
        assert result is None
        mock_get.assert_not_called()

    def test_accepts_string_form_of_integer(self):
        """Query-param ids arrive as strings; the validator must accept them."""
        client = CtomopClient(base_url='https://ctomop.example.com', token='tk')
        with patch(
            'trials.services.patient_info.ctomop_client.requests.get',
            return_value=_ok_response({'person_id': 9001}),
        ) as mock_get:
            assert client.fetch_patient('9001') == {'person_id': 9001}
        # URL has the validated integer, not the raw string.
        assert mock_get.call_args.args[0] == 'https://ctomop.example.com/api/patient-info/9001/'

    def test_unwraps_patient_info_envelope(self):
        """The HTTP endpoint wraps the row in `{"patient_info": {...}}`; the
        adapter expects a flat row. `fetch_patient` must unwrap so real fields
        aren't nested one level too deep and silently dropped (#144).
        """
        client = CtomopClient(base_url='https://ctomop.example.com', token='tk')
        envelope = {'patient_info': {'person_id': 9005, 'disease': 'breast cancer',
                                     'patient_age': 51, 'gender': 'F'}}
        with patch('trials.services.patient_info.ctomop_client.requests.get',
                   return_value=_ok_response(envelope)):
            result = client.fetch_patient(9005)
        assert result == {'person_id': 9005, 'disease': 'breast cancer',
                          'patient_age': 51, 'gender': 'F'}

    def test_flat_row_passes_through_unchanged(self):
        """Invariant: an already-flat row (the psql management-command shape)
        must pass through untouched — no double-unwrap, no mangling (#144).
        """
        client = CtomopClient(base_url='https://ctomop.example.com', token='tk')
        flat = {'person_id': 9005, 'disease': 'breast cancer',
                'patient_age': 51, 'gender': 'F'}
        with patch('trials.services.patient_info.ctomop_client.requests.get',
                   return_value=_ok_response(flat)):
            result = client.fetch_patient(9005)
        assert result == flat

    def test_envelope_with_sibling_keys_not_unwrapped(self):
        """Only a sole `patient_info` key is the envelope. A flat row that
        happens to carry a nested `patient_info` alongside other columns is
        ambiguous — pass it through rather than guess (#144).
        """
        client = CtomopClient(base_url='https://ctomop.example.com', token='tk')
        body = {'patient_info': {'disease': 'breast cancer'}, 'person_id': 9005}
        with patch('trials.services.patient_info.ctomop_client.requests.get',
                   return_value=_ok_response(body)):
            result = client.fetch_patient(9005)
        assert result == body

    def test_uses_django_settings_when_no_explicit_args(self, settings):
        """Constructor falls back to CTOMOP_BASE / CTOMOP_SERVICE_TOKEN settings."""
        settings.CTOMOP_BASE = 'https://settings.example.com'
        settings.CTOMOP_SERVICE_TOKEN = 'settingstoken'
        client = CtomopClient()
        with patch('trials.services.patient_info.ctomop_client.requests.get',
                   return_value=_ok_response({'person_id': 9001})) as mock_get:
            client.fetch_patient(9001)
        call_args = mock_get.call_args
        assert call_args.args[0].startswith('https://settings.example.com/')
        assert call_args.kwargs['headers']['Authorization'] == 'Bearer settingstoken'
