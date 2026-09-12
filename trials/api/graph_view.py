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
        "patientFieldHasSubform": item.get("ureadonly"),
        "label": item.get("label"),
        "trialValue": item.get("value"),
        "patientValue": item.get("uvalue"),
        "dependencies": item.get("dependencies", []),
        "dependencies_labels": [AttributeNames.humanize(x) for x in item.get("dependencies", [])],
    }


def _bucket_by_matching_type(details: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {"matched": [], "notMatched": [], "missing": []}

    for raw in (details.get("trialEligibilityAttributes") or []):
        item = _normalize_item_for_ui(raw)
        mt_raw = raw.get("matchingType")
        mt = (str(mt_raw).strip().lower() if mt_raw is not None else "")

        if mt == "matched":
            buckets["matched"].append(item)
        elif mt in {"not matched", "not_matched", "not-matched", "notmatched"}:
            buckets["notMatched"].append(item)
        elif mt in {"not evaluated", "not_evaluated", "not-evaluated", "notevaluated"}:
            # Dropped, not bucketed. The three buckets describe the PATIENT
            # against a requirement, and `not_evaluated` says there was no
            # requirement — putting it in "missing" would tell the reader
            # their data is incomplete when nothing was ever asked of it.
            continue
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
    http_method_names = ["get"]

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

            if search_type == "eligible" or getattr(trial, "match_score", None) == 100:
                attrs_to_fill_in = []
            else:
                attrs_to_fill_in = trial.attrs_to_fill_in(counts)

            tt = TrialTemplates(trial, patient_info)
            details = tt.potential_attributes_first_view(attrs_to_fill_in=attrs_to_fill_in)

            node["match"] = _bucket_by_matching_type(details)
            trial_nodes.append(node)

        return Response({
            "patient": GraphPatientInfoSerializer(patient_info).data,
            "trials": trial_nodes,
        })
