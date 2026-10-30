"""Patient language pairs built by EXACT from PROMOP's capability booleans (CB #5350).

PROMOP's PatientRecord carries eight three-valued booleans,
``english_{speak,read,write,understand}`` and ``spanish_{...}`` (NULL = that language
was never asked about), derived from its ``PersonLanguageSkill`` rows. This module
turns the True ones into the pair strings the OMOP trial column holds,
``"<Language.omop_concept_id>:<LanguageSkillLevel.omop_concept_id>"``, and puts
them on ``language_skill_concept_ids``, which ``LANGUAGES_MATCH_PROFILE`` reads
under ``EXACT_OMOP_LANGUAGES``.

This is a deliberate exception to "EXACT owns no patient crosswalk" (the PROMOP
field was dropped for it, promop#1635; this module has no CB counterpart): the
concept ids come from the ``Language`` / ``LanguageSkillLevel`` rows EXACT reads
(loaded by ``load_language_omop_concept_ids``; in split-DB mode they are the CB
trials DB's rows, the same ones CB's backfill used), and trials use only en/es.

Because the patient side takes its ids from those vocab rows, a renumbering of
PROMOP's HK-Language mint no longer reaches matching through the patient. What
can still split the sides is the trial column falling behind the vocab rows (ids
re-curated, column not re-backfilled); the readiness gate's check (c) in
``languages_readiness`` refuses the OMOP path then.

Only capabilities that have a ``LanguageSkillLevel`` row with a concept id produce a
pair, i.e. speak and write (CB's vocab has no read/understand rows, and EXACT does not
add any, to stay identical to CB). Before the loader has run the readiness gate keeps
the flag effectively off, so the payload is left untouched.

State model (OMOP path only: ``EXACT_OMOP_LANGUAGES`` on and the readiness gate
satisfied; otherwise nothing here runs and matching is the legacy path)
-------------------------------------------------------------------------------
Patient, built from PROMOP's eight booleans:

* **H** (``language_skill_concept_ids``): the held pairs, ``"<lang_cid>:<skill_cid>"``
  for every True speak/write capability.
* **A** (``language_asked_concept_ids``): the ASKED language concept ids, as strings.
  A language is asked iff at least one of its four booleans is non-null, i.e. parses
  as true (``_is_true``) or as false (``_is_false``). Only languages whose vocab row
  has a concept id count.
* A client that sends ``language_skill_concept_ids`` directly, with no booleans, sends
  no A. A held pair says nothing about the other skills in its language, so no
  negative is known: A is empty for the verdict, every requirement H does not meet is
  unknown (case 4), and such a patient is never not_matched. It still counts as
  answered through H (case 2 needs both H and A empty).

Trial: **R** = the pairs in ``omop_languages_skills_required``. The language of a pair
is the part before ``':'``.

Verdict for one trial, in this order:

1. R empty -> matched (no requirement).
2. A and H both empty (nothing asked) -> the blank behaviour: the queryset does not
   filter, and the matcher says unknown.
3. R and H overlap -> matched.
4. some pair in R has a language not in A -> unknown. The trial is potential, stays
   in the queryset, and "languages" is offered to fill in.
5. otherwise every language in R was asked and nothing in R is held -> not_matched,
   excluded from the queryset.

So a patient asked about English who does not speak it fails an English-speaking
requirement (an empty consumer set is not read as unknown), while one never asked
about Spanish stays potential for a Spanish requirement. Every surface applies this
model: the queryset (SQL, ``eligible_for_languages_skills_omop``), the per-trial
matcher (``_match_languages_skills``), the potential-attrs count and attrs-to-fill-in
(``UserToTrialAttrsMapper``). The match-reason output shows the matcher's status,
but its patientValue / trialRequirement fields are not in this model's terms: they
show the joined held pairs and the legacy requirement, not H, A and R.
"""
import logging

from trials.services.omop.languages import language_skill_concept_key, load_concept_ids

logger = logging.getLogger(__name__)

_LANGUAGE_CODES = {'english': 'en', 'spanish': 'es'}
_SKILLS = ('speak', 'read', 'write', 'understand')

#: All eight names PROMOP sends. read/understand are consumed too, so their
#: presence still marks the payload as carrying the booleans.
LANGUAGE_CAPABILITY_FIELDS = {
    f'{language}_{skill}': (code, skill)
    for language, code in _LANGUAGE_CODES.items()
    for skill in _SKILLS
}


_TRUE_STRINGS = frozenset({'true', 't', 'yes', '1'})


_FALSE_STRINGS = frozenset({'false', 'f', 'no', '0'})


def _is_false(value):
    # The mirror of _is_true: False, int 0, or 'false'/'f'/'no'/'0' (any case,
    # stripped). Anything neither true nor false (None, '', 2, 'maybe') is not an
    # answer, so the language is not asked because of it.
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value == 0
    return isinstance(value, str) and value.strip().lower() in _FALSE_STRINGS


def _is_true(value):
    # A JSON boolean from PROMOP, an int 1, or a string a form-encoded client sends
    # ('true'/'t'/'yes'/'1', any case, stripped). Never truthiness: the string
    # "false" is truthy. bool is checked first because True == 1 but isinstance
    # (True, int) is also True; only the literal True and int 1 count.
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value == 1
    return isinstance(value, str) and value.strip().lower() in _TRUE_STRINGS


def language_skill_concept_ids_from_capabilities(data):
    """Replace the capability booleans in ``data`` with ``language_skill_concept_ids``.

    Returns ``data`` unchanged when ``EXACT_OMOP_LANGUAGES`` is off, or when none of
    the eight names is present (so a ``language_skill_concept_ids`` sent directly
    survives). Otherwise returns a copy without the eight names, whose
    ``language_skill_concept_ids`` is the sorted, de-duplicated pair list built
    from the True ones (``[]`` when none is True, including all-NULL), overriding
    any value sent directly. It also sets ``language_asked_concept_ids`` (A in the
    state model above). Reads the vocab concept ids once, and logs a warning
    when a True speak/write capability has no pair because the vocab is not loaded.
    """
    from exact_matching.omop.languages_match_profile import omop_languages_enabled

    if not omop_languages_enabled():
        return data  # flag off: nothing reads the pairs; payload untouched, no queries
    present = [name for name in LANGUAGE_CAPABILITY_FIELDS if name in data]
    if not present:
        return data
    out = {k: v for k, v in data.items() if k not in LANGUAGE_CAPABILITY_FIELDS}
    held = [LANGUAGE_CAPABILITY_FIELDS[name] for name in present if _is_true(data[name])]
    asked_codes = {
        LANGUAGE_CAPABILITY_FIELDS[name][0] for name in present
        if _is_true(data[name]) or _is_false(data[name])
    }
    pairs, asked = [], []
    if held or asked_codes:
        concept_ids = load_concept_ids()
        language_ids = concept_ids[0]
        asked = [str(language_ids[code]) for code in asked_codes if code in language_ids]
    if held:
        codes = [f'{skill}__{code}' for code, skill in held]
        pairs = [language_skill_concept_key(code, concept_ids) for code in codes]
        # read/understand never have a pair (no vocab rows); a speak/write one
        # without a pair means the vocab concept ids are not loaded.
        lost = sorted(
            code for code, pair in zip(codes, pairs)
            if pair is None and code.split('__')[0] in ('speak', 'write')
        )
        if lost:
            logger.warning(
                'language capabilities without an OMOP pair (run load_language_omop_concept_ids?): %s',
                ', '.join(lost),
            )
    out['language_skill_concept_ids'] = sorted({p for p in pairs if p is not None})
    out['language_asked_concept_ids'] = sorted(set(asked))
    return out
