import inflection
import re

from trials.services.patient_info.configs import THERAPY_LINES_ATTRS, THERAPIES_ATTRS
from trials.services.trial_details.trial_attributes import *
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher


class TrialTemplates:
    def __init__(self, trial, patient_info):
        self._trial = trial
        self._patient_info = patient_info
        self._trial_attributes = TrialAttributes(trial, patient_info=patient_info)

    def details_and_group_names(self, template, attrs_to_fill_in):
        if template == 'all_attributes_in_groups':
            return {
                'details': self.all_attributes_in_groups_view(),
                'group_names': self.group_names(),
            }
        else:
            return {
                'details': self.potential_attributes_first_view(attrs_to_fill_in),
                'group_names': self.group_names(),
            }

    def get_field_name(self, field):
        self._trial_attributes.get_field_name(field)

    def get_field_groups(self, field_name):
        value = ATTR_GROUP_MAPPING.get(field_name, 'ignore')
        if not isinstance(value, (list, tuple)):
            return [value]
        else:
            return value

    def potential_attributes_first_view(self, attrs_to_fill_in):
        out = {
            'general': [],
            'trialEligibilityAttributes': [],
        }

        mapping_for_order = {
            # NOTE: the key is 'not matched' with a SPACE, while the status is
            # `not_matched` — so a mismatch never matches it and falls through
            # to the default weight of 5. Pre-existing; left alone here rather
            # than reordered as a side effect of adding a status, because
            # fixing it moves rows this change has nothing to do with.
            'not matched': 1,
            'unknown': 2,
            'matched': 3,
            # 6, not 4: it has to land after the default-weight bucket that
            # `not_matched` actually falls into. "The trial never asked" is the
            # least informative row there is and must not sit above a real
            # mismatch.
            'not_evaluated': 6,
        }

        details = self._trial_attributes.details()
        patient_info = self._patient_info
        # No patient, no matcher — and no claim about one. The rows still carry
        # what the trial requires; what they do not carry is a verdict, which
        # is the honest shape for "here is a trial" as opposed to "here is how
        # you compare to it".
        service = UserToTrialAttrMatcher(self._trial, patient_info) if patient_info else None
        label = ''

        def order_weight(value):
            if value['name'] in THERAPY_LINES_ATTRS:
                return 10, ''
            if value['name'] == 'matchScore':
                return 0, ''
            # `.get`: without a patient no row has a `matchingType` at all,
            # and they all land on the default weight and sort by label.
            return mapping_for_order.get(value.get('matchingType'), 5), value['label']

        therapy_match_statuses = service.therapy_related_things_match_status() if service else {}

        for field in details.values():
            if not isinstance(field, dict):
                continue
            field_name = field['name']
            field_groups = self.get_field_groups(field_name)

            for field_group in field_groups:

                if field_group == 'general':
                    out[field_group].append(field)
                elif field_group == 'admin':
                    pass
                elif field_name in THERAPIES_ATTRS:
                    if field['value'] == []:
                        continue  # skip — active column (legacy or OMOP) has no criteria
                    if field_name in therapy_match_statuses:
                        field['matchingType'] = therapy_match_statuses[field_name]["status"]
                        field['uvalue'] = therapy_match_statuses[field_name]["values"]
                        # OMOP code + title for the OMOP-mapped levels (regimen/component);
                        # absent for legacy/type criteria. See therapy_related_things_match_status.
                        if 'omopConcepts' in therapy_match_statuses[field_name]:
                            field['omopConcepts'] = therapy_match_statuses[field_name]['omopConcepts']
                    out['trialEligibilityAttributes'].append(field)
                elif not self._trial_attributes.is_blank(field_name, field['value'], field.get('search_type')):
                    if service is None:
                        pass  # no patient, so no verdict — see above
                    elif field['ufield']:
                        field['matchingType'] = service.attr_match_status(AttributeNames.get_by_camel_case(field['ufield']))
                    else:
                        field['matchingType'] = 'matched'
                    field_label = field['label']
                    if 'search_type' in field:
                        del field['search_type']
                    if field_label == label.replace(' Minimum', ' Maximum'):
                        # update latest record
                        name = out['trialEligibilityAttributes'][-1]['name']
                        out['trialEligibilityAttributes'][-1]['name'] = re.sub('Min$', '', name)
                        out['trialEligibilityAttributes'][-1]['type'] = 'string'
                        out['trialEligibilityAttributes'][-1]['label'] = re.sub('Minimum$', 'Range', label)
                        out['trialEligibilityAttributes'][-1]['value'] = f"{out['trialEligibilityAttributes'][-1]['value']} - {field['value']}"
                    else:
                        out['trialEligibilityAttributes'].append(field)
                    label = field_label

        # check 'select' value is in options
        new_attrs_list = []
        for attr in out['trialEligibilityAttributes']:
            if attr['type'] != 'select':
                new_attrs_list.append(attr)
            elif attr['value'] in [x['value'] for x in attr['options']] or str(attr['value']).lower() in [str(x['value']).lower() for x in attr['options']]:
                new_attrs_list.append(attr)

        out['trialEligibilityAttributes'] = new_attrs_list
        # print("\n\n>>>>>out['trialEligibilityAttributes'] before", out['trialEligibilityAttributes'])
        out['trialEligibilityAttributes'].sort(key=lambda v: order_weight(v))
        # print("\n\n>>>>>out['trialEligibilityAttributes'] after", out['trialEligibilityAttributes'])
        return out

    def all_attributes_in_groups_view(self):
        out = {}
        details = self._trial_attributes.details()
        for field in details.values():
            if not isinstance(field, dict):
                continue
            field_name = field['name']
            field_groups = self.get_field_groups(field_name)
            for field_group in field_groups:
                if field_group == 'ignore':
                    continue
                if field_group == 'admin':
                    continue
                if field_group not in out:
                    out[field_group] = []
                out[field_group].append(field)

        return out

    def group_names(self):
        items = {k: v for k, v in GROUP_NAMES.items() if k != 'admin'}
        return [{'value': k, 'label': v} for k, v in items.items()]
