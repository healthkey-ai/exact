"""Data-access port for the matcher's model/service queries (E1.2b).

The per-trial matcher's therapy path reaches a small set of EXACT models +
`trials.services.omop` helpers (regimen expansion, component/category titles,
class derivation). Extracting them behind this port removes those hardcoded
model/service reads from the matcher, so a future host (CB) can inject its own
data source instead of EXACT's `trials.*`.

`DjangoMatcherData` is the default EXACT implementation and reproduces the
previous inline matcher logic byte-for-byte (same lazy imports, same queries).
It is injected by default (`UserToTrialAttrMatcher(..., data=None)`), so all
existing callers are unaffected.

NOTE: `_resolve_omop_concepts` (vocab-mirror title lookup, presentation-only,
fails soft, never affects eligibility) is NOT yet behind this port — a follow-up
(E1.2b-cont) routes it too.
"""
from __future__ import annotations
from typing import Protocol


class MatcherDataPort(Protocol):
    def build_therapy_display_maps(self, therapy_codes, get_component_ids, omop) -> tuple[dict, dict, dict]:
        """Return (therapies, therapy_components_to_therapy, therapy_types_to_therapy)
        display maps keyed per the active therapy profile.

        `get_component_ids` is a zero-arg callable returning the patient's
        component concept_ids; it is invoked lazily (only in OMOP mode, after the
        regimen query) to preserve the original DB-query emission order.
        """
        ...

    def derive_component_and_type_values(self, values, patient_component_ids) -> tuple:
        """Return (component_codes, therapy_types) for the patient's therapies."""
        ...


class DjangoMatcherData:
    """Default EXACT implementation — 1:1 with the matcher's previous inline code."""

    def build_therapy_display_maps(self, therapy_codes, get_component_ids, omop):
        from trials.services.omop.therapy_graph import resolve_regimens

        therapies = {}                       # regimen match-value -> title
        therapy_components_to_therapy = {}   # component match-value -> title
        therapy_types_to_therapy = {}        # CB category code -> title (types not OMOP-mapped)

        if therapy_codes:
            for therapy in resolve_regimens(therapy_codes).prefetch_related('components__categories'):
                if omop:
                    if therapy.omop_concept_id is not None:
                        therapies[str(therapy.omop_concept_id)] = therapy.title
                else:
                    therapies[therapy.code] = therapy.title
                    for component in sorted(therapy.components.all(), key=lambda c: c.id):
                        therapy_components_to_therapy.setdefault(component.code, component.title)
                        for category in component.categories.all():
                            therapy_types_to_therapy.setdefault(category.code, category.title)

        if omop:
            # OMOP component/type display maps from the consumer-supplied concept_ids
            # (no regimen->component graph walk). Titles resolved from the local
            # TherapyComponent / category tables (transitional, while they still exist).
            # Fetched here (after the regimen loop, only in OMOP mode) to keep the
            # original DB-query order.
            component_ids = get_component_ids() or []
            if component_ids:
                from trials.models import TherapyComponent, TherapyComponentCategory
                from trials.services.omop.component_category_lookup import component_concept_ids_to_type_codes
                keys = [str(c) for c in component_ids]
                int_cids = [int(k) for k in keys if k.isdigit()]
                title_by_cid = dict(
                    TherapyComponent.objects.filter(omop_concept_id__in=int_cids)
                    .values_list('omop_concept_id', 'title')
                )
                for key in keys:
                    # Fall back to the concept_id string when EXACT has no local title
                    # (promop may supply concepts EXACT holds no local row for) — a None
                    # value here later TypeErrors in match_required's sorted(set(values)).
                    title = title_by_cid.get(int(key)) if key.isdigit() else None
                    therapy_components_to_therapy.setdefault(key, title or key)
                type_codes = component_concept_ids_to_type_codes(keys) or []
                if type_codes:
                    title_by_code = dict(
                        TherapyComponentCategory.objects.filter(code__in=type_codes)
                        .values_list('code', 'title')
                    )
                    for code in type_codes:
                        therapy_types_to_therapy.setdefault(code, title_by_code.get(code) or code)

        return therapies, therapy_components_to_therapy, therapy_types_to_therapy

    def derive_component_and_type_values(self, values, patient_component_ids):
        from trials.services.omop.therapy_graph import derive_component_and_type_values as _derive
        return _derive(values, patient_component_ids)
