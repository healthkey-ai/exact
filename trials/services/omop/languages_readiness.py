"""Readiness gate for OMOP language-skill matching (CB #5350).

``EXACT_OMOP_LANGUAGES`` alone is not enough. The OMOP path reads trial pairs that
CB backfilled from CB's vocab, and patient pairs that EXACT builds from its own
vocab (``patient_languages``). If either side is missing or the two disagree,
every language-restricted trial silently reports the wrong answer: with the vocab
unloaded the patient has no pairs and every restricted trial reads as matched,
and with a CB re-curation of the ids a patient's pair never overlaps and the trial
is hard-excluded. So ``omop_languages_enabled()`` is ``setting AND ready``, and a
not-ready process falls back to the legacy path.

Checks (``check_languages_readiness``):

a) vocab: ``Language`` en/es and ``LanguageSkillLevel`` speak/write all carry an
   ``omop_concept_id`` (``load_language_omop_concept_ids`` has run);
b) backfill coverage: no trial whose legacy ``languages_skills_required`` holds a
   code that has a pair under the current vocab while
   ``omop_languages_skills_required`` lacks that pair;
c) CB<->EXACT drift: every distinct pair in ``omop_languages_skills_required``
   across the catalog is one EXACT's vocab can produce. Only a duplicated mapping
   CSV binds CB's trial ids to EXACT's patient ids.

Warning, not a failure: trial languages other than en/es. PROMOP unrolls only
English and Spanish, so a patient can never carry those and such a requirement
reads as unknown.

The result is cached per process for ``READINESS_TTL_SECONDS``; a not-ready result
is logged at ERROR once per window, when it is computed.
"""
import logging
import time
from dataclasses import dataclass, field

from django.db import connections, router
from django.db.models import Q

logger = logging.getLogger(__name__)

READINESS_TTL_SECONDS = 300

REQUIRED_LANGUAGES = ('en', 'es')
REQUIRED_SKILLS = ('speak', 'write')

_cache = {'at': None, 'report': None}


@dataclass
class ReadinessReport:
    ok: bool
    reasons: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def _producible_pairs(concept_ids):
    """``{'<skill>__<lang>': 'lang_cid:skill_cid'}`` for every code the vocab maps."""
    language_ids, skill_ids = concept_ids
    return {
        f'{skill}__{language}': f'{language_cid}:{skill_cid}'
        for language, language_cid in language_ids.items()
        for skill, skill_cid in skill_ids.items()
    }


def _distinct_elements(column):
    from trials.models import Trial

    db = router.db_for_read(Trial) or 'default'
    sql = (
        f'SELECT DISTINCT jsonb_array_elements_text("{column}") '
        f'FROM "{Trial._meta.db_table}" WHERE jsonb_typeof("{column}") = \'array\''
    )
    with connections[db].cursor() as cursor:
        cursor.execute(sql)
        return {row[0] for row in cursor.fetchall()}


def check_languages_readiness():
    """Compute a fresh ``ReadinessReport`` (no cache)."""
    from trials.models import Trial
    from trials.services.omop.languages import load_concept_ids

    reasons, warnings = [], []
    concept_ids = load_concept_ids()
    language_ids, skill_ids = concept_ids

    # a) vocab loaded
    missing = [f'Language.{c}' for c in REQUIRED_LANGUAGES if c not in language_ids]
    missing += [f'LanguageSkillLevel.{c}' for c in REQUIRED_SKILLS if c not in skill_ids]
    if missing:
        reasons.append(
            f'vocab: no omop_concept_id on {", ".join(missing)}; run load_language_omop_concept_ids')

    # b) backfill coverage: one query over every (code, pair) the vocab can produce
    producible = _producible_pairs(concept_ids)
    if producible:
        uncovered = Q()
        for code, pair in producible.items():
            uncovered |= Q(languages_skills_required__has_key=code) & ~Q(omop_languages_skills_required__has_key=pair)
        stale = Trial.objects.filter(uncovered).count()
        if stale:
            reasons.append(
                f'backfill: {stale} trial(s) require a mapped language code whose pair is missing '
                f'from omop_languages_skills_required; run backfill_omop_languages_skills_column')

    # c) drift: every trial pair must be one EXACT's vocab produces
    foreign = sorted(_distinct_elements('omop_languages_skills_required') - set(producible.values()))
    if foreign:
        reasons.append(
            f'drift: omop_languages_skills_required holds pair(s) EXACT\'s vocab cannot produce: '
            f'{", ".join(foreign)}; CB and EXACT language mappings differ')

    # warning: languages PROMOP never unrolls
    other_languages = sorted({
        code.partition('__')[2] for code in _distinct_elements('languages_skills_required')
        if '__' in code and code.partition('__')[2] not in REQUIRED_LANGUAGES
    })
    if other_languages:
        warnings.append(
            f'trial languages beyond en/es read as unknown (PROMOP unrolls only en/es): '
            f'{", ".join(other_languages)}')

    return ReadinessReport(ok=not reasons, reasons=reasons, warnings=warnings)


def languages_ready():
    """Cached readiness for the gate; logs an ERROR when a fresh check is not ready."""
    now = time.monotonic()
    if _cache['report'] is not None and now - _cache['at'] < READINESS_TTL_SECONDS:
        return _cache['report'].ok
    report = check_languages_readiness()
    _cache['at'], _cache['report'] = now, report
    if not report.ok:
        logger.error(
            'EXACT_OMOP_LANGUAGES is on but language OMOP data is not ready; '
            'matching uses the legacy path: %s', '; '.join(report.reasons))
    return report.ok


def reset_readiness_cache():
    """Forget the cached result (tests; after loading or backfilling in-process)."""
    _cache['at'] = _cache['report'] = None
