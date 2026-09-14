"""Derive a patient's component + type match-values from their regimen therapies
via the internal CB graph (Therapy → TherapyComponent → TherapyComponentCategory).

The matcher and the search queryset both need, from the patient's regimen-line
therapies, the drug-component and drug-class (type) values to overlap against the
trial columns. This is the single shared derivation so the two stay consistent.

Vocabulary by level (see therapy_match_profile):
- regimen: handled by the caller directly (concept_ids under OMOP / codes legacy).
- component: OMOP mode (Phase P, #234) — the patient's component concept_ids are
  supplied PRE-EXPANDED by the consumer (promop) via ``patient_component_ids``;
  EXACT no longer reverse-maps regimens to components through the local CB graph.
  Legacy mode → internal component codes via the CB graph (unchanged).
- type / component-category: NOT OMOP-mapped — always returned as CB category
  CODES (to overlap the legacy therapy_types_* columns), because EXACT keeps CB's
  own category vocabulary and matches types through the flat category lookup (#197).

Returns ``(component_values, type_values)``. ``(None, None)`` means unknown
(preserves the matcher's "unknown" semantics): OMOP → the consumer sent no
``therapy_component_ids`` (``patient_component_ids is None``); legacy → the patient
has no resolvable regimens. An empty list is a known-empty component set.
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


def derive_component_and_type_values(therapy_identifiers, patient_component_ids=None,
                                     measure=False, patient_class_ids=None):
    """Return ``(component_values, type_values)`` for the patient's therapies.

    OMOP mode (Phase P, #234): components are the consumer-supplied pre-expanded
    ``patient_component_ids`` (no local CB-graph walk). ``None`` = unknown (consumer
    sent nothing) → ``(None, None)``; ``[]`` = known-empty; a list → those component
    concept_ids.

    Types (drug-class) under OMOP (folded into the base flag, #285 / promop ADR 0002):
    the consumer-supplied PRE-EXPANDED class concept_ids ``patient_class_ids`` — ``None``
    = unknown (fail-closed downstream), ``[]`` = known-empty, a list → those class
    concept_ids. Matched by overlap against ``omop_therapy_types_*``. (The old
    component→CB-category lookup type path is retired under OMOP; promop pre-expands
    component→class per ADR 0004.)

    ``therapy_identifiers`` is used only by the legacy path.

    Legacy mode (OMOP therapy flag OFF): the internal CB-graph expansion —
    byte-identical to CB.

    ``measure=True`` records the Phase-T gating metric (#263) when this call is the
    per-search derivation (the search queryset passes it) — a regimen present under
    OMOP with no consumer-supplied components. The matcher leaves it ``False`` so the
    signal is emitted once per search, not once per trial scored. Observation only —
    the return value is identical either way.
    """
    if omop_therapy_enabled():
        # Types are folded into the base OMOP flag (#285): drug-class types are the
        # consumer's PRE-EXPANDED class concept_ids (patient_class_ids), matched against
        # omop_therapy_types_*. The old component->CB-category lookup type path (mode 2)
        # is retired under OMOP — promop pre-expands component->class (ADR 0004).
        if patient_component_ids is None:
            if measure and therapy_identifiers:
                from trials.services.omop.phase_t_metrics import record_regimen_unresolved
                record_regimen_unresolved(therapy_identifiers)
            # Components unknown -> component_values None. Types are independent: a
            # class-only patient (class ids but no components — the #224 fold routes such
            # patients here) still derives its types from the class ids, so a
            # required-class trial isn't false-negatived.
            if patient_class_ids is not None:
                return None, [str(cid) for cid in patient_class_ids]
            return None, None
        component_values = [str(cid) for cid in patient_component_ids]
        # Types = the patient's pre-expanded class concept_ids as-is. Preserve None
        # (consumer sent no class ids -> unknown -> fail-closed) vs [] (known-empty).
        type_values = None if patient_class_ids is None else [str(cid) for cid in patient_class_ids]
        return component_values, type_values

    # ── legacy (flag OFF): internal CB-graph expansion — byte-identical to CB ──
    if not therapy_identifiers:
        return None, None
    from trials.models import TherapyComponent, TherapyComponentCategory

    therapies = resolve_regimens(therapy_identifiers)
    if not therapies.exists():
        return None, None

    components = TherapyComponent.objects.filter(
        therapycomponentconnection__therapy__in=therapies
    ).order_by('id')
    component_values = [c.code for c in components]
    categories = TherapyComponentCategory.objects.filter(
        therapycomponentcategoryconnection__component__in=components
    ).order_by('id')
    type_values = [cat.code for cat in categories]

    return component_values, type_values
