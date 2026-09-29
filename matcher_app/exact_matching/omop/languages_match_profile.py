"""Language-skill column/field names used by matching — OMOP cutover seam (CB #5350).

Same pattern as ``therapy_match_profile``: two profiles, picked by a setting
(``EXACT_OMOP_LANGUAGES``, off by default) on every attribute access, so tests can
toggle it with ``override_settings``. Nothing else changes between them.

The profile names two things, one per side:

- ``languages_skills_required`` — the TRIAL column. Legacy
  ``languages_skills_required`` holds CB codes (``speak__en``); OMOP
  ``omop_languages_skills_required`` holds ``"<language_concept_id>:<skill_concept_id>"``
  pairs, filled by CB (``backfill_omop_languages_skills_column``).
- ``patient_languages_skills`` — the PATIENT attribute. Legacy ``languages_skills``
  (a comma-separated code string); OMOP ``language_skill_concept_ids``, a list of the
  same pair strings. EXACT builds it at resolve time from PROMOP's
  ``english_*`` / ``spanish_*`` capability booleans
  (``trials.services.omop.patient_languages``), a deliberate exception to "EXACT owns
  no patient crosswalk": the ids come from EXACT's own mapping CSV, the same rows the
  trial column is built from, and trials use only en/es. Only speak/write produce
  pairs (interim; see that module).

Surfaces that read it — a cutover flips all three together, which is why they go
through this one profile rather than literals:

1. the search queryset, ``TrialQuerySet.eligible_for_languages_skills`` (trial column);
2. ``PatientInfoAttributes.get_value('languages_skills')`` (patient value). It feeds
   the queryset dispatch, the blank check both paths share, and the matcher;
3. the per-trial matcher, ``UserToTrialAttrMatcher._match_languages_skills`` (trial
   column). With the flag off it delegates to the generic computed handler, so the
   ``USER_TO_TRIAL_ATTRS_MAPPING['languages_skills']`` entry (``attr`` +
   ``uvalue_function``) keeps driving legacy matching unchanged.

The count / blank-check SQL (``UserToTrialAttrsMapper``: potential counts, eligible
vs potential status, attrs-to-fill-in) reads the same column via ``_trial_column``.

Not covered, and a cutover must flip them too: trial-detail / display configs that
read ``languages_skills_required``, and the match-reason output, which under the
flag pairs the patient value (joined concept pairs) with the legacy trial
requirement, i.e. two vocabularies side by side.
"""
from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class LanguagesMatchProfile:
    languages_skills_required: str = 'languages_skills_required'
    patient_languages_skills: str = 'languages_skills'


LEGACY_LANGUAGES_MATCH_PROFILE = LanguagesMatchProfile()

OMOP_LANGUAGES_MATCH_PROFILE = LanguagesMatchProfile(
    languages_skills_required='omop_languages_skills_required',
    patient_languages_skills='language_skill_concept_ids',
)


def omop_languages_enabled() -> bool:
    """Whether language-skill matching reads the OMOP pair column / patient field.

    ``EXACT_OMOP_LANGUAGES`` AND the data is ready
    (``trials.services.omop.languages_readiness``: vocab loaded, trials backfilled,
    no CB<->EXACT id drift). Not ready -> the legacy path, with an ERROR log. The
    readiness module is imported only when the setting is on, so the legacy path
    stays import-free (as the therapy release gates do). Every surface (queryset,
    matcher, count SQL, attrs-to-fill-in, patient builder) reads the flag here.
    """
    if not getattr(settings, 'EXACT_OMOP_LANGUAGES', False):
        return False
    from trials.services.omop.languages_readiness import languages_ready
    return languages_ready()


def get_languages_match_profile() -> LanguagesMatchProfile:
    """Return the active profile for the current setting."""
    if not omop_languages_enabled():
        return LEGACY_LANGUAGES_MATCH_PROFILE
    return OMOP_LANGUAGES_MATCH_PROFILE


class _ActiveLanguagesMatchProfile:
    """Settings-aware, read-only view of the active profile (see therapy_match_profile)."""
    __slots__ = ()

    def __getattr__(self, name):
        return getattr(get_languages_match_profile(), name)

    def __setattr__(self, name, value):
        raise AttributeError(
            "LANGUAGES_MATCH_PROFILE is read-only; set EXACT_OMOP_LANGUAGES to switch profiles."
        )


LANGUAGES_MATCH_PROFILE = _ActiveLanguagesMatchProfile()
