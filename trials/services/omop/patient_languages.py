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

Interim limitation: only capabilities that have a ``LanguageSkillLevel`` row with a
concept id produce a pair, i.e. speak and write (CB's vocab has no read/understand
rows, and EXACT does not add any, to stay identical to CB). A patient who only
reads, or only understands, gets ``[]`` and reads as unknown, not as failing a
requirement. Before the loader has run the readiness gate keeps the flag
effectively off, so the payload is left untouched.
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
    any value sent directly. Reads the vocab concept ids once, and logs a warning
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
    pairs = []
    if held:
        concept_ids = load_concept_ids()
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
    return out
