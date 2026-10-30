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
    def build_therapy_display_maps(self, therapy_codes, get_component_ids, omop,
                                   get_class_ids=None, omop_types=False) -> tuple[dict, dict, dict]:
        """Return (therapies, therapy_components_to_therapy, therapy_types_to_therapy)
        display maps keyed per the active therapy profile.

        `get_component_ids` / `get_class_ids` are zero-arg callables returning the
        patient's component / drug-class concept_ids; invoked lazily (only in OMOP
        mode) to preserve the original DB-query emission order. Under `omop_types`
        (#285) the type map is keyed by the patient's class concept_ids.
        """
        ...

    def derive_component_and_type_values(self, values, patient_component_ids, patient_class_ids=None) -> tuple:
        """Return (component_codes, therapy_types) for the patient's therapies.

        ``patient_class_ids`` supplies the patient's pre-expanded drug-class "type"
        concept_ids (used as type_values under the OMOP flag; #285 folded types in).
        """
        ...


class DjangoMatcherData:
    """Default EXACT implementation — 1:1 with the matcher's previous inline code."""

    def build_therapy_display_maps(self, therapy_codes, get_component_ids, omop,
                                   get_class_ids=None, omop_types=False):
        from trials.services.omop.therapy_graph import resolve_regimens

        therapies = {}                       # regimen match-value -> title
        therapy_components_to_therapy = {}   # component match-value -> title
        therapy_types_to_therapy = {}        # class concept_id -> title (#285 folded types into OMOP)

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
                from trials.models import TherapyComponent
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
                # (The old component->CB-category lookup TYPE display path is retired —
                # types are folded into the base OMOP flag, #285; the type display map is
                # built from the patient's class concept_ids below.)

            if omop_types:
                # OMOP types (#285): keys are the patient's class concept_ids as-is
                # (matched against omop_therapy_types_*). Titles from OmopConcept when
                # present, else the raw id — never None (match_required sorts values).
                from trials.models import OmopConcept
                class_ids = (get_class_ids() if get_class_ids else None) or []
                class_keys = [str(c) for c in class_ids]
                int_cids = [int(k) for k in class_keys if k.isdigit()]
                title_by_cid = dict(
                    OmopConcept.objects.filter(concept_id__in=int_cids)
                    .values_list('concept_id', 'concept_name')
                )
                for key in class_keys:
                    title = title_by_cid.get(int(key)) if key.isdigit() else None
                    therapy_types_to_therapy.setdefault(key, title or key)

        return therapies, therapy_components_to_therapy, therapy_types_to_therapy

    def derive_component_and_type_values(self, values, patient_component_ids, patient_class_ids=None):
        from trials.services.omop.therapy_graph import derive_component_and_type_values as _derive
        return _derive(values, patient_component_ids, patient_class_ids=patient_class_ids)
