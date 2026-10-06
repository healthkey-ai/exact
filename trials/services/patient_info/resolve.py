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
import logging
import re
from math import isfinite
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Optional

from django.db.models import DateField, DateTimeField, DecimalField, FloatField, IntegerField, JSONField
from rest_framework.exceptions import APIException, ValidationError

from trials.services.patient_info.normalize import normalize_patient_info
from trials.services.omop.patient_languages import (
    LANGUAGE_CAPABILITY_FIELDS,
    _is_false,
    _is_true,
    language_skill_concept_ids_from_capabilities,
)

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


logger = logging.getLogger(__name__)


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
        # The one caller that is a client, so the one that gets 400s.
        return _build_in_memory(patient_info_data, strict=True)

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


#: PROMOP's machine-readable language capability (promop #827), for the LEGACY path
#: (#605, porting cb-like-trials #597/#607; fixes #591 on 2omop).
#:
#: PROMOP's ``languages_skills`` is a DISPLAY string, "English language: read,
#: speak; Spanish language: speak", which can never equal a trial code like
#: ``speak__en``. So a PROMOP patient who recorded any language failed every trial
#: with a language requirement. The same record carries eight three-valued
#: booleans; see ``exact_matching.patient_info.language_capability`` for the state
#: model built from them (H = held codes, A = asked languages).
_LEGACY_SKILLS = ('speak', 'write')   # the only capabilities trials are written in


def _has_value(value):
    return _is_true(value) or _is_false(value)


def languages_skills_from_capabilities(data):
    """Legacy H and A from PROMOP's eight capability booleans. Keeps the booleans.

    Returns ``data`` unchanged when none of the eight names is present, so a
    caller that sends CB codes and no booleans is unaffected. Otherwise returns a
    copy in which:

    * if any boolean has a value (parses as true or false), ``languages_skills``
      is rebuilt from the True speak/write ones as ``<skill>__<lang>`` codes
      (``None`` when there are none), and ``languages_asked`` lists the languages
      with at least one valued boolean. The booleans win over codes sent beside
      them;
    * if all of them are NULL or blank, ``languages_skills`` is dropped only when
      it is PROMOP's display string (it contains ':', which no CB code does), so
      codes a caller sent alongside a form's empty fields survive.

    The eight booleans are left in place for the OMOP builder
    (``language_skill_concept_ids_from_capabilities``), which reads the same
    values; the model-field filter drops them afterwards.
    """
    present = [name for name in LANGUAGE_CAPABILITY_FIELDS if name in data]
    if not present:
        return data
    out = dict(data)
    if not any(_has_value(data[name]) for name in present):
        current = out.get('languages_skills')
        if isinstance(current, str) and ':' in current:
            out['languages_skills'] = None
        return out
    held = sorted({
        f'{skill}__{code}' for name in present
        for code, skill in [LANGUAGE_CAPABILITY_FIELDS[name]]
        if skill in _LEGACY_SKILLS and _is_true(data[name])
    })
    asked = sorted({LANGUAGE_CAPABILITY_FIELDS[name][0] for name in present if _has_value(data[name])})
    out['languages_skills'] = ','.join(held) if held else None
    out['languages_asked'] = ','.join(asked) if asked else None
    return out


def translate_language_capabilities(data):
    """Both language translations from one read of the booleans (#605, CB #5350).

    The legacy H/A first, which keeps the booleans, then the OMOP pairs, which
    consume them under ``EXACT_OMOP_LANGUAGES`` + readiness. Each path reads its own
    fields, so whichever path the readiness snapshot picks has its values.
    """
    return language_skill_concept_ids_from_capabilities(languages_skills_from_capabilities(data))


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
    # PROMOP's language booleans -> legacy codes/asked languages and, on the OMOP
    # path, concept pairs, before the field filter below drops them (#605, CB #5350).
    snake_data = translate_language_capabilities(snake_data)

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
    # Which fields the CALLER named, as opposed to which happen to hold a value.
    # Every field below has `default=False`, so "key absent" and "key present,
    # value null" both arrive as a falsy field and the derivations cannot tell
    # them apart — an explicit "we do not know" became a confirmed "no". This is
    # the shared form of the marker #488 added for `tp53_disruption` alone.
    #
    # Captured from `filtered`, which keeps None values.
    #
    # This helper is NOT inline-payload-only — the CTOMOP/PROMOP adapter and
    # four management commands reach it too, and there a null is EXACT's own
    # stored derivation rather than anyone's assertion. What keeps those apart
    # today is `ctomop_adapter`, which strips None from the row before calling
    # in; nothing here enforces it. If that strip is ever widened the way
    # `tp53_disruption` is already exempted from it, upstream nulls would start
    # reading as caller assertions.
    pi._provided_fields = frozenset(filtered)
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
