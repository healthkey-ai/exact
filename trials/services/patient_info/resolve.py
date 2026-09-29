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
import logging
import math
import re
import datetime as dt
import json
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Optional

from django.db.models import (
    DateField, DecimalField, FloatField, IntegerField, JSONField, Q,
)
# Module level, unlike the local imports below. Those are inside functions
# because they predate this one and nothing made them move; this one is on
# the inline path's error translation and would be a second copy of the
# same line there.
from rest_framework.exceptions import ValidationError

from trials.services.patient_info.normalize import normalize_patient_info

logger = logging.getLogger(__name__)

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
    patient_info_data, sent_as = _patient_info_payload(request)
    if sent_as is not None:
        if not patient_info_data:
            # `{}` or `null` under the key: said, and says nothing.
            return None
        return _patient_from_inline(patient_info_data, sent_as)

    person_id = _extract_person_id(request)
    if person_id:
        # IDOR gate (#150/#108): the CTOMOP fetch uses a static service token
        # not bound to the caller, and CTOMOP doesn't enforce row-level authz
        # for it — so honoring an arbitrary person_id leaks other patients'
        # PHI. Off by default outside local/DEBUG; reject rather than silently
        # ignore so the disabled path can't masquerade as a no-patient search.
        from django.conf import settings
        if not getattr(settings, 'EXACT_ALLOW_PERSON_ID_LOOKUP', False):
            from rest_framework.exceptions import PermissionDenied, ValidationError
            raise PermissionDenied(
                'person_id lookup is disabled. Provide an inline patient_info '
                'payload instead.'
            )
        return _resolve_from_ctomop(person_id)

    return None


#: Both spellings of the inline payload key. `docs/api.md` documents the
#: camelCase one and always has; the parser only ever read the snake_case one,
#: and an unread key is not an error — so the documented request ran the
#: matcher with NO patient and answered 200 with the unfiltered catalog.
#: Measured against a real corpus: 2 376 matching trials under `patient_info`,
#: 18 462 under `patientInfo` (#375).
#:
#: Accepted rather than rejected, because the docs are the contract an
#: integrator reads and this is the shape they were told to send. The inner
#: keys were always handled either way — `_to_snake_case` does that — so only
#: the outer one was ever wrong.
PATIENT_INFO_KEYS = ('patient_info', 'patientInfo')


def _patient_info_payload(request):
    """The inline payload and the key it arrived under, or `(None, None)`.

    A key that is PRESENT but empty is still the caller's statement — "I have
    no patient" — and it ends resolution here rather than falling through to
    `person_id`. Gating on truthiness made `{"patient_info": {}, "person_id":
    7}` do a person lookup, contradicting both the documented meaning of the
    empty object and this module's own inline-first precedence.
    """
    data = getattr(request, 'data', None)
    if not isinstance(data, dict):
        return None, None
    # A usable payload wins over an empty one, whichever key it came under:
    # `{"patient_info": null, "patientInfo": {...}}` is a caller sending both
    # spellings, and answering "no patient" because the first one is empty
    # would discard the one they filled in. Between two usable payloads the
    # snake_case key still decides — it is the existing contract.
    present = [key for key in PATIENT_INFO_KEYS if key in data]
    for key in present:
        if data[key]:
            return data[key], key
    if present:
        return data[present[0]], present[0]
    return None, None


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


#: How many unrecognised keys the 400 quotes back, and how long each may be.
#: `pagination.py` made the same call for `?limit=` two files away — "don't
#: echo unbounded user input back in the error body" — and this is the same
#: shape: a payload with 5 000 junk keys produced a 69 kB response, and one
#: 200 000-character key produced a 200 kB one. Naming a few is what makes the
#: error useful; naming all of them is a megaphone.
_QUOTED_KEYS = 10
_QUOTED_KEY_LENGTH = 64


#: The one payload key that is genuinely many-to-many. Named here, and used
#: both by the recognition test and by the pop in `_build_in_memory`, so the
#: two cannot drift: it is NOT a model field — it is attached as a synthetic
#: `_`-prefixed attribute, because an unsaved instance cannot hold an M2M — so
#: a recognition test built from `_meta` alone refused the documented key.
#:
#: `concomitant_medications` was popped here too and is not an M2M at all: it
#: is a `TextField` with a column, which the queryset
#: (`_filter_concomitant_medications`) and the matcher
#: (`_match_concomitant_medications`, through `get_value`) both read AS a
#: column. Nothing anywhere reads the `_concomitant_medications` the pop
#: created. So the pop only ever deleted the value — silently, and
#: asymmetrically: the snake_case spelling was popped and lost, while the
#: camelCase one escaped by accident and worked. Not popped now, so both
#: spellings set the column.
M2M_PAYLOAD_KEYS = ('pre_existing_condition_categories',)


#: Names the producer sends for a field EXACT stores under a different one.
#:
#: Same shape of defect as `patientInfo` (#375) and
#: `preExistingConditionCategories` above: a key the caller sends, means, and
#: never learns was ignored. It is not in `model_fields`, so `_build_in_memory`
#: drops it at the filter and answers 200.
#:
#: `pd_l1_tumor_cels` is EXACT's own spelling and it is missing an `l`
#: (`patient_info.py`, and the trial bounds `pd_l1_tumor_cels_min` / `_max`).
#: PROMOP spells it correctly — `omop_core/models.py:3232`, written at
#: `patient_record_service.py:2440` — so the value never arrives (#593).
#:
#: Aliased inbound rather than renamed. The misspelling is EXACT's column name
#: and the stem of two TRIAL columns; correcting all of them is a migration
#: plus a data move for no difference any caller can observe. This costs one
#: line and is reversible.
_INBOUND_ALIASES = {
    'pd_l1_tumor_cells': 'pd_l1_tumor_cels',
}

#: PROMOP's machine-readable form of language capability (promop #827).
#:
#: PROMOP's `languages_skills` is a DISPLAY string, "English language: read,
#: speak; Spanish language: speak" (`format_language_skills` in
#: `omop_core/models.py`), and it can never equal a trial code like
#: `speak__en`. So a PROMOP patient who recorded any language failed every
#: trial with a language requirement (#591). The same record carries eight
#: three-valued booleans, english_/spanish_ x speak/read/write/understand
#: (NULL = that language was never asked about), derived from the same
#: `PersonLanguageSkill` rows.
#:
#: Only the two capabilities trials are written in, speak and write, become
#: codes, so the value stays inside the vocabulary the "Yours" cell labels.
#: The cost is that "reads English only", or a language answered with no
#: capability, reads as unknown and does not fail a "speaks English"
#: requirement; a language other than English or Spanish is not unrolled by
#: PROMOP at all. This is the interim read fix. The OMOP pair path
#: (cancerbot-org/cancerbot#5350) carries all four capabilities as concepts.
_LANGUAGE_CAPABILITY_CODES = {
    f'{language}_{skill}': f'{skill}__{code}'
    for language, code in (('english', 'en'), ('spanish', 'es'))
    for skill in ('speak', 'write')
}
#: All eight names PROMOP sends. The four without a code are consumed here so
#: that their presence still marks the record as PROMOP's; they are not
#: recognised by the gate, because nothing EXACT stores comes from them.
_LANGUAGE_CAPABILITY_FIELDS = {
    f'{language}_{skill}'
    for language in ('english', 'spanish')
    for skill in ('speak', 'read', 'write', 'understand')
}


def _is_true(value):
    # A JSON boolean from PROMOP, or the string a form-encoded client sends.
    # Never truthiness: the string "false" is truthy.
    return value is True or (isinstance(value, str) and value.strip().lower() == 'true')


def languages_skills_from_capabilities(data):
    """Replace a PROMOP display `languages_skills` with codes from the booleans.

    Returns `data` unchanged when none of the eight names is present. Otherwise
    returns a copy without them, and:

    * if any of them has a value, `languages_skills` is rebuilt from the True
      speak/write ones (`None` when there are none, which skips the filter);
    * if all of them are NULL or blank, `languages_skills` is dropped only when
      it is PROMOP's display string (it contains ':', which no CB code does),
      so codes a caller sent alongside a form's empty fields survive.

    When a boolean does have a value, the booleans win over any codes sent
    beside them.
    """
    if not any(name in data for name in _LANGUAGE_CAPABILITY_FIELDS):
        return data
    out = {k: v for k, v in data.items() if k not in _LANGUAGE_CAPABILITY_FIELDS}
    if not any(_says_something(data.get(name)) for name in _LANGUAGE_CAPABILITY_FIELDS):
        current = out.get('languages_skills')
        if isinstance(current, str) and ':' in current:
            out['languages_skills'] = None
        return out
    held = sorted(code for name, code in _LANGUAGE_CAPABILITY_CODES.items() if _is_true(data.get(name)))
    out['languages_skills'] = ','.join(held) if held else None
    return out


#: A second entry is coming (#590), and this table has four ways to go wrong
#: with nothing failing: chaining one entry's target into another's source,
#: two sources onto one target, a source that shadows a real field, and a
#: source that is not snake_case — both readers snake-case before they look
#: one up, so such an entry never matches anything. `TestTheTableItself`
#: checks all four. Kept there rather than as module-level `assert`s: those
#: vanish under `python -O`, and a typo in a literal should not stop the
#: service booting.


def _known_attribute_names():
    """Every name whose value `_build_in_memory` will actually use.

    Not "every name it reads": an alias source is recognised here and never
    reaches the instance under that spelling, because `_normalise_inbound_keys`
    moves its value onto the field first. The distinction matters because the
    argument below is about what a payload naming ONLY a given name produces,
    and for an alias source that is a real patient, not a blank one.

    The set has to be exactly that, and getting it wrong in either direction
    puts back the defect this module is fixing:

    * too narrow and a field EXACT does use is answered 400 — the M2M keys
      have no column, so a column-only test refused `preExistingConditionCategories`
      outright;
    * too wide and a payload naming only something we DROP passes the check
      and produces the blank patient again, one step further along.

    `geo_point` is the second case and is deliberately absent. It is an
    attribute on the instance, and it is what the distance filter reads — but
    it is not in `_FIELDS`, so `_build_in_memory` discards a caller-supplied
    one. EXACT computes it from the country and postal code instead. A payload
    naming only `geo_point` therefore tells us nothing we will use, and 400 is
    the honest answer.
    """
    from trials.services.patient_info.patient_info import PatientInfo

    names = {
        field.name
        for field in PatientInfo._meta.get_fields()
        if hasattr(field, 'column')
    }
    names.update(M2M_PAYLOAD_KEYS)
    # Third case: a name that is not a field and is not dropped either,
    # because `_normalise_inbound_keys` moves its value onto one that is.
    # Leaving it out made the two spellings of the SAME field disagree about
    # the empty state — `{"pd_l1_tumor_cells": null}` fell into "something we
    # could not read" and answered 400, while `{"pd_l1_tumor_cels": null}`
    # answered None. The form-backed client this empty state exists for is
    # exactly the one serialising every field, so it would have met that.
    names.update(_INBOUND_ALIASES)
    # Same third case: the speak/write language booleans are moved onto
    # `languages_skills` by `languages_skills_from_capabilities`, so a payload
    # carrying only them describes a patient.
    names.update(_LANGUAGE_CAPABILITY_CODES)
    return names


def _says_something(value):
    """Whether a value tells us anything about a patient.

    `[]` does — for the M2M list it is the statement "none of these", which is
    a fact rather than silence. `None`, `''` and whitespace do not: a field
    holding three spaces is an empty form field, and building a patient whose
    disease is `'   '` narrows the corpus on it.

    `False` and `0` DO. They are answers.
    """
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _patient_from_inline(payload, sent_as):
    """The inline payload, in three states rather than two.

    `_build_in_memory` filters a payload down to known fields, and the gate
    above it tested only that the object was non-empty. So a payload whose
    keys this service does not know passed the gate and emptied at the filter,
    and what came out was a BLANK `PatientInfo` — not `None`. Every downstream
    "is there a patient?" check passed, the matcher found nothing standing in
    the way, and the answer was `eligible`, score 100, for every trial (#466).

    Three states, because two cannot separate the caller who typed a field
    name wrong from the one who has nothing to say yet:

        no name we recognise  -> 400. You meant something we cannot read.
        names but no values   -> None. You read the contract; there is just
                                 nothing in it yet — a form-backed client
                                 serialising every field as null is well
                                 behaved, and answering it 400 would be wrong.
        anything else         -> a patient.

    Sending no key at all is a fourth thing and unchanged: a search without a
    patient, which is supported.

    Deciding here rather than inside `_build_in_memory` because that function
    is shared: the CTOMOP adapter and `explain_trial_match` call it too, and a
    sparse CTOMOP row would otherwise answer the caller 400 about a
    `patient_info` key their request never contained — and bypass the view's
    deliberate choice to let a `person_id` failure surface as a 500 rather
    than be masked as a misleading 400.
    """
    from rest_framework.exceptions import ValidationError

    # Snake-cased first: `patientAge` and `patient_age` are the same field, and
    # the M2M keys have to be seen through the same lens as the rest — reading
    # the raw keys meant `preExistingConditionCategories` was neither
    # recognised nor popped, so the documented spelling of a field EXACT
    # curates was refused, and its value silently dropped in a mixed payload.
    snake = _to_snake_case(payload)
    known = _known_attribute_names()
    recognised = {name for name in snake if name in known}

    # Asked in this order because the two questions compose, and the first
    # version short-circuited: it refused only when NOTHING was recognised, so
    # one recognised-but-empty key disarmed the check entirely and
    # `{"patient_age": null, "diseas": "myeloma"}` fell through to "no
    # patient" — the whole catalog, 200, no signal. Straight back to #375.
    #
    # That combination is not a curiosity: the form-backed client the empty
    # state exists FOR — every field serialised, most of them null — is
    # exactly the client most likely to also carry a misspelled one.
    # A language boolean says something only when it becomes a code: a False
    # one is dropped by `languages_skills_from_capabilities`, and letting it
    # through alone would build the blank patient again.
    if any(
        _says_something(snake[name])
        and (name not in _LANGUAGE_CAPABILITY_CODES or _is_true(snake[name]))
        for name in recognised
    ):
        # Something usable arrived. Unrecognised keys alongside it are not an
        # error: a client sending a field EXACT has not heard of yet, next to
        # ones it has, described a patient.
        #
        # Strict, and translated HERE rather than raised from the builder:
        # this is the one caller who typed the value and can fix it. See
        # `MalformedPatientValue` for what raising it deeper cost.
        try:
            return _build_in_memory(payload, strict=True)
        except MalformedPatientValue as wrong:
            raise ValidationError({
                _as_the_caller_wrote_it(payload, wrong.field_name): wrong.as_message()
            })

    if set(snake) - recognised:
        # Nothing usable, and something we could not read. That is a caller
        # who meant to describe a patient and was not understood.
        raise ValidationError({sent_as: _no_recognised_fields_message(payload)})

    # Every name recognised, no value in any of them: the contract was read
    # and there is nothing in it yet.
    return None


def _as_the_caller_wrote_it(payload, field_name: str) -> str:
    """The key the caller actually sent for this column.

    `_no_recognised_fields_message` below exists for this reason and says it
    in its own words: "Names the keys the CALLER typed, not what they
    became… `patientAgee` came back as `patient_agee`, a string the caller
    never wrote." An error naming `patient_age` at somebody who sent
    `patientAge` makes them search their payload for a key that is not in
    it.
    """
    for sent in payload:
        if not isinstance(sent, str):
            continue
        snake = camel_to_snake(sent)
        # Through the alias table too, so a caller who sent the DOCUMENTED
        # `pd_l1_tumor_cells` is told about `pd_l1_tumor_cells`, not about
        # EXACT's misspelled column.
        if snake == field_name or _INBOUND_ALIASES.get(snake) == field_name:
            return sent
    # Nothing in the payload maps to it — a derived column, or a name this
    # function has not been taught. The column name is a worse answer than
    # the caller's own key and a better one than silence.
    return field_name


def _no_recognised_fields_message(payload):
    """Names the keys the CALLER typed, not what they became.

    The point of listing them is that "invalid patient_info" sends the reader
    back to guess which of forty fields was wrong. Listing the snake_cased
    forms undoes that — `patientAgee` came back as `patient_agee`, a string the
    caller never wrote.
    """
    keys = [str(key) for key in payload]
    quoted = [
        key[:_QUOTED_KEY_LENGTH] + ('…' if len(key) > _QUOTED_KEY_LENGTH else '')
        for key in sorted(keys)[:_QUOTED_KEYS]
    ]
    listed = ', '.join(quoted)
    if len(keys) > _QUOTED_KEYS:
        listed += f' (and {len(keys) - _QUOTED_KEYS} more)'
    return (
        'No recognised patient fields. EXACT understood none of: '
        + listed
        + '. Send an empty object to search without a patient.'
    )


def _build_in_memory(data: dict, strict: bool = False) -> 'PatientInfo':
    """Build an unsaved PatientInfo from a dict, compute derived fields.

    `strict` is about WHO SENT the dict, not about how careful to be. Only
    the inline payload has a caller who can fix a wrong value, so only that
    path asks for the raise; see `MalformedPatientValue`. An upstream row
    and the batch commands get what they always got — the value dropped and
    a log line — because telling a reader that PROMOP holds `'unknown'` in
    a lab column asks them to fix something they cannot reach.
    """
    from trials.services.patient_info.patient_info import PatientInfo
    from trials.models import PreExistingConditionCategory

    # Converted BEFORE the M2M pop, not after. The pops name the snake_case
    # keys, so `preExistingConditionCategories` — the spelling the docs
    # publish, and the one EXACT's own serializer emits — was never popped and
    # then dropped by the field filter, because an M2M has no column. The
    # value vanished, with a 200: measured, `{"preExistingConditionCategories":
    # [1]}` produced a patient with no categories while the snake_case form
    # produced the real one.
    snake_data = languages_skills_from_capabilities(_normalise_inbound_keys(data))

    # Extract the M2M field, which cannot be set on an unsaved instance
    pre_existing_ids = snake_data.pop(M2M_PAYLOAD_KEYS[0], None) or []

    # Filter to known model fields only
    model_fields = {f.name for f in PatientInfo._meta.get_fields() if hasattr(f, 'column')}
    filtered = {k: v for k, v in snake_data.items() if k in model_fields}

    # Coerce date strings from JSON into proper date objects
    _coerce_dates(filtered, PatientInfo, strict)
    # Coerce numeric strings into proper numeric types (CB API can send "10.20" etc.)
    _coerce_numerics(filtered, PatientInfo, strict)
    # Coerce string-encoded lists/dicts for JSONField columns (CB can send "[{...}]" as str)
    _coerce_json_fields(filtered, PatientInfo)
    # Enforce per-field item shape on JSON list fields downstream code iterates as dicts
    _normalize_structured_json_fields(filtered)
    # After the JSON coercion above, since CTOMOP can send this field as a
    # string holding the JSON rather than as a list.
    _normalize_stem_cell_transplant_history(filtered)

    pi = PatientInfo(**filtered)

    # Attach M2M as synthetic attributes so matchers can read them
    # By id OR by code, because the documented example uses codes and the
    # lookup only ever took ids. That mismatch was invisible while the
    # camelCase spelling of this key was being dropped before it got here: the
    # documented request lost the value silently. Reaching the lookup, it
    # raised `ValueError: Field 'id' expected a number but got 'cardiacIssues'`
    # — a 400 saying nothing about which key or why. `code` is unique on the
    # model, so both readings are unambiguous and neither is a guess.
    categories = []
    if pre_existing_ids:
        ids, codes = [], []
        for value in pre_existing_ids:
            # `bool` before `int`, because in Python it IS one: `true` would
            # otherwise be looked up as primary key 1 and select whichever
            # category happens to hold it.
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                ids.append(value)
            elif isinstance(value, str):
                # A digit string is an id, which is what `pk__in` accepted
                # before this — form encoding turns every value into a string,
                # so reading `"12"` as a code would silently drop it.
                (ids if value.strip().lstrip('-').isdigit() else codes).append(
                    int(value) if value.strip().lstrip('-').isdigit() else value
                )
        lookups = Q()
        if ids:
            lookups |= Q(pk__in=ids)
        if codes:
            lookups |= Q(code__in=codes)
        if ids or codes:
            categories = list(PreExistingConditionCategory.objects.filter(lookups))
    pi._pre_existing_condition_categories = categories

    normalize_patient_info(pi)
    return pi


#: THREE STATES FOR A TYPED VALUE, and before #594 there were four ANSWERS
#: for the same mistake, none of them chosen. Measured on `pd_l1_tumor_cels`,
#: an `IntegerField`:
#:
#:     'abc'   -> silently None -> no filter applied at all
#:     true    -> silently 1    -> filtered on one
#:     ''      -> 500 ValueError from the ORM
#:     [1] {}  -> 500 TypeError from the ORM
#:
#: The silent ones are the dangerous half, not the safe half:
#: `eligible_for_min_max_value` returns the WHOLE scope for a `None`, so a
#: numeric value quietly dropped shows the reader trials they may not
#: qualify for. In a clinical matcher that costs more than an error does.
#:
#: So a value is absent, usable, or wrong:
#:
#:   absent  — no key, `null`, or a string of whitespace. "Not answered".
#:             Becomes `None`, and no filter applies. `_says_something` is
#:             the same predicate the rest of this module already uses, so
#:             an empty form field means here what it means everywhere.
#:   usable  — can be the column's type without guessing.
#:   wrong   — anything else. A `ValidationError` naming the field and what
#:             it expected, which DRF renders as a 400. The view re-raises
#:             an `APIException` untouched, so the caller is told WHICH of
#:             ~170 fields was bad rather than "could not build patient
#:             context" — the same reasoning as `_no_recognised_fields_message`.
#:
#: A BOOLEAN IS WRONG ON A NUMBER, though Python says `True` is an `int` and
#: the ORM would have taken it as 1. Nobody means one by `true`; a form that
#: sends it has a bug, and 1 is a plausible enough PD-L1 percentage that
#: nothing downstream would look odd.


class MalformedPatientValue(ValueError):
    """A value that cannot be its column's type.

    A PLAIN exception, not a DRF one, and that distinction is the whole of
    the first review round on this change. `_build_in_memory` has three
    callers and only one of them is a caller who can be blamed:

      * the inline payload — somebody who typed the value and can fix it.
        `_patient_from_inline` turns this into a 400 naming the key THEY
        sent.
      * a CTOMOP/PROMOP row — the reader cannot fix PROMOP, and this
        adapter already expects unparseable labs: `ctomop_adapter` names
        `'unknown'` and a censored `'<0.5'` in as many words. Dropped to
        `None`, with a log line.
      * the batch commands (`search_trials_for_patients`,
        `probe_eligibility`, `explain_trial_match`). Two of them have no
        guard at all, so a DRF exception here killed the run on a row that
        used to print, and the third counted the patient as an error and
        could exit non-zero for a whole cohort sharing one censored lab.

    Raising a `ValidationError` from the builder reached all three, and the
    view's `except APIException: raise` fires BEFORE the branch that keeps a
    `person_id` failure a 500 — so a bad PROMOP column answered 400, naming
    a field the caller never sent, at the wrong party. Measured.
    """

    def __init__(self, field_name: str, expected: str, value):
        self.field_name = field_name
        self.expected = expected
        self.got = type(value).__name__
        super().__init__(
            f'{field_name}: expected {expected}, got {self.got}.'
        )

    def as_message(self) -> str:
        return (
            f'Expected {self.expected}, got {self.got}. '
            f'Send null or omit the field if it was not answered.'
        )


#: Every `DateField` here is a date, but the value arriving may be a
#: datetime — `ctomop_adapter` calls `.isoformat()` on anything that is a
#: `date`, and a `datetime` IS one, so it emits `2026-01-31T10:00:00` itself.
#: `dt.date.fromisoformat` rejects that. Parsed as a datetime first and
#: narrowed, so a producer that sends the time of day is understood rather
#: than told its own output is malformed.
def _as_date(key: str, val):
    if isinstance(val, dt.datetime):
        return val.date()
    if isinstance(val, dt.date):
        return val
    if isinstance(val, str):
        text = val.strip()
        for parse in (dt.date.fromisoformat, lambda t: dt.datetime.fromisoformat(t).date()):
            try:
                return parse(text)
            except ValueError:
                continue
    raise MalformedPatientValue(key, 'an ISO date such as 2026-01-31', val)


def _coerce_dates(data: dict, model_cls, strict: bool = False):
    """Absent, a date, or wrong. See `MalformedPatientValue`."""
    date_fields = {
        f.name for f in model_cls._meta.get_fields()
        # `DateTimeField` subclasses `DateField`, so naming both was
        # redundant. Left as one name rather than two that look like a pair.
        if hasattr(f, 'column') and isinstance(f, DateField)
    }
    for key in date_fields & data.keys():
        if not _says_something(data[key]):
            data[key] = None
            continue
        try:
            data[key] = _as_date(key, data[key])
        except MalformedPatientValue:
            if strict:
                raise
            logger.warning(
                'Dropping an unusable value for %s from an upstream row: %r',
                key, data[key],
            )
            data[key] = None


#: What each numeric column accepts, and what to call it when it does not.
#:
#: Walked in order rather than looked up by class because `Field` subclasses
#: overlap: `PositiveIntegerField`, `SmallIntegerField` and `BigIntegerField`
#: all derive from `IntegerField` and must match it. `BooleanField` does NOT
#: — an earlier version of this comment said it did, and it is wrong on
#: Django 5.2: `BooleanField` derives from `Field`, and the 40 boolean
#: columns here are matched by no entry. `AutoField` does derive from
#: `IntegerField` and WOULD be matched; nothing here excludes it, and
#: nothing needs to, because `PatientInfo` is a plain class with a
#: hand-built `_meta` and has no `AutoField`, no `ForeignKey` and no `id`.
_NUMERIC_KINDS = (
    (IntegerField, int, 'a whole number'),
    (FloatField, float, 'a number'),
    (DecimalField, Decimal, 'a number'),
)


def _as_number(key: str, val, parse, expected: str):
    # A BOOLEAN IS WRONG ON A NUMBER, though Python says `True` is an `int`
    # and the ORM would take it as 1. Nobody means one by `true`, and 1 is a
    # plausible enough PD-L1 percentage that nothing downstream looks odd.
    if isinstance(val, bool):
        raise MalformedPatientValue(key, expected, val)
    if isinstance(val, str):
        try:
            val = parse(val.strip())
        except (ValueError, TypeError, InvalidOperation):
            raise MalformedPatientValue(key, expected, val)
    elif not isinstance(val, (int, float, Decimal)):
        raise MalformedPatientValue(key, expected, val)
    # NaN AND INFINITY ARE NOT NUMBERS HERE, though `float('nan')` and
    # `Decimal('Infinity')` both construct. `lab_number` in `ctomop_adapter`
    # already refuses them and says why: a NaN reaches DRF's renderer, which
    # will not emit it and answers 500 — and through the non-HTTP path it is
    # quieter and worse, because every threshold comparison against it is
    # False and the patient fails criteria nobody can see them failing.
    if isinstance(val, float) and not math.isfinite(val):
        raise MalformedPatientValue(key, expected, val)
    if isinstance(val, Decimal) and not val.is_finite():
        raise MalformedPatientValue(key, expected, val)
    return val


def _coerce_numerics(data: dict, model_cls, strict: bool = False):
    """Absent, a number, or wrong. See `MalformedPatientValue`."""
    for f in model_cls._meta.get_fields():
        if not hasattr(f, 'column') or f.name not in data:
            continue
        for field_cls, parse, expected in _NUMERIC_KINDS:
            if isinstance(f, field_cls):
                break
        else:
            continue
        if not _says_something(data[f.name]):
            data[f.name] = None
            continue
        try:
            data[f.name] = _as_number(f.name, data[f.name], parse, expected)
        except MalformedPatientValue:
            if strict:
                raise
            logger.warning(
                'Dropping an unusable value for %s from an upstream row: %r',
                f.name, data[f.name],
            )
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


def _normalise_inbound_keys(data: dict) -> dict:
    """Snake-case the payload's keys, then apply the aliases.

    The gate does NOT call this, and that is deliberate rather than an
    omission. `_patient_from_inline` only has to decide whether EXACT
    understands a name, and `_known_attribute_names` answers that from the
    same `_INBOUND_ALIASES` — so the two cannot drift, and moving values there
    as well changed no decision I could construct or test.

    What they must not do is disagree, which is this module's recurring
    defect: `preExistingConditionCategories` was neither recognised nor
    popped, and the first draft of this change recognised the alias nowhere,
    so `{"pd_l1_tumor_cells": null}` answered 400 while the same field under
    EXACT's own spelling answered "no patient". One constant, read by both.

    An alias never overwrites a value the caller put under EXACT's own name.
    Same tie-break as `PATIENT_INFO_KEYS`: between two usable spellings the
    existing contract decides, and the alias only fills a gap.

    Both sides of that are guarded, and the SOURCE side is the one with
    teeth. Copying an empty string onto a typed column put it somewhere the
    drop-at-the-filter path never let it reach, and the chain that made that
    a 500 is written out below because it is the reason this guard exists,
    not because it still fires: `_coerce_numerics` skipped `''`,
    `is_attr_blank` blanks an `IntegerField` only on `== 0`, and
    `eligible_for_min_max_value` then handed it to Django, which raised
    `ValueError: expected a number but got ''`. A 500 through the spelling
    every client sends, where EXACT's own misspelling — which no client
    sends — was the only way in before.

    #594 closed that chain at its head: `''` is now "not answered" and
    becomes `None`. The guard stays, because it is about the ALIAS not
    manufacturing a value, and that is true whatever the column does with
    one.
    """
    snake = _to_snake_case(data)
    for sent, stored in _INBOUND_ALIASES.items():
        if _says_something(snake.get(sent)) and not _says_something(snake.get(stored)):
            snake[stored] = snake[sent]
    return snake


def camel_to_snake(name: str) -> str:
    """One key's camelCase spelling, snake_cased.

    Module level so `_INBOUND_ALIASES` can be checked against it: both
    readers of that table snake-case before they look a source up, so a
    source that is not already snake_case matches nothing and says nothing.
    """
    import re

    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()


def _to_snake_case(data: dict) -> dict:
    """Convert camelCase dict keys to snake_case."""
    return {camel_to_snake(k): v for k, v in data.items()}
