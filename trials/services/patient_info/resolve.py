"""
PatientInfo resolver — supports two contract shapes.

1. Inline payload: `{"patient_info": {...}}` in the request body. No DB
   lookup; PatientInfo is never persisted by this path. CancerBot
   depends on this contract — do not change.
2. CTOMOP fetch: `?person_id=` query param or `person_id` in the body.
   Looks up the patient from CTOMOP via `CtomopClient` and feeds the
   row through `build_patient_info_from_ctomop_row` (#102). Gated behind
   `EXACT_ALLOW_PERSON_ID_LOOKUP` (off by default outside local/DEBUG) —
   see the authorization boundary below.

The inline path takes precedence — if both `patient_info` and
`person_id` are present, the inline payload wins (lets callers stage
the migration without breaking).

## Authorization boundary

The CTOMOP `person_id` path calls CTOMOP with a static service token
(`CTOMOP_SERVICE_TOKEN`) that is NOT bound to the authenticated caller,
and CTOMOP does not enforce row-level authz for that token — so honoring
an arbitrary `person_id` lets any authenticated caller enumerate other
patients' PHI (IDOR, #150/#108). EXACT also has no model linking users to
patients (it's stateless for patient data — see project memory
`feedback_exact_no_own_db.md`), so there's nothing in-tree to verify
against.

Because no production caller uses this path (the federation host fetches
the patient from CTOMOP `/patient-info/me/` under the end-user's own token
and forwards it inline), the path is gated OFF by default outside
local/DEBUG via `EXACT_ALLOW_PERSON_ID_LOOKUP`. A request carrying
`person_id` while the gate is off gets a 403.

Re-enabling it in production requires BOTH:
- forwarding the caller's identity to CTOMOP (token exchange / pass-through
  bearer or actor_iss/actor_sub — see hk-labs `ctomop_client.py`), AND
- CTOMOP enforcing per-user authz (its `PatientUser`/consent models), or
  using the self-scoped `/patient-info/me/` route.

Tracked as #150/#108.
"""
import ast
import datetime as dt
import json
import logging
from math import isfinite
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Optional

from django.db.models import DateField, DateTimeField, DecimalField, FloatField, IntegerField, JSONField

from trials.services.patient_info.normalize import normalize_patient_info

if TYPE_CHECKING:
    from trials.services.patient_info.patient_info import PatientInfo


logger = logging.getLogger(__name__)


def resolve_patient_info(request) -> Optional['PatientInfo']:
    """
    Build an in-memory PatientInfo instance from the request.

    Resolution order:
      1. Inline `patient_info` payload (existing contract — unchanged).
      2. `person_id` query param or body field — fetch from CTOMOP.
      3. Return None — caller may proceed without patient context
         (e.g. public trial browsing).
    """
    patient_info_data = _get_body_field(request, 'patient_info')
    if patient_info_data:
        # The one caller that is a client, so the one that gets 400s.
        return _build_in_memory(patient_info_data, strict=True)

    person_id = _extract_person_id(request)
    if person_id:
        # IDOR gate (#150/#108): the CTOMOP fetch uses a static service token
        # not bound to the caller, and CTOMOP doesn't enforce row-level authz
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
        return _resolve_from_ctomop(person_id)

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
    yields `str`. `CtomopClient.fetch_patient` coerces both to int before
    constructing the URL.
    """
    query_params = getattr(request, 'query_params', None)
    if query_params:
        pid = query_params.get('person_id') or query_params.get('personId')
        if pid:
            return pid

    body_pid = _get_body_field(request, 'person_id') or _get_body_field(request, 'personId')
    return body_pid or None


def _resolve_from_ctomop(person_id: Any) -> Optional['PatientInfo']:
    """Fetch the CTOMOP row and adapt it to a PatientInfo. None on any error."""
    from trials.services.patient_info.ctomop_adapter import (
        build_patient_info_from_ctomop_row,
    )
    from trials.services.patient_info.ctomop_client import CtomopClient

    row = CtomopClient().fetch_patient(person_id)
    if not row:
        return None
    return build_patient_info_from_ctomop_row(row)


def _build_in_memory(data: dict, strict: bool = False) -> 'PatientInfo':
    """Build an unsaved PatientInfo from a dict, compute derived fields.

    `strict` refuses a malformed value instead of falling back to the
    derivation. It is a property of the HTTP boundary, not of this helper, so
    it defaults OFF and exactly one caller turns it on: the inline payload in
    `resolve_patient_info`, where the caller is a client and 400 is the right
    answer. The CTOMOP adapter and the management commands leave it off —
    there a raise blames the wrong party (see below).
    """
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
    if 'tp53_disruption' in filtered:
        value = filtered['tp53_disruption']
        if value is not None and type(value) is not bool and strict:
            # The inline path only: a client sent something that is not an
            # aggregate, and 400 names the right party.
            from rest_framework.exceptions import ValidationError
            raise ValidationError(
                {'patient_info': {'tp53_disruption': 'Expected a boolean or null.'}}
            )
        if value is None or type(value) is bool:
            # An explicit aggregate is supplied by the caller (including
            # unknown). Retain it across normalization and later
            # attribute-service instances.
            pi._provided_tp53_disruption = value
        else:
            # Anything else is not an aggregate we can trust, so no provenance
            # is recorded and the legacy derivation runs — exactly what these
            # values did before the aggregate was honoured at all, since
            # `normalize` overwrote the field unconditionally.
            #
            # Deliberately not a ValidationError. This helper is shared: the
            # CTOMOP adapter and four management commands
            # (`search_trials_for_patients`, `explain_trial_match`,
            # `probe_eligibility`, `compare_trials`) all reach it, so a raise
            # here turns a malformed UPSTREAM row into a client 400 —
            # `trials_views._resolve_patient_info` re-raises `APIException`
            # unchanged, and its own comment says a person_id-path failure
            # must surface as 500 "rather than masking it as a misleading
            # 400". In a batch command it is not a 400 at all, it is a
            # traceback. It also breaks the contract this function documents
            # and implements next door: `_coerce_dates`, `_coerce_numerics`
            # ("CB API can send \"10.20\"") and `_coerce_json_fields` all
            # accept loose input, and the module docstring says of the inline
            # path "CancerBot depends on this contract — do not change".
            #
            # Reached only with `strict` off, i.e. from the CTOMOP adapter or
            # a management command. Raising there blames the wrong party: the
            # value came from UPSTREAM, and `trials_views._resolve_patient_info`
            # re-raises `APIException` unchanged while its own comment says a
            # person_id-path failure must surface as 500 "rather than masking
            # it as a misleading 400". In a batch command it is not a 400 at
            # all, it is a traceback that ends the run.
            #
            # Falling back is also what these values did before the aggregate
            # was honoured at all, since `normalize` overwrote the field
            # regardless — the status quo, not new leniency.
            logger.warning(
                'Ignoring tp53_disruption of unsupported type %s for person_id '
                '%s; falling back to the marker derivation.',
                type(value).__name__, data.get('person_id', '<inline>'),
            )

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
        if not isinstance(val, str):
            continue
        # '' used to short-circuit here (`or val == ''`), which left a `str`
        # sitting in a numeric column: the first comparison against a threshold
        # raises TypeError from inside request resolution, the search fails, and
        # every trial is hidden. `creatinine_clearance_rate: ""` does exactly
        # that on the base branch via `meets_crab_r_renal_insufficiency`.
        # Letting it reach the branches below turns it into None — an empty
        # string is an ABSENT reading, and None is how this file spells that.
        # Non-numeric columns are untouched: they match no branch and keep ''.
        if isinstance(f, IntegerField):
            try:
                data[f.name] = int(val)
            except (ValueError, TypeError):
                try:
                    # "40.5" is a stated measurement and `int()` refuses it.
                    # This module's own docstring says CB sends decimal strings,
                    # so discarding one throws away a reading the caller gave us
                    # — for eGFR that silently hands the screen to the derived
                    # value instead.
                    #
                    # `float`, NOT `int(float(...))`. Truncating would make the
                    # quoted and unquoted forms of the same number mean different
                    # things across all 31 IntegerField columns, several of which
                    # are physiologically fractional: "990.9"/"9.95" for the FLC
                    # pair floors to 990/9, a ratio of 110 against a true 99.59,
                    # which trips `meets_slim` and republishes a smoldering
                    # patient as active. Floor is also biased on every ceiling
                    # criterion ("2.9" vs `ecog_max=2` becomes a match). Nothing
                    # enforces the column type here — `PatientInfo` is a plain
                    # object, and `normalize` already writes a float 129.93 into
                    # this very "IntegerField".
                    coerced = float(val)
                    if not isfinite(coerced):
                        # "inf" parses where int() refused it. Infinity clears
                        # every ceiling criterion and reads as adequate renal
                        # function; it is not a measurement.
                        raise ValueError(val)
                    data[f.name] = coerced
                except (ValueError, TypeError, OverflowError):
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

    Bare-string items (legacy rows, malformed CTOMOP input) would otherwise crash
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
