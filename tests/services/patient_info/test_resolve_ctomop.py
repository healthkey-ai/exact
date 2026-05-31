"""Tests for resolve_patient_info's CTOMOP `?person_id=` path (#102).

Resolution order under test:
1. Inline `patient_info` payload wins if both are present (lets callers
   stage the migration without breaking CB).
2. `person_id` (query param or body) → CtomopClient fetch → adapter.
3. Neither → None.
"""
from unittest.mock import MagicMock, patch

import pytest

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
