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

The PROMOP `person_id` path calls PROMOP with a static service token
(`PROMOP_SERVICE_TOKEN`) that is NOT bound to the authenticated caller,
and PROMOP does not enforce row-level authz for that token — so honoring
an arbitrary `person_id` lets any authenticated caller enumerate other
patients' PHI (IDOR, #150/#108). EXACT also has no model linking users to
patients (it's stateless for patient data — see project memory
`feedback_exact_no_own_db.md`), so there's nothing in-tree to verify
against.

Because no production caller uses this path (the federation host fetches
the patient from PROMOP `/patient-info/me/` under the end-user's own token
and forwards it inline), the path is gated OFF by default outside
local/DEBUG via `EXACT_ALLOW_PERSON_ID_LOOKUP`. A request carrying
`person_id` while the gate is off gets a 403.

Re-enabling it in production requires BOTH:
- forwarding the caller's identity to PROMOP (token exchange / pass-through
  bearer or actor_iss/actor_sub — see hk-labs `promop_client.py`), AND
- PROMOP enforcing per-user authz (its `PatientUser`/consent models), or
  using the self-scoped `/patient-info/me/` route.

Tracked as #150/#108.
"""
import ast
import datetime as dt
import json
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Optional

from django.db.models import DateField, DateTimeField, DecimalField, FloatField, IntegerField, JSONField

from trials.services.patient_info.normalize import normalize_patient_info

if TYPE_CHECKING:
    from trials.services.patient_info.patient_info import PatientInfo


def resolve_patient_info(request) -> Optional['PatientInfo']:
    """
    Build an in-memory PatientInfo instance from the request.

    Resolution order:
      1. Inline `patient_info` payload (existing contract — unchanged).
      2. `person_id` query param or body field — fetch from PROMOP.
      3. Return None — caller may proceed without patient context
         (e.g. public trial browsing).
    """
    patient_info_data = _get_body_field(request, 'patient_info')
    if patient_info_data:
        return _build_in_memory(patient_info_data)

    person_id = _extract_person_id(request)
    if person_id:
        # IDOR gate (#150/#108): the PROMOP fetch uses a static service token
        # not bound to the caller, and PROMOP doesn't enforce row-level authz
        # for it — so honoring an arbitrary person_id leaks other patients'
        # PHI. Off by default outside local/DEBUG; reject rather than silently
        # ignore so the disabled path can't masquerade as a no-patient search.
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


def _extract_person_id(request) -> Optional[Any]:
    """Return person_id from query params first, then body. None if absent.

    The return is `Any` (not `str`) because the body path can carry a JSON
    integer (e.g. `{"person_id": 9003}`) while the query-string path always
    yields `str`. `PromopClient.fetch_patient` coerces both to int before
    constructing the URL.
    """
    query_params = getattr(request, 'query_params', None)
    if query_params:
        pid = query_params.get('person_id') or query_params.get('personId')
        if pid:
            return pid

    body_pid = _get_body_field(request, 'person_id') or _get_body_field(request, 'personId')
    return body_pid or None


def _resolve_from_promop(person_id: Any) -> Optional['PatientInfo']:
    """Fetch the PROMOP row and adapt it to a PatientInfo. None on any error."""
    from trials.services.patient_info.promop_adapter import (
        build_patient_info_from_promop_row,
    )
    from trials.services.patient_info.promop_client import PromopClient

    row = PromopClient().fetch_patient(person_id)
    if not row:
        return None
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
