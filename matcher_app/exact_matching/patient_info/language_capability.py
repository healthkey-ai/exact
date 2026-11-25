"""Language requirements on the LEGACY path, with the asked state (#605, #591).

Ported from the widget line (cb-like-trials #597/#607). This is what matches
whenever the OMOP path is not active: ``EXACT_OMOP_LANGUAGES`` off, or the
readiness gate not satisfied. The same state model as the OMOP path
(``trials/services/omop/patient_languages.py``), in the legacy code vocabulary:

* H = ``languages_skills``: the held codes, ``<skill>__<lang>``, for True
  speak/write only. These are the capabilities trials are written in, and the
  only ones the "Yours" cell can label. ``resolve.languages_skills_from_capabilities``
  builds H from PROMOP's eight booleans (``english_``/``spanish_`` x
  speak/read/write/understand; NULL = never asked).
* A = ``languages_asked``: the languages asked about, ``en``/``es``,
  comma-joined. A language is asked iff at least one of its four booleans parses
  as true or false (``patient_languages._is_true`` / ``_is_false``).

Trial: R = ``languages_skills_required``. A code's language is the part after
``__``. Elements that are not codes (non-strings, blank strings) are ignored,
the same way in ``verdict`` and in the SQL.

When A is known (a patient built from the booleans):

1. R empty -> matched (no requirement).
2. A empty -> the blank behaviour: no filter, unknown.
3. R and H share a code -> matched.
4. some language in R is not in A -> unknown (kept, potential).
5. otherwise -> not_matched: every required language was asked and none of the
   required codes is held (excluded from search).

``other`` is never in A (PROMOP unrolls only English and Spanish), so a
``speak__other`` requirement is always case 4 for a PROMOP patient. Asking is
per language, not per skill: ``english_understand=True`` alone marks English as
asked, so that patient fails every ``speak__en`` / ``write__en`` trial.

When A is NOT known (a caller that sends CB codes in ``languages_skills`` and no
booleans), nothing here applies. Matching is the legacy two-state overlap it
always was, so such callers see no change.

The decision itself is shared with the OMOP path
(``exact_matching.omop.languages_verdict.three_state_verdict``); only the
vocabulary (codes here, concept pairs there) differs.
"""
import re

from django.db.models import BooleanField, F, Func

from exact_matching.omop.languages_verdict import three_state_verdict

_SAFE = re.compile(r'[a-zA-Z0-9_]+', re.ASCII)


def _split(value):
    if value is None:
        return []
    parts = value if isinstance(value, (list, tuple)) else str(value).split(',')
    return [str(p).strip() for p in parts if p is not None and str(p).strip()]


def code_language(code):
    """``speak__en`` -> ``en``; ``''`` when the code has no ``__``."""
    _skill, sep, language = str(code).partition('__')
    return language if sep else ''


def asked_languages(patient_info):
    """A: the languages the patient was asked about, or [] when unknown."""
    if patient_info is None:
        return []
    # Same allowlist the count SQL applies, so a caller-sent junk value cannot
    # make the Python and SQL surfaces disagree.
    return sorted({v for v in _split(getattr(patient_info, 'languages_asked', None)) if _SAFE.fullmatch(v)})


def held_codes(patient_info):
    """H: the held capability codes."""
    if patient_info is None:
        return []
    return sorted(set(_split(getattr(patient_info, 'languages_skills', None))))


def _codes(values):
    """The code elements of a trial list: non-blank strings, stripped."""
    return [c.strip() for c in (values or []) if isinstance(c, str) and c.strip()]


def verdict(required, held, asked):
    """The five-case verdict above, for one trial. ``asked`` must be non-empty."""
    return three_state_verdict(_codes(required), held, asked, code_language)


def _inline(values):
    safe = sorted({v for v in values if _SAFE.fullmatch(v or '')})
    return ', '.join(f"'{v}'" for v in safe)


def _elements(column, alias):
    """Code elements of a jsonb list column: strings only, non-blank, trimmed."""
    return (
        f"(SELECT btrim({alias}.code #>> '{{}}') AS code FROM jsonb_array_elements("
        f"CASE WHEN jsonb_typeof({column}) = 'array' THEN {column} ELSE '[]'::jsonb END) "
        f"AS {alias}(code) "
        f"WHERE jsonb_typeof({alias}.code) = 'string' AND btrim({alias}.code #>> '{{}}') <> '')"
    )


# Inlined-SQL forms for the potential-attrs count, which is assembled as plain SQL
# text (RawSQL with no params) like the rest of that builder. Values are
# patient-derived codes, inlined only if they match [a-zA-Z0-9_]+; anything else
# is dropped. ``column`` must be UNQUALIFIED, so it binds to whatever alias the
# enclosing query gives the trials table.

def sql_has_requirement(column):
    return f'EXISTS {_elements(column, "_r")}'


def sql_holds_any(column, held):
    in_list = _inline(held)
    if not in_list:
        return 'FALSE'
    return f'EXISTS (SELECT 1 FROM {_elements(column, "_h")} AS _hh WHERE _hh.code IN ({in_list}))'


def sql_has_unasked_language(column, asked):
    """The trial requires a language not in ``asked``. A code without ``__`` has
    language '' and so counts as not asked, which keeps the trial."""
    in_list = _inline(asked)
    condition = f"split_part(_ll.code, '__', 2) NOT IN ({in_list})" if in_list else 'TRUE'
    return f'EXISTS (SELECT 1 FROM {_elements(column, "_l")} AS _ll WHERE {condition})'


class LanguagesKept(Func):
    """Boolean expression: the search keeps this trial (verdict is not not_matched).

    A real expression, not RawSQL with a table name, so the column is compiled
    through the ORM and re-aliased when the queryset is nested
    (``pk__in=scope.values('pk')`` turns the table into U0; a hard-coded table
    name there makes a correlated subquery). ``held`` and ``asked`` are bound
    parameters.
    """
    output_field = BooleanField()

    def __init__(self, column, held, asked):
        super().__init__(F(column))
        self.held = sorted({str(v).strip() for v in held if str(v).strip()})
        self.asked = sorted({str(v).strip() for v in asked if str(v).strip()})

    def as_sql(self, compiler, connection, **extra):
        col, col_params = compiler.compile(self.source_expressions[0])
        sql = (
            f'(NOT EXISTS {_elements(col, "_r")}'
            f' OR EXISTS (SELECT 1 FROM {_elements(col, "_h")} AS _hh WHERE _hh.code = ANY(%s))'
            f' OR EXISTS (SELECT 1 FROM {_elements(col, "_l")} AS _ll'
            f" WHERE NOT (split_part(_ll.code, '__', 2) = ANY(%s))))"
        )
        # the column appears twice in each of the three subqueries (see _elements)
        params = [*col_params * 2, *col_params * 2, self.held, *col_params * 2, self.asked]
        return sql, params
