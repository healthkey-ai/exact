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

This works in both directions. Carlos loses trials (case 5 used to be
Potential). A patient with held codes GAINS trials: a trial requiring an
unasked language (Spanish, or any `other`) used to be excluded on no overlap
and is now Potential, because the language nobody asked about is unknown.

Elements of R that are not codes (JSON null, blank strings) are ignored, the
same way in `verdict` and in the SQL.

Asking is per language, not per skill: `english_understand=True` alone marks
English asked, so that patient fails every `speak__en` / `write__en` trial.

When A is NOT known -- a caller that sends CB codes in `languages_skills` and no
booleans -- nothing here applies: matching is exactly the legacy two-state
overlap it was before, so such callers see no change.
"""
import re

from django.db.models import BooleanField, F, Func

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


def _codes(values):
    """The code elements of a trial list: non-blank strings, stripped."""
    return [c.strip() for c in (values or []) if isinstance(c, str) and c.strip()]


def verdict(required, held, asked):
    """The five-case verdict above, for one trial. `asked` must be non-empty."""
    required = _codes(required)
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


def _elements(column, alias):
    """Code elements of a jsonb list column: non-null, non-blank, trimmed."""
    return (
        f"(SELECT btrim({alias}.code) AS code FROM jsonb_array_elements_text("
        f"COALESCE({column}, '[]'::jsonb)) AS {alias}(code) "
        f"WHERE {alias}.code IS NOT NULL AND btrim({alias}.code) <> '')"
    )


# Inlined-SQL forms for the potential-attrs count, which is assembled as plain
# SQL text (RawSQL with no params) like the rest of that builder. Values are
# fixed patient-derived codes, inlined after dropping anything outside
# [a-zA-Z0-9_]. `column` must be UNQUALIFIED so it binds to whatever alias the
# enclosing query gives the trials table.

def sql_has_requirement(column):
    return f"EXISTS {_elements(column, '_r')}"


def sql_holds_any(column, held):
    in_list = _inline(held)
    if not in_list:
        return 'FALSE'
    return f"EXISTS (SELECT 1 FROM {_elements(column, '_h')} AS _hh WHERE _hh.code IN ({in_list}))"


def sql_has_unasked_language(column, asked):
    """The trial requires a language not in `asked`. A code without `__` has
    language '' and so counts as not asked, which keeps the trial."""
    in_list = _inline(asked)
    condition = f"split_part(_ll.code, '__', 2) NOT IN ({in_list})" if in_list else 'TRUE'
    return f"EXISTS (SELECT 1 FROM {_elements(column, '_l')} AS _ll WHERE {condition})"


class LanguagesKept(Func):
    """Boolean expression: the search keeps this trial (verdict is not not_matched).

    A real expression, not RawSQL, so the column is compiled through the ORM and
    re-aliased when the queryset is nested (`pk__in=scope.values('pk')` turns the
    table into U0; a hard-coded "trials_trial" there made a correlated subquery
    that timed out on the count query). `held` and `asked` are bound parameters.
    """
    output_field = BooleanField()

    def __init__(self, column, held, asked):
        super().__init__(F(column))
        self.held = sorted({str(v).strip() for v in held if str(v).strip()})
        self.asked = sorted({str(v).strip() for v in asked if str(v).strip()})

    def as_sql(self, compiler, connection, **extra):
        col, col_params = compiler.compile(self.source_expressions[0])
        sql = (
            f"(NOT EXISTS {_elements(col, '_r')}"
            f" OR EXISTS (SELECT 1 FROM {_elements(col, '_h')} AS _hh WHERE _hh.code = ANY(%s))"
            f" OR EXISTS (SELECT 1 FROM {_elements(col, '_l')} AS _ll"
            f" WHERE NOT (split_part(_ll.code, '__', 2) = ANY(%s))))"
        )
        params = [*col_params, *col_params, self.held, *col_params, self.asked]
        return sql, params
