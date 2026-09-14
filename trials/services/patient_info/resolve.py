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
import re
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
      2. `person_id` query param or body field — fetch from CTOMOP.
      3. Return None — caller may proceed without patient context
         (e.g. public trial browsing).
    """
    patient_info_data = _get_body_field(request, 'patient_info')
    if patient_info_data:
        return _build_in_memory(patient_info_data)

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
    # After the JSON coercion above, since CTOMOP can send this field as a
    # string holding the JSON rather than as a list.
    _normalize_stem_cell_transplant_history(filtered)

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


#: Procedure-name patterns, mapped to the vocabulary codes this service
#: matches on (`value_options.stem_cell_transplant_history`). Ported from
#: SoC's `core/pipeline/_transplant_codes.py`, which solved the same problem
#: against the same CTOMOP field and anchored these patterns on HT's own
#: `procedureMappings` vocabulary — including the ones that name no type
#: explicitly ("Myeloablative Allotransplant", "Allograft of cord blood") but
#: are unambiguously allogeneic.
#:
#: Type-specific tokens first, so a string naming both breaks the tie the same
#: way every time.
_SCT_PROCEDURE_PATTERNS = (
    ('autologous', 'completedASCT'),
    ('autograft', 'completedASCT'),
    ('autotransplant', 'completedASCT'),
    ('auto-sct', 'completedASCT'),
    ('ahct', 'completedASCT'),
    ('asct', 'completedASCT'),
    ('allogeneic', 'completedAllogeneicSCT'),
    ('allogenic', 'completedAllogeneicSCT'),   # the common misspelling
    ('allograft', 'completedAllogeneicSCT'),
    ('allotransplant', 'completedAllogeneicSCT'),
    ('allo-sct', 'completedAllogeneicSCT'),
    ('allohct', 'completedAllogeneicSCT'),
)


#: Words that describe a transplant which has NOT happened. Substring
#: matching cannot read them — "ASCT-ineligible" contains "asct" — so a row
#: carrying one is unclassifiable and discards the whole history, the same
#: conservative answer this module gives a name it does not recognise.
#:
#: Not mapped to the status codes this vocabulary also has
#: (`ineligibleForASCT`, `preASCT`): the loader only writes a row when
#: `has_transplant` is true, so a row that then says "ineligible" contradicts
#: its own presence, and picking a winner is the confident wrong answer this
#: module exists to avoid.
_SCT_NOT_YET_WORDS = (
    'eligib', 'planned', 'candidate', 'intended', 'pre-', 'prior to',
    'consideration', 'workup', 'evaluation',
)

#: Negations, matched against the TYPE TOKEN rather than the whole string.
#: A first version discarded any row containing "non-", which is wrong twice
#: over: "Non-myeloablative allogeneic stem cell transplant" is a standard
#: name for a transplant that DID happen — the negation attaches to
#: "myeloablative", not to "allogeneic" — and the same name without the hyphen
#: classified fine, so the answer depended on spelling. One such row voided
#: every other row in the history too. Review caught it.
_SCT_NEGATIONS = ('non-', 'non ', 'not ', 'no ')


def _names(haystack, pattern):
    """Whether `haystack` uses `pattern` as a word, not inside another one.

    The short acronyms are the reason: `asct`, `ahct`, `allohct` would
    otherwise match wherever those letters happen to fall, and this classifies
    a completed transplant. A boundary is anything that is not a letter or a
    digit, so the hyphenated forms (`auto-sct`) still match their own hyphen.
    """
    return re.search(r'(?<![a-z0-9])' + re.escape(pattern) + r'(?![a-z0-9])', haystack) is not None


def _is_negated(haystack, pattern):
    """Whether `pattern` is immediately preceded by a negation in `haystack`.

    "non-autologous" is negated; "non-myeloablative allogeneic" is not, because
    the negation sits against a different word.
    """
    start = 0
    while True:
        at = haystack.find(pattern, start)
        if at < 0:
            return False
        before = haystack[:at]
        if any(before.endswith(word) for word in _SCT_NEGATIONS):
            return True
        start = at + 1


def _sct_codes_in(procedures_text):
    """The codes a comma-separated procedure string names, first seen first.

    Empty when the text says the transplant has not happened, or negates the
    type it names — so the caller discards the history rather than recording a
    transplant that was refused, planned or ruled out as one that took place.
    """
    haystack = procedures_text.lower()
    if any(word in haystack for word in _SCT_NOT_YET_WORDS):
        return []
    found = []
    for pattern, code in _SCT_PROCEDURE_PATTERNS:
        if not _names(haystack, pattern) or code in found:
            continue
        if _is_negated(haystack, pattern):
            # "non-autologous transplant" asserts the opposite of what the
            # substring says. Discard the row rather than guess the type.
            return []
        found.append(code)
    return found


def _sct_vocabulary():
    """The codes this service offers for a transplant history."""
    from trials.services.value_options import ValueOptions

    return set(ValueOptions().stem_cell_transplant_history)


def _normalize_stem_cell_transplant_history(data: dict):
    """Flatten CTOMOP's structured transplant rows into vocabulary codes.

    CTOMOP's loader writes one dict per line of therapy:

        [{"line_number": 1, "procedures": "Autologous stem cell transplant"}]

    while everything downstream expects a list of hashable vocabulary codes:
    `eligible_for_stem_cell_transplant_history` does
    `SCT_HISTORY_EXCLUDED_MAPPING.get(item, [item])`, which raises
    `TypeError: unhashable type: 'dict'` on a row — a 500 on every
    patient-context endpoint for any patient CTOMOP knows a transplant for
    (#141). Both BigQuery-sourced and seeded patients carry this shape.

    Normalised here rather than in the matcher deliberately: the matcher and
    the queryset are ~95% shared with CancerBot, which never sees this shape,
    and `docs/porting-from-cancerbot.md` makes divergence there expensive. The
    shape gymnastics belong at the integration boundary. The ticket recommends
    the same, and SoC's adapter took that route.

    AN UNCLASSIFIABLE ROW DISCARDS THE WHOLE HISTORY, and that is the
    important part. `procedures` is free text, so a row can say "Stem Cell
    Transplant" — true, and unclassifiable. Emitting the rows we DID classify
    would turn "this patient had an autologous transplant and something we
    could not read" into "this patient had an autologous transplant", and a
    trial that EXCLUDES allogeneic transplants would go from unknown to
    eligible for a patient whose unreadable row may well have been allogeneic.
    Discarding the lot leaves the field blank, which the matcher reports as
    `unknown` and the queryset does not filter on — Potential rather than a
    confident wrong answer. SoC's module documents the same failure and makes
    the same call.
    """
    value = data.get('stem_cell_transplant_history')
    if not isinstance(value, list) or not value:
        return
    if not any(isinstance(item, dict) for item in value):
        # Already the canonical shape — a list of codes — PROVIDED every item
        # is one. A list of lists carries no dict, so an "is it structured?"
        # test alone waved it through to the queryset and the very
        # `unhashable type` this function exists to prevent, with `list` in
        # place of `dict`.
        if not all(isinstance(item, str) for item in value):
            data['stem_cell_transplant_history'] = None
        return

    codes = []
    for entry in value:
        if not isinstance(entry, dict):
            # A bare code alongside structured rows is kept — but only if it
            # IS one. An unrecognised string beside CTOMOP rows is a third
            # thing nobody can account for, and keeping it would leave a value
            # the matcher silently ignores sitting inside a history this
            # function is otherwise being careful about. Discard, like any
            # other row it cannot read.
            #
            # A list holding ONLY strings is left alone further up: that is
            # the canonical shape from a caller who knows the vocabulary, and
            # validating it here would be a contract change for every
            # non-CTOMOP client rather than a fix for this one.
            if not isinstance(entry, str) or entry not in _sct_vocabulary():
                data['stem_cell_transplant_history'] = None
                return
            if entry not in codes:
                codes.append(entry)
            continue
        procedures = entry.get('procedures')
        if not isinstance(procedures, str) or not procedures.strip():
            # The loader only writes a row when `has_transplant` is true, so
            # an empty name is a transplant we cannot classify, not an absence.
            data['stem_cell_transplant_history'] = None
            return
        detected = _sct_codes_in(procedures)
        if not detected:
            data['stem_cell_transplant_history'] = None
            return
        for code in detected:
            if code not in codes:
                codes.append(code)

    data['stem_cell_transplant_history'] = codes or None


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
