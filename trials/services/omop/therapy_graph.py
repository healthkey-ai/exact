"""Derive a patient's component + type match-values from their regimen therapies
via the internal CB graph (Therapy → TherapyComponent → TherapyComponentCategory).

The matcher and the search queryset both need, from the patient's regimen-line
therapies, the drug-component and drug-class (type) values to overlap against the
trial columns. This is the single shared derivation so the two stay consistent.

Vocabulary by level (see therapy_match_profile):
- regimen: handled by the caller directly (concept_ids under OMOP / codes legacy).
- component: OMOP-mapped — under EXACT_OMOP_THERAPY the patient's regimen
  concept_ids are reverse-mapped to internal Therapies (via Therapy.omop_concept_id),
  walked to their components, and the components' OMOP concept_ids are returned
  (to overlap the omop_therapy_components_* columns). Legacy → internal codes.
- type / component-category: NOT OMOP-mapped — always returned as CB category
  CODES (to overlap the legacy therapy_types_* columns), because EXACT keeps CB's
  own category vocabulary and matches types through this graph (#197).

Returns ``(component_values, type_values)``; ``(None, None)`` when the patient
has no resolvable regimens (preserves the matcher's "unknown" semantics).
"""
from trials.services.therapy_match_profile import omop_therapy_enabled


def resolve_regimens(therapy_identifiers):
    """Patient regimen identifiers → internal Therapy queryset.

    Under OMOP the identifiers are concept_ids → match Therapy.omop_concept_id;
    legacy → match Therapy.code.
    """
    from trials.models import Therapy
    if omop_therapy_enabled():
        # concept_ids are non-negative ints; isdigit() also keeps int() safe
        cids = [int(v) for v in therapy_identifiers if str(v).isdigit()]
        return Therapy.objects.filter(omop_concept_id__in=cids) if cids else Therapy.objects.none()
    return Therapy.objects.filter(code__in=therapy_identifiers)


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

    if omop_therapy_enabled():
        component_values = [str(c.omop_concept_id) for c in components if c.omop_concept_id is not None]
    else:
        component_values = [c.code for c in components]
    # types are not OMOP-mapped — CB category codes, both modes
    type_values = [cat.code for cat in categories]
    return component_values, type_values
