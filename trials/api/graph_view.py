from __future__ import annotations

from typing import Any, Dict, List

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response

from trials.api.graph_serializers import GraphTrialNodeSerializer
from trials.api.patient_info_serializers import PatientInfoSerializer
from trials.api.trials_views import TrialsViewSet
from trials.services.attribute_names import AttributeNames
from trials.services.trial_details.trial_templates import TrialTemplates


def _normalize_item_for_ui(item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "trialField": item.get("name"),
        "patientField": item.get("ufield"),
        # The same attribute, canonical (snake_case) form. This endpoint
        # rebuilds each row from a whitelist, so without naming it here its
        # consumer is left deriving the name by hand — and the derivation
        # is not safe: `p53_ihc` camelizes to `p53Ihc`, which a standard
        # snake-caser turns back into `p_53_ihc`, a field nobody has.
        #
        # NOT `patientRecordField`, which is what this said first: EXACT
        # cannot assert a column exists on PROMOP's PatientRecord, and a
        # key that implied otherwise would put this service's authority
        # behind a PATCH that answers 200 and changes nothing. The count
        # and the reasoning live at `TrialAttributes.with_patient_field_names`.
        "patientFieldCanonical": item.get("upatientField"),
        # Whether EXACT recomputes it, i.e. whether a write upstream
        # survives to the next match (#449).
        "patientFieldRecomputed": item.get("upatientRecomputed"),
        # The same answer with its reason. This endpoint rebuilds the row
        # from a whitelist, so a key left out is a key its consumer cannot
        # tell from "this row is about nothing" — and `null` is also what
        # an un-recomputed field answers, which makes a missing one the
        # hardest kind to notice.
        "patientFieldRecomputedWhen": item.get("upatientRecomputedWhen"),
        # Carried for the same reason as the name: this endpoint rebuilds
        # the row, so a client here would otherwise not know the value sits
        # behind a subform.
        #
        # Named for the mechanism, not for permission. `patientFieldReadOnly`
        # was the first spelling, and `false` there reads as "you may edit
        # this" — which is the answer EXACT has just finished explaining it
        # does not have (#449): `renal_adequacy_status` has no subform and
        # is still overwritten by `normalize.py` on every match.
        #
        # Passed through rather than coerced, so a builder that did not
        # say arrives as `null` rather than `false`. `bool(...)` would turn
        # "unsaid" into "no subform", which is the wrong direction to fail
        # in and the opposite of what the key beside it does.
        # The subform signal is the subform itself, not `ureadonly`: that flag
        # also comes from `is_computed_value` (FL `meets_gelf` / `meets_lugano`
        # are read-only rows with no subform entries), and a client reading it
        # as "this opens a dialog" would offer a door with nothing behind it.
        "patientFieldHasSubform": (
            bool(item.get("subform_details")) if item.get("upatientField") else None
        ),
        "label": item.get("label"),
        "trialValue": item.get("value"),
        # The comparison beside `trialValue` is in the matcher's unit. The
        # detail row's `uvalue` is now the patient's STORED value when units
        # differ, so use the canonical one it carries for that comparison; a
        # raw 120 g/L against a 9 g/dL minimum must not read as 120 vs 9.
        "patientValue": item.get("uvalue_canonical", item.get("uvalue")),
        "dependencies": item.get("dependencies", []),
        "dependencies_labels": [AttributeNames.humanize(x) for x in item.get("dependencies", [])],
    }


def _bucket_by_matching_type(details: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {"matched": [], "notMatched": [], "missing": []}

    for raw in (details.get("trialEligibilityAttributes") or []):
        item = _normalize_item_for_ui(raw)
        mt_raw = raw.get("matchingType")
        mt = (str(mt_raw).strip().lower() if mt_raw is not None else "")

        if mt in {"unsupported", "unavailable"}:
            buckets.setdefault('systemUnavailable', []).append(item)
        elif mt == "matched":
            buckets["matched"].append(item)
        elif mt in {"not matched", "not_matched", "not-matched", "notmatched"}:
            buckets["notMatched"].append(item)
        else:
            buckets["missing"].append(item)

    key_fn = lambda x: (x.get("label") or "").lower()
    for k in buckets:
        buckets[k].sort(key=key_fn)

    return buckets


class GraphPatientInfoSerializer(PatientInfoSerializer):
    """Strip the heavy fields from PatientInfoSerializer for the graph endpoint."""

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data.pop('details', None)
        return data


class TrialsGraphViewSet(TrialsViewSet):
    """
    Reuses TrialsViewSet queryset logic to build a graph-oriented response.
    """
    # POST as well as GET, for the alias below. The GET form stays exactly as
    # it was for the callers that already speak it.
    http_method_names = ["get", "post"]

    # The inherited POST aliases, removed. `TrialsViewSet` carries `match`,
    # `search_match` and `match_detail`, and widening `http_method_names`
    # published all three under `/trials-graph/` — an untested second copy of
    # the trials API at a URL nothing asked for. Setting an inherited action to
    # None is how DRF's router is told to skip it.
    match = None
    search_match = None
    match_detail = None
    # Restored with #692; the graph has its own `graph/match` alias, and an
    # inherited CSV export at /trials-graph/export/ is a second surface nobody
    # asked for.
    export = None

    @action(methods=["get"], detail=False, url_path="graph", url_name="graph")
    def graph(self, request, *args, **kwargs):
        patient_info = self._resolve_patient_info()
        if patient_info is None:
            return Response(
                {"detail": "Graph view requires patient context "
                           "(provide person_id or inline patient_info)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            n = int(request.query_params.get("n", 50))
        except (TypeError, ValueError):
            return Response(
                {"detail": "Query param 'n' must be an integer."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        n = max(1, min(n, 200))

        prev_action = getattr(self, "action", None)
        self.action = "search"
        try:
            qs = self.filter_queryset(self.get_queryset(patient_info=patient_info))[:n]
        finally:
            self.action = prev_action

        trials = list(qs)

        ctx = self.get_serializer_context()
        ctx.update({
            "patient_info": patient_info,
            "base_url": getattr(settings, 'BASE_URL', ''),
        })

        search_type = ctx.get("search_type")
        counts = ctx.get("counts") or {}

        trial_nodes: List[Dict[str, Any]] = []
        for trial in trials:
            node = GraphTrialNodeSerializer(trial, context=ctx).data

            evaluation = ctx.get('typed_evaluation')
            if evaluation is not None:
                from trials.services.matching.typed_runtime import details, missing_attributes
                result = evaluation.result_for(trial.pk)
                node.update(result.summary())
                node['attributesToFillIn'] = missing_attributes(result, counts)
                node['match'] = _bucket_by_matching_type(details(evaluation, trial)['details'])
                trial_nodes.append(node)
                continue

            if search_type == "eligible" or getattr(trial, "match_score", None) == 100:
                attrs_to_fill_in = []
            else:
                attrs_to_fill_in = trial.attrs_to_fill_in(counts, patient_info=patient_info)

            tt = TrialTemplates(trial, patient_info)
            details = tt.potential_attributes_first_view(attrs_to_fill_in=attrs_to_fill_in)

            node["match"] = _bucket_by_matching_type(details)
            trial_nodes.append(node)

        return Response({
            "patient": GraphPatientInfoSerializer(patient_info).data,
            "trials": trial_nodes,
        })

    @action(methods=["post"], detail=False, url_path="graph/match", url_name="graph-match")
    def graph_match(self, request, *args, **kwargs):
        """POST alias for `graph`, carrying `patient_info` in the body.

        The graph refuses to draw without a patient, and the two ways to supply
        one are an inline payload — which only travels in a body, since
        GET-with-body is forbidden by the Fetch spec and dropped by axios's XHR
        adapter — or `?person_id=`, which is gated off outside DEBUG because it
        would let any authenticated caller enumerate another patient's record
        (#150/#108). So the GET form is reachable in local development and
        essentially nowhere else, and the federated remote could not call this
        endpoint at all.

        Mirrors `search_match`: bind the action so `get_queryset` runs the
        `search` branch, then delegate.
        """
        self.action = "graph"
        return self.graph(request, *args, **kwargs)
