"""Tests for resolve_patient_info's CTOMOP `?person_id=` path (#102).

Resolution order under test:
1. Inline `patient_info` payload wins if both are present (lets callers
   stage the migration without breaking CB).
2. `person_id` (query param or body) → CtomopClient fetch → adapter.
3. Neither → None.
"""
from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings
from rest_framework.exceptions import PermissionDenied

from trials.services.patient_info.resolve import resolve_patient_info


def _mock_request(data=None, query_params=None):
    req = MagicMock()
    req.data = data if data is not None else {}
    req.query_params = query_params if query_params is not None else {}
    return req


class TestResolvePatientInfoDispatch:
    def test_no_payload_no_person_id_returns_none(self):
        assert resolve_patient_info(_mock_request()) is None

    def test_empty_dict_data_returns_none(self):
        assert resolve_patient_info(_mock_request(data={})) is None

    def test_non_dict_data_returns_none(self):
        """`request.data` can be a string / list under some DRF configs — must not crash."""
        req = MagicMock()
        req.data = 'not a dict'
        req.query_params = {}
        assert resolve_patient_info(req) is None

    def test_query_param_person_id_routes_to_ctomop(self):
        req = _mock_request(query_params={'person_id': '9001'})
        with patch(
            'trials.services.patient_info.ctomop_client.CtomopClient', autospec=True,
        ) as MockClient, patch(
            'trials.services.patient_info.ctomop_adapter.build_patient_info_from_ctomop_row',
        ) as mock_build:
            MockClient.return_value.fetch_patient.return_value = {'person_id': 9001}
            mock_build.return_value = 'pi_object'

            result = resolve_patient_info(req)

        MockClient.return_value.fetch_patient.assert_called_once_with('9001')
        mock_build.assert_called_once_with({'person_id': 9001})
        assert result == 'pi_object'

    def test_camelcase_personid_query_param_also_routes(self):
        req = _mock_request(query_params={'personId': '9002'})
        with patch(
            'trials.services.patient_info.ctomop_client.CtomopClient', autospec=True,
        ) as MockClient, patch(
            'trials.services.patient_info.ctomop_adapter.build_patient_info_from_ctomop_row',
        ) as mock_build:
            MockClient.return_value.fetch_patient.return_value = {'person_id': 9002}
            mock_build.return_value = 'pi_object'
            resolve_patient_info(req)
        MockClient.return_value.fetch_patient.assert_called_once_with('9002')

    def test_body_person_id_routes_to_ctomop(self):
        req = _mock_request(data={'person_id': 9003})
        with patch(
            'trials.services.patient_info.ctomop_client.CtomopClient', autospec=True,
        ) as MockClient, patch(
            'trials.services.patient_info.ctomop_adapter.build_patient_info_from_ctomop_row',
        ) as mock_build:
            MockClient.return_value.fetch_patient.return_value = {'person_id': 9003}
            mock_build.return_value = 'pi'
            resolve_patient_info(req)
        MockClient.return_value.fetch_patient.assert_called_once_with(9003)

    def test_inline_patient_info_wins_over_person_id(self):
        """Resolution order: inline payload first. Lets callers send both
        during a migration period without surprising flips.
        """
        req = _mock_request(
            data={'patient_info': {'disease': 'multiple myeloma'}, 'person_id': 9001},
        )
        with patch(
            'trials.services.patient_info.resolve._build_in_memory',
        ) as mock_inline, patch(
            'trials.services.patient_info.ctomop_client.CtomopClient', autospec=True,
        ) as MockClient:
            mock_inline.return_value = 'inline_pi'

            result = resolve_patient_info(req)

        mock_inline.assert_called_once_with({'disease': 'multiple myeloma'})
        MockClient.return_value.fetch_patient.assert_not_called()
        assert result == 'inline_pi'

    @pytest.mark.django_db
    def test_enveloped_fetch_survives_into_patient_info(self):
        """End-to-end regression for #144: an enveloped HTTP body
        (`{"patient_info": {...}}`) fetched via CtomopClient and run through the
        adapter must yield a PatientInfo carrying the patient's real disease /
        age / gender — NOT the silent myeloma defaults that result when the
        envelope is left un-unwrapped and every field is filtered away.
        """
        from unittest.mock import MagicMock
        from trials.services.patient_info.ctomop_client import CtomopClient
        from trials.services.patient_info.ctomop_adapter import (
            build_patient_info_from_ctomop_row,
        )

        resp = MagicMock()
        resp.ok = True
        resp.status_code = 200
        resp.reason = 'OK'
        resp.json.return_value = {
            'patient_info': {
                'person_id': 9005,
                'disease': 'breast cancer',
                'patient_age': 51,
                'gender': 'F',
            },
        }
        client = CtomopClient(base_url='https://ctomop.example.com', token='tk')
        with patch('trials.services.patient_info.ctomop_client.requests.get',
                   return_value=resp):
            row = client.fetch_patient(9005)

        pi = build_patient_info_from_ctomop_row(row)

        assert pi.disease == 'breast cancer'   # not the 'multiple myeloma' default
        assert pi.patient_age == 51            # not None
        assert pi.gender == 'F'                # not None

    def test_ctomop_client_returns_none_propagates(self):
        """Client failure (network, 4xx/5xx, malformed JSON) → resolver returns None."""
        req = _mock_request(query_params={'person_id': '9001'})
        with patch(
            'trials.services.patient_info.ctomop_client.CtomopClient', autospec=True,
        ) as MockClient, patch(
            'trials.services.patient_info.ctomop_adapter.build_patient_info_from_ctomop_row',
        ) as mock_build:
            MockClient.return_value.fetch_patient.return_value = None
            assert resolve_patient_info(req) is None
            # Adapter not invoked on missing row.
            mock_build.assert_not_called()


class TestPersonIdLookupGate:
    """The `?person_id=` path is an IDOR (CTOMOP service token isn't bound to
    the caller). It's gated behind EXACT_ALLOW_PERSON_ID_LOOKUP — off in prod.
    When off, a person_id request is rejected (403) rather than silently
    ignored; the inline path is unaffected. (#150/#108)"""

    @override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=False)
    def test_query_param_person_id_rejected_when_gate_off(self):
        req = _mock_request(query_params={'person_id': '9001'})
        with patch(
            'trials.services.patient_info.ctomop_client.CtomopClient', autospec=True,
        ) as MockClient:
            with pytest.raises(PermissionDenied):
                resolve_patient_info(req)
            MockClient.return_value.fetch_patient.assert_not_called()

    @override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=False)
    def test_body_person_id_rejected_when_gate_off(self):
        req = _mock_request(data={'person_id': 9003})
        with pytest.raises(PermissionDenied):
            resolve_patient_info(req)

    @override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=False)
    def test_inline_payload_unaffected_when_gate_off(self):
        req = _mock_request(data={'patient_info': {'disease': 'multiple myeloma'}})
        with patch('trials.services.patient_info.resolve._build_in_memory') as mock_inline:
            mock_inline.return_value = 'inline_pi'
            assert resolve_patient_info(req) == 'inline_pi'

    @override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=False)
    def test_no_patient_context_still_returns_none_when_gate_off(self):
        assert resolve_patient_info(_mock_request()) is None

    @override_settings(EXACT_ALLOW_PERSON_ID_LOOKUP=True)
    def test_person_id_allowed_when_gate_on(self):
        req = _mock_request(query_params={'person_id': '9001'})
        with patch(
            'trials.services.patient_info.ctomop_client.CtomopClient', autospec=True,
        ) as MockClient, patch(
            'trials.services.patient_info.ctomop_adapter.build_patient_info_from_ctomop_row',
        ) as mock_build:
            MockClient.return_value.fetch_patient.return_value = {'person_id': 9001}
            mock_build.return_value = 'pi'
            assert resolve_patient_info(req) == 'pi'
