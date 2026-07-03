"""Derive a patient's component + type match-values from their regimen therapies
via the internal CB graph (Therapy → TherapyComponent → TherapyComponentCategory).

LEGACY MODE ONLY.  Under EXACT_OMOP_THERAPY the patient's component concept_ids
arrive directly from CTOMOP (promop#189) and types are resolved via
``component_category_lookup.component_concept_ids_to_type_codes``.  This module
is NOT called in OMOP mode; callers must check ``omop_therapy_enabled()`` and
dispatch accordingly.

Returns ``(component_values, type_values)``; ``(None, None)`` when the patient
has no resolvable regimens (preserves the matcher's "unknown" semantics).
"""


def resolve_regimens(therapy_identifiers):
    """Patient regimen codes → internal Therapy queryset (legacy: Therapy.code)."""
    from trials.models import Therapy
    return Therapy.objects.filter(code__in=therapy_identifiers) if therapy_identifiers else Therapy.objects.none()


def derive_component_and_type_values(therapy_identifiers):
    if not therapy_identifiers:
        return None, None
    from trials.models import TherapyComponent, TherapyComponentCategory

    therapies = resolve_regimens(therapy_identifiers)
    if not therapies.exists():
        return None, None

    components = TherapyComponent.objects.filter(
        therapycomponentconnection__therapy__in=therapies
    ).order_by('id')
    categories = TherapyComponentCategory.objects.filter(
        therapycomponentcategoryconnection__component__in=components
    ).order_by('id')

    component_values = [c.code for c in components]
    type_values = [cat.code for cat in categories]
    return component_values, type_values
