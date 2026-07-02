"""Map patient component concept_ids to CB category codes for type matching (ADR 0001).

Under the minimal-EXACT design, EXACT no longer reverse-resolves regimen concept_ids
through the internal therapy graph.  Instead, the patient's component concept_ids arrive
directly from CTOMOP and types are resolved in one flat lookup:

    component_concept_id  →  TherapyComponent.omop_concept_id
                          →  TherapyComponentCategoryConnection
                          →  TherapyComponentCategory.code  (CB category code)

The result overlaps the legacy ``therapy_types_*`` columns (CB category codes, unchanged).
Returns ``None`` when concept_ids is None (unknown → fail-closed); returns ``[]`` when the
lookup yields no categories (known-empty → no types to match).
"""


def component_concept_ids_to_type_codes(concept_ids):
    """Patient component concept_ids → list of CB category codes for type matching.

    Returns None when concept_ids is None.  Returns [] when the lookup yields
    nothing (patient has no resolvable types).
    """
    if concept_ids is None:
        return None
    if not concept_ids:
        return []

    from trials.models import TherapyComponent
    cids = [int(v) for v in concept_ids if str(v).isdigit()]
    if not cids:
        return []

    components = TherapyComponent.objects.filter(omop_concept_id__in=cids).prefetch_related('categories')
    codes = []
    seen = set()
    for comp in components.order_by('id'):
        for cat in comp.categories.all():
            if cat.code not in seen:
                seen.add(cat.code)
                codes.append(cat.code)
    return codes
