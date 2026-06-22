"""Translate a patient's internal therapy codes to OMOP concept_ids for matching.

The OMOP cutover flips the TRIAL therapy columns from internal codes to OMOP
concept_id strings (see therapy_match_profile). For matching to still work the
PATIENT side must speak the same language: when ``EXACT_OMOP_THERAPY`` is on, the
patient's vocab codes (regimen / drug component / drug class) are mapped to the
same concept_id strings via the vocab models' ``omop_concept_id`` before any
overlap test (queryset ``has_any_keys`` filters + the per-trial matcher).

This is the EXACT-specific counterpart to the trial-side profile — CB has no
patient side (its PatientInfo is stateless and lives in EXACT). Both the trial
column names and these patient codes flip on the same flag.

When the flag is off these are pass-throughs (zero behavior change). Unmapped /
unknown codes are dropped — the same cutover-divergence the shadow-compare report
surfaces on the trial side.
"""
from trials.services.therapy_match_profile import omop_therapy_enabled


def to_match_codes(model, codes):
    """Translate a list of patient vocab codes to the values used for matching.

    Under OMOP: returns sorted, de-duplicated OMOP concept_id strings (unmapped
    codes dropped). Off: returns ``codes`` unchanged. ``None``/empty pass through
    untouched so the matcher's "unknown" (None) vs "no match" ([]) logic is
    preserved.
    """
    if not omop_therapy_enabled() or not codes:
        return codes
    from trials.services.omop.therapy_concept_mapper import map_codes_to_concept_ids
    concept_ids, _unmapped = map_codes_to_concept_ids(model, list(codes))
    return concept_ids


def to_match_value_map(model, code_to_title):
    """Translate a ``{code: title}`` display map to ``{match_value: title}``.

    Under OMOP the keys become concept_id strings (unmapped codes dropped) so the
    match-details builder compares against the same concept_ids the trial columns
    hold. Off: returns the map unchanged.
    """
    if not omop_therapy_enabled() or not code_to_title:
        return code_to_title
    code_to_cid = dict(
        model.objects.filter(code__in=list(code_to_title)).values_list('code', 'omop_concept_id')
    )
    return {
        str(cid): code_to_title[code]
        for code, cid in code_to_cid.items()
        if cid is not None
    }
