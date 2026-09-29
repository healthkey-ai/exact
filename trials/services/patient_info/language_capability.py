"""Language requirements with a third patient state: asked, and not able (#591, P1-1).

PROMOP sends eight three-valued booleans per patient, english_/spanish_ x
speak/read/write/understand, where NULL means that language was never asked
about. `resolve.languages_skills_from_capabilities` turns them into two values
on the patient:

* H = `languages_skills`: the held codes, `<skill>__<lang>` for True speak/write
  only (the capabilities trials are written in, and the ones the "Yours" cell
  can label);
* A = `languages_asked`: the languages asked about, `en`/`es`, comma-joined. A
  language is asked iff at least one of its four booleans has a value (True or
  False).

Trial side: R = `languages_skills_required`, codes `<skill>__<lang>`; a code's
language is the part after `__`.

Verdict, when A is known (PROMOP booleans arrived):

1. R empty                          -> no requirement (not_evaluated / kept).
2. A empty                          -> today's blank behaviour: no filter, unknown.
3. R and H share a code             -> matched.
4. some language in R is not in A   -> unknown (kept, Potential).
5. otherwise                        -> not_matched (every required language was
                                       asked, none of the required codes is held;
                                       excluded from search).

`other` is never in A (PROMOP unrolls only English and Spanish), so a trial
requiring `speak__other` always falls under case 4 for a PROMOP patient.

When A is NOT known -- a caller that sends CB codes in `languages_skills` and no
booleans -- nothing here applies: matching is exactly the legacy two-state
overlap it was before, so such callers see no change.
"""
import re

LANGUAGE_CODES = {'english': 'en', 'spanish': 'es'}

_SAFE = re.compile(r'[a-zA-Z0-9_]+')


def _split(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        parts = value
    else:
        parts = str(value).split(',')
    return [str(p).strip() for p in parts if str(p).strip()]


def code_language(code):
    """`speak__en` -> `en`; '' when the code has no `__`."""
    _skill, sep, language = str(code).partition('__')
    return language if sep else ''


def asked_languages(patient_info):
    """A: the languages the patient was asked about, or [] when unknown."""
    if patient_info is None:
        return []
    return sorted(set(_split(getattr(patient_info, 'languages_asked', None))))


def held_codes(patient_info):
    """H: the held capability codes."""
    if patient_info is None:
        return []
    return sorted(set(_split(getattr(patient_info, 'languages_skills', None))))


def verdict(required, held, asked):
    """The five-case verdict above, for one trial. `asked` must be non-empty."""
    required = [str(c).strip() for c in (required or []) if str(c).strip()]
    if not required:
        return 'not_evaluated'
    if set(required) & set(held):
        return 'matched'
    if any(code_language(c) not in set(asked) for c in required):
        return 'unknown'
    return 'not_matched'


def _inline(values):
    safe = sorted({v for v in values if _SAFE.fullmatch(v or '')})
    return ', '.join(f"'{v}'" for v in safe)


def sql_has_unasked_language(column, asked):
    """SQL boolean: the trial requires at least one language not in `asked`.

    `asked` is a fixed, patient-derived set of two-letter codes, inlined after
    dropping anything outside [a-zA-Z0-9_]. A code without `__` has language ''
    and so counts as not asked, which keeps the trial rather than excluding it.
    """
    in_list = _inline(asked)
    condition = f"split_part(_l.code, '__', 2) NOT IN ({in_list})" if in_list else 'TRUE'
    return (
        f"EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE({column}, '[]'::jsonb)) "
        f"AS _l(code) WHERE {condition})"
    )


def sql_holds_any(column, held):
    """SQL boolean: the trial's list shares at least one code with `held`."""
    in_list = _inline(held)
    if not in_list:
        return 'FALSE'
    return (
        f"EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE({column}, '[]'::jsonb)) "
        f"AS _h(code) WHERE _h.code IN ({in_list}))"
    )


def sql_has_requirement(column):
    return f"jsonb_array_length(COALESCE({column}, '[]'::jsonb)) > 0"
