"""
Tests for TrialsPagination.get_page_size — page-size validation (#33).

DRF's default `PageNumberPagination` silently clamps `?limit=N` requests
to `max_page_size`, hiding abuse and shipping fewer results than the caller
asked for. Our override returns 400 ValidationError instead so resource
exhaustion attempts are explicit.
"""
import pytest
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from trials.api.pagination import TrialsPagination


def _request(**params):
    # Wrap the WSGIRequest in a DRF Request so request.query_params resolves
    # the same way it does inside a view's dispatch() — production code path.
    return Request(APIRequestFactory().get('/trials/', params))


class TestPageSizeValidation:
    def test_no_limit_returns_default_page_size(self):
        assert TrialsPagination().get_page_size(_request()) == 20

    def test_valid_limit_returned_as_is(self):
        assert TrialsPagination().get_page_size(_request(limit='50')) == 50

    def test_limit_equal_to_max_is_accepted(self):
        assert TrialsPagination().get_page_size(_request(limit='200')) == 200

    def test_limit_above_max_raises_validation_error(self):
        with pytest.raises(ValidationError) as exc_info:
            TrialsPagination().get_page_size(_request(limit='1000'))
        # Error payload must surface the requested value and the cap so
        # API consumers can correct their request.
        detail = str(exc_info.value.detail['limit'][0])
        assert '1000' in detail
        assert '200' in detail

    def test_limit_zero_raises_validation_error(self):
        with pytest.raises(ValidationError) as exc_info:
            TrialsPagination().get_page_size(_request(limit='0'))
        assert '>=' in str(exc_info.value.detail['limit'][0])

    def test_negative_limit_raises_validation_error(self):
        with pytest.raises(ValidationError):
            TrialsPagination().get_page_size(_request(limit='-5'))

    def test_non_integer_limit_raises_validation_error(self):
        with pytest.raises(ValidationError) as exc_info:
            TrialsPagination().get_page_size(_request(limit='abc'))
        assert 'integer' in str(exc_info.value.detail['limit'][0])

    def test_empty_string_limit_raises_validation_error(self):
        # ?limit= (no value) → '' → ValueError on int() → 400
        with pytest.raises(ValidationError):
            TrialsPagination().get_page_size(_request(limit=''))

    def test_unbounded_input_not_reflected_in_error_body(self):
        # Defensive: large garbage should produce a fixed-size error, not
        # echo the entire input back (request-bloat amplifier).
        garbage = 'x' * 10000
        with pytest.raises(ValidationError) as exc_info:
            TrialsPagination().get_page_size(_request(limit=garbage))
        assert garbage not in str(exc_info.value.detail['limit'][0])
