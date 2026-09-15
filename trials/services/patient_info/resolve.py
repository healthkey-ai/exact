"""
PatientInfo resolver — supports two contract shapes.

1. Inline payload: `{"patient_info": {...}}` in the request body. No DB
   lookup; PatientInfo is never persisted by this path. CancerBot
   depends on this contract — do not change.
2. PROMOP fetch: `?person_id=` query param or `person_id` in the body.
   Looks up the patient from PROMOP via `PromopClient` and feeds the
   row through `build_patient_info_from_promop_row` (#102). Gated behind
   `EXACT_ALLOW_PERSON_ID_LOOKUP` (off by default outside local/DEBUG) —
   see the authorization boundary below.

The inline path takes precedence — if both `patient_info` and
`person_id` are present, the inline payload wins (lets callers stage
the migration without breaking).

## Authorization boundary

The PROMOP `person_id` path calls PROMOP with EXACT's own service
credential, which authenticates EXACT as a service (`urn:service|exact`)
and is NOT bound to the authenticated caller. PROMOP does not enforce
row-level authz for a service credential — so honoring an arbitrary
`person_id` lets any authenticated caller enumerate other patients' PHI
(IDOR, #150/#108). EXACT also has no model linking users to patients
(it's stateless for patient data — see project memory
`feedback_exact_no_own_db.md`), so there's nothing in-tree to verify
against.

Because no production caller uses this path (the federation host fetches
the patient from PROMOP `/patient-info/me/` under the end-user's own token
and forwards it inline), the path is gated OFF by default outside
local/DEBUG via `EXACT_ALLOW_PERSON_ID_LOOKUP`. A request carrying
`person_id` while the gate is off gets a 403.

Re-enabling it in production requires BOTH:
- a *verified* end-user identity reaching PROMOP: either the caller's own
  bearer forwarded through, or a token exchanged for it — matched to
  PROMOP's audience, token type and scopes. Asserting `actor_iss`/
  `actor_sub` alongside a service credential is NOT one of the options:
  PROMOP rejects unsigned actor claims from a service identity outright
  (#448 / promop #147, #568), and EXACT sends no such field anywhere, AND
- PROMOP enforcing per-user authz (its `PatientUser`/consent models), or
  using the self-scoped `/patient-info/me/` route.

Neither exists today, so the gate stays closed in production: the
service-identity migration does not re-open this path.

Tracked as #150/#108, #448.
"""
import ast
import datetime as dt
import json
import re
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Optional

from django.db.models import DateField, DateTimeField, DecimalField, FloatField, IntegerField, JSONField
from rest_framework.exceptions import APIException, ValidationError

from trials.services.patient_info.normalize import normalize_patient_info

# Distinguishes "no person_id supplied" from a supplied-but-falsy one.
_MISSING = object()
# Bounded on purpose. A person_id is a database primary key, so bigint's range
# is the honest ceiling — and the bound must be applied *before* int(), because
# CPython refuses to convert a string of more than 4300 digits at all
# (ValueError), which would escape the ValidationError below as a 500.
_MAX_PERSON_ID = 9223372036854775807  # PostgreSQL bigint
_DIGITS_ONLY = re.compile(r'[0-9]{1,19}')

if TYPE_CHECKING:
    from trials.services.patient_info.patient_info import PatientInfo


def resolve_patient_info(request) -> Optional['PatientInfo']:
    """
    Build an in-memory PatientInfo instance from the request.

    Resolution order:
      1. Inline `patient_info` payload (existing contract — unchanged).
      2. `person_id` query param or body field — fetch from PROMOP. A
         malformed `person_id` is a 400; a well-formed one whose patient can't
         be fetched raises `PatientContextUnavailable` (502). Neither ever
         degrades into case 3.
      3. Return None — caller may proceed without patient context
         (e.g. public trial browsing).
    """
    patient_info_data = _get_body_field(request, 'patient_info')
    if patient_info_data:
        return _build_in_memory(patient_info_data)

    person_id = _extract_person_id(request)
    if person_id is not _MISSING:
        # IDOR gate (#150/#108): the PROMOP fetch uses EXACT's own service
        # credential, which is not bound to the caller, and PROMOP doesn't
        # enforce row-level authz for a service identity — so honoring an
        # arbitrary person_id leaks other patients' PHI. Off by default outside
        # local/DEBUG; reject rather than silently ignore so the disabled path
        # can't masquerade as a no-patient search.
        from django.conf import settings
        if not getattr(settings, 'EXACT_ALLOW_PERSON_ID_LOOKUP', False):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied(
                'person_id lookup is disabled. Provide an inline patient_info '
                'payload instead.'
            )
        return _resolve_from_promop(person_id)

    return None


def _get_body_field(request, name: str) -> Any:
    """Read a field from request.data, tolerating None or non-dict bodies."""
    data = getattr(request, 'data', None)
    if not isinstance(data, dict):
        return None
    return data.get(name)


def _extract_person_id(request) -> Any:
    """Return the supplied person_id, or `_MISSING` when the caller named none.

    Presence, not truthiness. A JSON body can carry `{"person_id": 0}`, and
    `0` is falsy — reading this field with `or` turned that into "no patient
    supplied", which meant a bad id was answered with a whole-corpus search
    (and slipped past the 403 gate, since the gate only fires when a person_id
    is present). An empty `?person_id=` is the same mistake in the query
    string. Both are now *supplied but invalid*, and validation rejects them.

    The return is `Any` (not `str`) because the body path can carry a JSON
    integer while the query-string path always yields `str`.
    """
    query_params = getattr(request, 'query_params', None)
    if query_params:
        for key in ('person_id', 'personId'):
            if key in query_params:
                return query_params[key]

    data = getattr(request, 'data', None)
    if isinstance(data, dict):
        for key in ('person_id', 'personId'):
            if key in data:
                return data[key]

    return _MISSING


class PatientContextUnavailable(APIException):
    """A well-formed `person_id` was named but its patient could not be fetched.

    502 rather than 404: `fetch_patient` collapses every failure to None, and
    this code deliberately does not take them apart. PROMOP's 404 would say the
    patient doesn't exist — reporting that back would turn this route into a
    patient-existence oracle, on a route whose whole problem is that a service
    credential can read any patient (the IDOR the gate exists for, #150/#108).
    One conservative code for "we could not get them", whatever the reason:
    unreachable PROMOP, non-2xx, a body that isn't a row, or (since #448) no
    usable credential. Operators diagnose the real cause from the WARNING the
    client logs, which does distinguish them.

    A malformed `person_id` is NOT this error — see `_reject_malformed_person_id`.
    """
    status_code = 502
    default_detail = ('Could not fetch the requested patient from PROMOP. '
                      'No trial results are returned for an unresolved patient.')
    default_code = 'patient_context_unavailable'


def _reject_malformed_person_id(person_id: Any) -> None:
    """400 for a `person_id` that isn't a positive integer, before any fetch.

    `PromopClient.fetch_patient` also rejects these — without a network call, as
    a URL-injection guard — but it reports the rejection as None, which here
    would read as "PROMOP could not give us the patient" and answer 502. Blaming
    an upstream that was never called turns a permanently-unsatisfiable client
    mistake into a 5xx: alert noise, and a retry loop in any client that retries
    5xx. The shape check belongs to whoever can still tell the caller.

    Checked by type, not by `int()`. A JSON body carries real types, and `int()`
    *truncates* rather than refusing: `int(1.5)` is 1 and `int(True)` is 1, so
    `{"person_id": 1.5}` would have quietly fetched and matched patient 1 — a
    different, real patient's record. Anything that is not an integer, or an
    all-digit string of at most bigint's 19 digits, is rejected rather than
    rounded into somebody — or handed to an `int()` that refuses to convert it.
    """
    bad = ValidationError({'person_id': (
        'Must be a positive integer no larger than 9223372036854775807.'
    )})
    if isinstance(person_id, bool):
        # bool is a subclass of int; `True` would otherwise pass as patient 1.
        raise bad
    if isinstance(person_id, int):
        value = person_id
    elif isinstance(person_id, str) and _DIGITS_ONLY.fullmatch(person_id):
        # Not str.isdigit(): that accepts non-ASCII digit characters, which
        # int() then happily converts.
        value = int(person_id)
    else:
        raise bad
    if not 0 < value <= _MAX_PERSON_ID:
        raise bad


def _resolve_from_promop(person_id: Any) -> 'PatientInfo':
    """Fetch the PROMOP row and adapt it to a PatientInfo.

    Raises `PatientContextUnavailable` rather than returning None when the row
    can't be fetched. Returning None here would be read by the caller as "this
    request has no patient" — and a search with no patient answers with the
    whole corpus, unscored and unfiltered, which looks like a valid result
    (#156). A caller that named a `person_id` asked about *that* patient; the
    honest answer to "we couldn't get them" is an error, not every trial we
    know. Failing closed on the credential (#448) would otherwise have created
    exactly this: a dropped secret answering 200 with a full trial list.
    """
    from trials.services.patient_info.promop_adapter import (
        build_patient_info_from_promop_row,
    )
    from trials.services.patient_info.promop_client import PromopClient

    _reject_malformed_person_id(person_id)
    row = PromopClient().fetch_patient(person_id)
    if not row:
        raise PatientContextUnavailable()
    return build_patient_info_from_promop_row(row)


def _build_in_memory(data: dict) -> 'PatientInfo':
    """Build an unsaved PatientInfo from a dict, compute derived fields."""
    from trials.services.patient_info.patient_info import PatientInfo
    from trials.models import PreExistingConditionCategory

    # Extract M2M fields that can't be set on an unsaved instance
    pre_existing_ids = data.pop('pre_existing_condition_categories', None) or []
    concomitant_ids = data.pop('concomitant_medications', None) or []

    # Convert camelCase keys to snake_case if needed
    snake_data = _to_snake_case(data)

    # Filter to known model fields only
    model_fields = {f.name for f in PatientInfo._meta.get_fields() if hasattr(f, 'column')}
    filtered = {k: v for k, v in snake_data.items() if k in model_fields}

    # Coerce date strings from JSON into proper date objects
    _coerce_dates(filtered, PatientInfo)
    # Coerce numeric strings into proper numeric types (CB API can send "10.20" etc.)
    _coerce_numerics(filtered, PatientInfo)
    # Coerce string-encoded lists/dicts for JSONField columns (CB can send "[{...}]" as str)
    _coerce_json_fields(filtered, PatientInfo)
    # Enforce per-field item shape on JSON list fields downstream code iterates as dicts
    _normalize_structured_json_fields(filtered)

    pi = PatientInfo(**filtered)

    # Attach M2M as synthetic attributes so matchers can read them
    if pre_existing_ids:
        categories = list(PreExistingConditionCategory.objects.filter(pk__in=pre_existing_ids))
    else:
        categories = []
    pi._pre_existing_condition_categories = categories
    pi._concomitant_medications = concomitant_ids

    normalize_patient_info(pi)
    return pi


def _coerce_dates(data: dict, model_cls):
    """Parse ISO date strings into datetime.date for all DateField entries."""
    date_fields = {
        f.name for f in model_cls._meta.get_fields()
        if hasattr(f, 'column') and isinstance(f, (DateField, DateTimeField))
    }
    for key in date_fields & data.keys():
        val = data[key]
        if isinstance(val, str) and val:
            try:
                data[key] = dt.date.fromisoformat(val)
            except ValueError:
                data[key] = None


def _coerce_numerics(data: dict, model_cls):
    """Coerce string values to numeric types for IntegerField/FloatField/DecimalField columns."""
    for f in model_cls._meta.get_fields():
        if not hasattr(f, 'column') or f.name not in data:
            continue
        val = data[f.name]
        if not isinstance(val, str) or val == '':
            continue
        if isinstance(f, IntegerField):
            try:
                data[f.name] = int(val)
            except (ValueError, TypeError):
                data[f.name] = None
        elif isinstance(f, FloatField):
            try:
                data[f.name] = float(val)
            except (ValueError, TypeError):
                data[f.name] = None
        elif isinstance(f, DecimalField):
            try:
                data[f.name] = Decimal(val)
            except (InvalidOperation, TypeError):
                data[f.name] = None


def _normalize_structured_json_fields(data: dict):
    """Enforce list-of-dicts shape on JSON fields whose consumers call `.get(...)` per item.

    Bare-string items (legacy rows, malformed PROMOP input) would otherwise crash
    the matcher and trial-details renderer with `'str' object has no attribute 'get'`.
    """
    for key in ('later_therapies', 'supportive_therapies'):
        val = data.get(key)
        if val is None:
            continue
        if not isinstance(val, list):
            data[key] = []
            continue
        coerced = []
        for item in val:
            if isinstance(item, dict):
                coerced.append(item)
            elif isinstance(item, str) and item.strip():
                coerced.append({'therapy': item.strip()})
        data[key] = coerced

    val = data.get('genetic_mutations')
    if val is not None:
        if not isinstance(val, list):
            data['genetic_mutations'] = []
        else:
            data['genetic_mutations'] = [item for item in val if isinstance(item, dict)]

    # MCL list-of-strings fields: code lists (e.g. ['bone_marrow', 'gi_tract']).
    # Coerce None / non-list / non-string items to [] so the default=list
    # contract holds when CB sends `null` or `_coerce_json_fields` falls
    # through on malformed input (which sets the value to None).
    # NB: bulky_disease_criteria / high_risk_mcl_criteria are derived
    # comma-strings (computed in normalize for MCL), not list inputs.
    for key in ('extranodal_sites',):
        val = data.get(key)
        if key not in data:
            continue
        if not isinstance(val, list):
            data[key] = []
            continue
        data[key] = [s.strip() for s in val if isinstance(s, str) and s.strip()]


def _coerce_json_fields(data: dict, model_cls):
    """Parse string-encoded JSON/Python-repr values for JSONField columns."""
    json_fields = {
        f.name for f in model_cls._meta.get_fields()
        if hasattr(f, 'column') and isinstance(f, JSONField)
    }
    for key in json_fields & data.keys():
        val = data[key]
        if not isinstance(val, str):
            continue
        # Try JSON first, then Python repr (CB stores lists as Python repr strings)
        try:
            data[key] = json.loads(val)
        except (ValueError, TypeError):
            try:
                data[key] = ast.literal_eval(val)
            except (ValueError, SyntaxError):
                data[key] = None


def _to_snake_case(data: dict) -> dict:
    """Convert camelCase dict keys to snake_case."""
    import re
    def camel_to_snake(name):
        s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
        return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()

    return {camel_to_snake(k): v for k, v in data.items()}
