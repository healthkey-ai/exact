from trials.services.patient_info.configs import USER_TO_TRIAL_ATTRS_MAPPING
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from trials.services.utils import disease_attr_applies

_STATUS_ORDER = {'not_matched': 0, 'unknown': 1, 'matched': 2}


def _trial_requirement(trial, meta):
    """
    Extract the trial-side requirement value for display.

    For attrs with a single field (e.g. age_low_limit) returns that value.
    For min/max pairs returns {'min': ..., 'max': ...}.
    For computed / multi-attr cases returns None (the UI can read trial
    fields directly if needed).
    """
    attr = meta.get('attr')
    if attr is None:
        return None

    if isinstance(attr, list):
        # Multi-field attr (e.g. min/max stored on separate columns)
        attr_min = meta.get('attr_min')
        attr_max = meta.get('attr_max')
        if attr_min or attr_max:
            return {
                'min': getattr(trial, attr_min, None) if attr_min else None,
                'max': getattr(trial, attr_max, None) if attr_max else None,
            }
        return None

    return getattr(trial, attr, None)


class TrialMatchExplainer:
    """
    Produces a per-criterion breakdown of why a trial matches, is potentially
    eligible, or is ineligible for a given patient.

    Usage::

        explainer = TrialMatchExplainer(trial, patient_info)
        reasons = explainer.explain()
        # [{'attr': 'age', 'status': 'matched', 'patientValue': 58,
        #   'trialRequirement': {'min': 18, 'max': 75}}, ...]

    ``attr_match_status()`` always returns a plain status string
    ('matched' | 'unknown' | 'not_matched'). The dict-shaped return value
    belongs to ``therapy_related_things_match_status()``, which is a separate
    method used only by the trial-details view.

    Results are sorted: not_matched → unknown → matched, so the most
    actionable information (disqualifiers, then data gaps) appears first.
    """

    def __init__(self, trial, patient_info, matcher=None):
        self.trial = trial
        self.patient_info = patient_info
        # Reuse a caller-supplied matcher when given (avoids rebuilding it) — e.g.
        # the detail serializer already constructed one for this (trial, patient).
        self._matcher = matcher if matcher is not None else UserToTrialAttrMatcher(trial, patient_info)
        self._pi_attrs = PatientInfoAttributes(patient_info)

    def explain(self):
        results = []

        for attr, meta in USER_TO_TRIAL_ATTRS_MAPPING.items():
            # Skip attrs not relevant to this trial's disease.
            disease_restriction = meta.get('disease')
            if disease_restriction is not None:
                if self._matcher.disease_code is None:
                    continue
                if not disease_attr_applies(disease_restriction, self._matcher.disease_code):
                    continue

            status = self._matcher.attr_match_status(attr)
            patient_value = self._pi_attrs.get_value(attr)

            results.append({
                'attr': attr,
                'status': status,
                'patientValue': patient_value,
                'trialRequirement': _trial_requirement(self.trial, meta),
            })

        results.sort(key=lambda r: _STATUS_ORDER.get(r['status'], 3))
        return results
