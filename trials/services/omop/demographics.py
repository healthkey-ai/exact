"""CB demographics (gender, ethnicity) -> OMOP concept_id.

Part of the OMOP migration (epic #4447). concept_ids are verified present in the
PROMOP release (LOINC 'Answer' concepts, ``Meas Value`` domain — the gender/race
concepts loaded in this PROMOP).

CB has NO separate race field; its ``ethnicity`` field holds race categories, so
ethnicity maps to OMOP RACE concepts. ``other`` has no clean OMOP race concept and
is intentionally left unmapped (SME review). CB gender is a fixed enum
(GenderChoices), not a vocab table, so its mapping lives here as a constant
(consumed by the trial-side backfill); ethnicity is a vocab table, so its mapping
is loaded onto Ethnicity.omop_concept_id by ``load_ethnicity_omop_concept_ids``.

Ported from CancerBot; CB owns the upstream mapping. EXACT reads/populates it locally.
"""

# GenderChoices code -> OMOP concept_id
GENDER_OMOP_CONCEPT_ID = {
    'M': 45880669,    # Male
    'F': 45878463,    # Female
    'UN': 45877986,   # Unknown
    # '' (empty) -> no requirement / no concept
}

# Ethnicity.code -> OMOP race concept_id
ETHNICITY_OMOP_CONCEPT_ID = {
    'caucasian_or_european': 45877987,  # White
    'african_or_black': 45877988,       # Black or African American
    'asian': 45879439,                  # Asian
    'native_american': 45877442,        # American Indian or Alaska Native
    # 'other' -> no clean OMOP race concept (needs SME)
}


def build_omop_demographics(trial):
    """Compute a trial's OMOP demographics columns from its legacy fields.

    Reads ethnicity concept_ids from the Ethnicity vocab (omop_concept_id, loaded
    by load_ethnicity_omop_concept_ids) and gender from GENDER_OMOP_CONCEPT_ID.

    Returns ``(values, unmapped)``:
    - ``values``: ``{'omop_ethnicity_required': [concept_id_str, ...],
      'omop_gender_concept_id': int | None}``. Ethnicity ids are de-duped, stably
      sorted, stringified (mirrors the omop_therapies_* JSONB convention). Gender
      is a single concept (None = no requirement / unmapped).
    - ``unmapped``: ethnicity codes dropped because they're unknown or have no
      omop_concept_id (e.g. 'other').
    """
    from trials.models import Ethnicity

    codes = trial.ethnicity_required or []
    code_to_cid = dict(
        Ethnicity.objects.filter(code__in=codes)
        .exclude(omop_concept_id__isnull=True)
        .values_list('code', 'omop_concept_id')
    )
    omop_ethnicity = sorted({str(code_to_cid[c]) for c in codes if c in code_to_cid})
    unmapped = sorted({c for c in codes if c not in code_to_cid})

    gender = trial.gender or ''
    gender_cid = GENDER_OMOP_CONCEPT_ID.get(gender)  # None for '' (no requirement) or any unmapped value

    values = {'omop_ethnicity_required': omop_ethnicity, 'omop_gender_concept_id': gender_cid}
    return values, unmapped


# Columns this mapper owns (for backfill update_fields / change detection).
OMOP_DEMOGRAPHICS_COLUMNS = ['omop_ethnicity_required', 'omop_gender_concept_id']
