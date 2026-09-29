"""The OMOP language verdict, shared by every surface (CB #5350).

State model: ``trials/services/omop/patient_languages.py``. R = the trial's pairs,
H = the patient's held pairs, A = the patient's asked language concept ids.

1. R empty -> matched; 2. A and H empty -> blank (the caller handles it); 3. R and H overlap
-> matched; 4. a pair in R names a language not in A -> unknown; 5. otherwise
not_matched.

``language_verdict`` decides one trial in Python (the matcher).
``language_verdict_sql`` returns the same three conditions as SQL over a trial
column, for the queryset filter and the potential-count fragments. The patient
values are inlined as literals: those fragments are raw SQL without parameters
(``add_potential_attrs_count``). So only values shaped like concept ids
(``^\\d+$``, ``^\\d+:\\d+$``) are kept, and anything else is dropped, never quoted.
"""
import re

# re.ASCII: without it \d also matches non-ASCII digits (e.g. Arabic-Indic).
_CID = re.compile(r'\d+', re.ASCII)
_PAIR = re.compile(r'\d+:\d+', re.ASCII)


def language_of(pair):
    return str(pair).split(':', 1)[0]


def language_verdict(required, held, asked):
    """'matched' / 'unknown' / 'not_matched' for a non-blank patient (cases 1, 3-5)."""
    # Only string elements are pairs; SQL ignores the rest the same way.
    required = [p for p in (required or []) if isinstance(p, str)]
    if not required:
        return 'matched'
    if set(required) & set(held):
        return 'matched'
    if any(language_of(p) not in set(asked) for p in required):
        return 'unknown'
    return 'not_matched'


def _text_array(values, shape):
    kept = sorted({v for v in (str(x) for x in values) if shape.fullmatch(v)})
    return 'ARRAY[' + ','.join(f"'{v}'" for v in kept) + ']::text[]'


def language_verdict_sql(column, held, asked):
    """``(required, overlap, unasked)`` SQL boolean conditions over ``column``.

    * required: R holds at least one string element (a ``[null]`` column is no
      requirement, as in ``language_verdict``);
    * overlap: R holds a pair in H (case 3);
    * unasked: some pair in R names a language outside A (case 4).

    Keep (queryset): ``NOT required OR overlap OR unasked``. Potential: ``required
    AND NOT overlap AND unasked``. Not matched: ``required AND NOT overlap AND NOT
    unasked``.
    """
    # ``column`` is left unqualified so that, inside a subquery Django aliases
    # (``pk__in=scope.values('pk')`` -> ``FROM trials_trial U0``), it binds to the
    # inner table rather than correlating with the outer one.
    strings = (
        f"(SELECT lang_pair #>> '{{}}' AS lang_pair FROM jsonb_array_elements("
        f"CASE WHEN jsonb_typeof({column}) = 'array' THEN {column} ELSE '[]'::jsonb END) AS lang_pair "
        f"WHERE jsonb_typeof(lang_pair) = 'string')"
    )
    required = f'EXISTS (SELECT 1 FROM {strings} AS r)'
    overlap = f'({column} ?| {_text_array(held, _PAIR)})'
    unasked = (
        f'EXISTS (SELECT 1 FROM {strings} AS r '
        f"WHERE split_part(r.lang_pair, ':', 1) <> ALL({_text_array(asked, _CID)}))"
    )
    return required, overlap, unasked
