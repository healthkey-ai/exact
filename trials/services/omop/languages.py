"""CB language-skill requirement -> OMOP (language, skill) concept pair (#5350).

CB codes a requirement as ``<skill>__<language>`` (``speak__en``, ``write__es``;
built in ``ValueOptions.languages_skills`` from the ``LanguageSkillLevel`` x
``Language`` vocab). No single OMOP concept means "writes English", so the
trial column carries the same pair PROMOP stores on ``PersonLanguageSkill``:
the language as a SNOMED ``Language``-domain concept, the skill as PROMOP's
locally minted ``HK-Language`` value concept. Each pair is one JSONB string,
``"<language_concept_id>:<skill_concept_id>"``, so the existing
``eligible_for_required_lists`` overlap keeps its any-of meaning. Two separate
columns would not: a patient who speaks English and writes Spanish would
overlap both a language column holding Spanish and a skill column holding
speak, and so pass a "speaks Spanish" requirement.

The concept_ids live on the vocab rows (``Language.omop_concept_id``,
``LanguageSkillLevel.omop_concept_id``), loaded from
``docs/omop/mapping/language_omop_mapping.csv`` by
``load_language_omop_concept_ids`` (therapy CSV format). Order: the loader,
then ``backfill_omop_languages_skills_column``; see both commands' docstrings.
A code whose language or skill has no concept_id has no pair. That covers
``other`` (``no_omop``) and the time before the loader has run. The loader is
the only intended writer: a concept_id typed into a vocab row in admin is used
by syncs until the next loader run, which overwrites it with the CSV's value or
clears it. Trial columns change only when a sync or backfill runs.
"""


def load_concept_ids():
    """``({language_code: concept_id}, {skill_code: concept_id})`` for mapped vocab rows."""
    from trials.models import Language, LanguageSkillLevel

    def mapped(model):
        return dict(
            model.objects.exclude(omop_concept_id__isnull=True).values_list('code', 'omop_concept_id')
        )

    return mapped(Language), mapped(LanguageSkillLevel)


def language_skill_concept_key(code, concept_ids):
    """``speak__en`` -> ``'4180186:2100007853'``; None for anything unmapped.

    ``concept_ids`` is what ``load_concept_ids()`` returns.
    """
    if not isinstance(code, str):
        return None
    skill, sep, language = code.partition('__')
    if not sep:
        return None
    language_ids, skill_ids = concept_ids
    language_cid = language_ids.get(language)
    skill_cid = skill_ids.get(skill)
    if language_cid is None or skill_cid is None:
        return None
    return f'{language_cid}:{skill_cid}'


def build_omop_languages_skills(trial, concept_ids=None):
    """Compute a trial's ``omop_languages_skills_required`` from its legacy field.

    Returns ``(values, unmapped)``: ``values`` maps the column to its de-duped,
    sorted pair strings; ``unmapped`` lists, as strings, the codes dropped because
    they have no pair (``speak__other``, a spelling outside the vocab, a
    non-string element, a vocab row without a concept_id).

    A requirement whose codes are ALL unmapped comes out as ``[]``, which the
    legacy matcher reads as "no requirement": once reads flip to this column,
    such a trial would stop restricting by language. The backfill counts these
    trials; deciding how to represent them is cutover work (#5356).

    ``concept_ids`` lets a batch caller load the vocab once; by default it is
    read here.
    """
    codes = trial.languages_skills_required or []
    if not codes:
        return {'omop_languages_skills_required': []}, []
    if concept_ids is None:
        concept_ids = load_concept_ids()
    keyed = [(code, language_skill_concept_key(code, concept_ids)) for code in codes]
    pairs = sorted({key for _, key in keyed if key is not None})
    unmapped = sorted({str(code) for code, key in keyed if key is None})
    return {'omop_languages_skills_required': pairs}, unmapped


# Columns this mapper owns (for backfill change detection).
OMOP_LANGUAGES_COLUMNS = ['omop_languages_skills_required']
