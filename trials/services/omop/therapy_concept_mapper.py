"""Map a trial's legacy therapy codes to OMOP concept_id strings (epic #4447, Phase 3).

Source of truth: the vocab models' ``omop_concept_id`` (#4451). Destination: the
trial ``omop_*`` columns (#4453). This module is the pure conversion logic shared
by the batch backfill command (and the shadow-comparison harness #4446).

Ported from CancerBot (CB epic #4447); CB owns the upstream conversion. EXACT is
the downstream that runs the backfill locally / against CB's read-only trials DB.

Scope: the three therapy levels whose vocab has ``omop_concept_id`` today —
regimen (``Therapy``), drug component (``TherapyComponent``), drug class
(``TherapyComponentCategory``). ``planned_*`` / ``supportive_*`` are excluded
until their vocabs gain ``omop_concept_id``.

Conventions (per the plan review): concept_ids are stored as STRINGS; output
arrays are de-duplicated and stably sorted; unknown or unmapped (null
``omop_concept_id``) codes are dropped and reported; the mapping is idempotent.
"""
from trials.models import Therapy, TherapyComponent, TherapyComponentCategory

# (vocab model, legacy required col, legacy excluded col, omop required col, omop excluded col)
THERAPY_LEVELS = [
    (Therapy,
     'therapies_required', 'therapies_excluded',
     'omop_therapies_required', 'omop_therapies_excluded'),
    (TherapyComponent,
     'therapy_components_required', 'therapy_components_excluded',
     'omop_therapy_components_required', 'omop_therapy_components_excluded'),
    (TherapyComponentCategory,
     'therapy_types_required', 'therapy_types_excluded',
     'omop_therapy_types_required', 'omop_therapy_types_excluded'),
]


def map_codes_to_concept_ids(model, codes):
    """Map vocab codes to OMOP concept_id strings.

    Returns ``(concept_ids, unmapped)`` where ``concept_ids`` is a sorted,
    de-duplicated list of stringified concept_ids and ``unmapped`` is the sorted,
    de-duplicated list of input codes that are unknown to the vocab or whose
    ``omop_concept_id`` is null.
    """
    if not codes:
        return [], []

    code_to_cid = dict(
        model.objects.filter(code__in=codes).values_list('code', 'omop_concept_id')
    )
    concept_ids = set()
    unmapped = set()
    for code in codes:
        cid = code_to_cid.get(code)
        if cid is None:  # unknown code OR null omop_concept_id
            unmapped.add(code)
        else:
            concept_ids.add(str(cid))
    return sorted(concept_ids), sorted(unmapped)


def build_omop_columns(trial):
    """Compute the trial's OMOP therapy column values from its legacy codes.

    Returns ``(values, unmapped)``:
    - ``values``: ``{omop_column_name: [concept_id_str, ...]}`` for all 6 mapped
      columns (3 levels x required/excluded).
    - ``unmapped``: ``{legacy_column_name: [code, ...]}`` for codes that could not
      be mapped (only non-empty entries included).
    """
    values = {}
    unmapped = {}
    for model, legacy_req, legacy_exc, omop_req, omop_exc in THERAPY_LEVELS:
        for legacy_col, omop_col in ((legacy_req, omop_req), (legacy_exc, omop_exc)):
            concept_ids, missing = map_codes_to_concept_ids(model, getattr(trial, legacy_col))
            values[omop_col] = concept_ids
            if missing:
                unmapped[legacy_col] = missing
    return values, unmapped


# Columns this mapper owns, in a stable order — used by the backfill command's
# change detection.
OMOP_COLUMNS = [omop_col for _, _, _, r, e in THERAPY_LEVELS for omop_col in (r, e)]
