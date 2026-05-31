"""HTTP client for CTOMOP's `patient-info` endpoint.

Used by `resolve_patient_info` when a request carries `?person_id=` (or
a body field `person_id`) instead of an inline `patient_info` payload.

Keeps the surface minimal: one method `fetch_patient(person_id)` that
returns the flat row dict (matching the shape `normalize_ctomop_row`
expects) or `None` on any error path (network failure, 4xx/5xx, malformed
JSON, missing settings). Returning `None` rather than raising lets the
resolver treat the failure the same as a missing payload — the caller
can proceed without patient context (e.g. public trial browsing).

`CTOMOP_BASE` and `CTOMOP_SERVICE_TOKEN` come from Django settings (each
read from the corresponding env var with empty-string defaults). Uses
`requests` rather than `httpx` per the original issue spec because
`requests` is already in `requirements.txt`; adding `httpx` solely for
this endpoint isn't justified.
"""
import logging

import requests
from django.conf import settings


logger = logging.getLogger(__name__)


DEFAULT_TIMEOUT_SECONDS = 10


class CtomopClient:
    def __init__(self, base_url: str | None = None, token: str | None = None,
                 timeout: float = DEFAULT_TIMEOUT_SECONDS):
        self.base_url = (base_url if base_url is not None
                         else getattr(settings, 'CTOMOP_BASE', '')).rstrip('/')
        self.token = token if token is not None else getattr(settings, 'CTOMOP_SERVICE_TOKEN', '')
        self.timeout = timeout

    def fetch_patient(self, person_id) -> dict | None:
        """GET {base}/api/patient-info/{person_id}/ and return the JSON row.

        Returns None when:
        - `CTOMOP_BASE` is unset (the endpoint isn't configured).
        - The network call fails (connection error, timeout).
        - The HTTP status is non-2xx.
        - The response body isn't a JSON object.
        Logs at WARNING level so failures surface in Sentry / log
        aggregation without short-circuiting the caller.
        """
        if not self.base_url:
            logger.warning(
                'CtomopClient.fetch_patient called with no CTOMOP_BASE configured; '
                'returning None (person_id=%s)', person_id,
            )
            return None

        url = f'{self.base_url}/api/patient-info/{person_id}/'
        headers = {'Accept': 'application/json'}
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'

        try:
            response = requests.get(url, headers=headers, timeout=self.timeout)
        except requests.RequestException as exc:
            logger.warning('CtomopClient network error for person_id=%s: %s', person_id, exc)
            return None

        if not response.ok:
            logger.warning(
                'CtomopClient non-OK response for person_id=%s: %s %s',
                person_id, response.status_code, response.reason,
            )
            return None

        try:
            data = response.json()
        except ValueError:
            logger.warning('CtomopClient non-JSON body for person_id=%s', person_id)
            return None

        if not isinstance(data, dict):
            logger.warning(
                'CtomopClient response for person_id=%s is %s, expected dict',
                person_id, type(data).__name__,
            )
            return None

        return data
