import types

import inflection
import re

from django.db import models
from django.db.models import Q

from trials.services.patient_info.configs import THERAPY_LINES_ATTRS_UNDERSCORED, ATTR_MAPPING_TYPE_COMPUTED, \
    THERAPIES_ATTRS_UNDERSCORED, THERAPY_LINES_ATTRS
from trials.services.trial_details.configs import *
from trials.services.therapies_mapper import *
from trials.services.user_to_trial_attrs_mapper import *
from trials.services.value_options import ValueOptions


def get_nested_attribute(instance, path):
    parts = path.split('.')
    current_obj = instance
    for part in parts:
        if current_obj is None:
            return None
        current_obj = getattr(current_obj, part, None)
    return current_obj


class TrialAttributes:
    def __init__(self, trial, patient_info, study_preferences=None):
        self._trial = trial
        self._value_options = ValueOptions()
        self._patient_info = patient_info
        self._study_info = study_preferences
        self._patient_info_attr = PatientInfoAttributes(self._patient_info)
        self._context = self.get_context_from_user()

        self._all_value_options = {}

        self.all_options = ValueOptions().all_options()
        self.all_options['languagesSkillsRequired'] = self.all_options['languagesSkills']
        self.all_options['toxicityGradeMax'] = self.all_options['toxicityGrade']
        self.all_options['flipiScoreOptions'] = self.all_options['flipiScore']
        self.all_options['hrdStatus'] = self.all_options['hrdStatus']
        self.all_options['hrStatuses'] = self.all_options['hrStatus']
        self.all_options['tumorStage'] = self.all_options['tumorStages']
        self.all_options['tumorStagesRequired'] = self.all_options['tumorStages']
        self.all_options['tumorStagesExcluded'] = self.all_options['tumorStages']
        self.all_options['nodesStage'] = self.all_options['nodesStages']
        self.all_options['nodesStagesRequired'] = self.all_options['nodesStages']
        self.all_options['nodesStagesExcluded'] = self.all_options['nodesStages']
        self.all_options['distantMetastasisStage'] = self.all_options['distantMetastasisStages']
        self.all_options['distantMetastasisStagesRequired'] = self.all_options['distantMetastasisStages']
        self.all_options['distantMetastasisStagesExcluded'] = self.all_options['distantMetastasisStages']
        self.all_options['stagingModalitiesRequired'] = self.all_options['stagingModalities']

        self.all_options['mutationGenesRequired'] = self.all_options['geneticMutationGenes']
        self.all_options['mutationVariantsRequired'] = self.all_options['geneticMutationVariants']
        self.all_options['mutationOriginsRequired'] = self.all_options['geneticMutationAllOrigins']
        self.all_options['mutationInterpretationsRequired'] = self.all_options['geneticMutationAllInterpretations']

        # Per #4137 the treatment-outcome dropdown is disease-aware: BC drops
        # the MM-specific IMWG categories (sCR / VGPR / MRD). Other diseases
        # still see the full enum.
        outcome_options_key = self._therapy_outcome_options_key()
        self.all_options['firstLineOutcome'] = self.all_options[outcome_options_key]
        self.all_options['secondLineOutcome'] = self.all_options[outcome_options_key]
        self.all_options['laterOutcome'] = self.all_options[outcome_options_key]
        self.all_options['preExistingConditionsExcluded'] = self.all_options['preExistingConditionCategories']
        self.all_options['therapyTypesRequired'] = self.all_options['therapyTypesAll']
        self.all_options['therapyTypesExcluded'] = self.all_options['therapyTypesAll']
        self.all_options['therapyComponentsRequired'] = self.all_options['therapyComponentsAll']
        self.all_options['therapyComponentsExcluded'] = self.all_options['therapyComponentsAll']
        self.all_options['ethnicityRequired'] = self.all_options['ethnicity']
        self.all_options['cytogenicMarkersRequired'] = self.all_options['cytogenicMarkers']
        self.all_options['cytogenicMarkersExcluded'] = self.all_options['cytogenicMarkers']
        self.all_options['molecularMarkersRequired'] = self.all_options['molecularMarkers']
        self.all_options['molecularMarkersExcluded'] = self.all_options['molecularMarkers']
        self.all_options['histologicTypesRequired'] = self.all_options['histologicType']
        self.all_options['estrogenReceptorStatusesRequired'] = self.all_options['estrogenReceptorStatus']
        self.all_options['progesteroneReceptorStatusesRequired'] = self.all_options['progesteroneReceptorStatus']
        self.all_options['her2StatusesRequired'] = self.all_options['her2Status']
        self.all_options['hrdStatusesRequired'] = self.all_options['hrdStatus']
        self.all_options['hrStatusesRequired'] = self.all_options['hrStatus']
        self.all_options['ldpreExistingConditionsExcluded'] = self.all_options['preExistingConditionCategories']
        self.all_options['ldtumorGradeMin'] = self.all_options['tumorGrade']
        self.all_options['ldtumorGradeMax'] = self.all_options['tumorGrade']
        self.all_options['upreExistingConditionsExcluded'] = self.all_options['upreExistingConditionCategories']
        self.all_options['binetStagesRequired'] = self.all_options['binetStages']
        self.all_options['proteinExpressionsRequired'] = self.all_options['proteinExpressions']
        self.all_options['proteinExpressionsExcluded'] = self.all_options['proteinExpressions']
        self.all_options['richterTransformationsRequired'] = self.all_options['richterTransformations']
        self.all_options['richterTransformationsExcluded'] = self.all_options['richterTransformations']
        self.all_options['tumorBurdensRequired'] = self.all_options['tumorBurdens']
        self.all_options['diseaseActivitiesRequired'] = self.all_options['diseaseActivities']
        if self._trial.disease.lower() == 'multiple myeloma':
            self.all_options['firstLineTherapy'] = self.all_options['therapiesFirstLineMm']
            self.all_options['secondLineTherapy'] = self.all_options['therapiesSecondLineMm']
            self.all_options['laterTherapy'] = self.all_options['therapiesLaterLineMm']
            self.all_options['laterTherapies'] = self.all_options['laterTherapy']
            self.all_options['supportiveTherapiesRequired'] = self.all_options['supportiveTherapiesMm']
            self.all_options['supportiveTherapiesExcluded'] = self.all_options['supportiveTherapiesMm']
            self.all_options['therapiesRequired'] = self.all_options['therapiesMm']
            self.all_options['therapiesExcluded'] = self.all_options['therapiesMm']
            self.all_options['plannedTherapies'] = self.all_options['plannedTherapiesMm']
            self.all_options['plannedTherapiesRequired'] = self.all_options['plannedTherapiesMm']
            self.all_options['plannedTherapiesExcluded'] = self.all_options['plannedTherapiesMm']
            self.all_options['stage'] = self.all_options['stagesMm']
            self.all_options['stages'] = self.all_options['stagesMm']
        elif self._trial.disease.lower() == 'follicular lymphoma':
            self.all_options['firstLineTherapy'] = self.all_options['therapiesFirstLineFl']
            self.all_options['secondLineTherapy'] = self.all_options['therapiesSecondLineFl']
            self.all_options['laterTherapy'] = self.all_options['therapiesLaterLineFl']
            self.all_options['laterTherapies'] = self.all_options['laterTherapy']
            self.all_options['supportiveTherapiesRequired'] = self.all_options['supportiveTherapiesFl']
            self.all_options['supportiveTherapiesExcluded'] = self.all_options['supportiveTherapiesFl']
            self.all_options['therapiesRequired'] = self.all_options['therapiesFl']
            self.all_options['therapiesExcluded'] = self.all_options['therapiesFl']
            self.all_options['plannedTherapies'] = self.all_options['plannedTherapiesFl']
            self.all_options['plannedTherapiesRequired'] = self.all_options['plannedTherapiesFl']
            self.all_options['plannedTherapiesExcluded'] = self.all_options['plannedTherapiesFl']
            self.all_options['stage'] = self.all_options['stagesFl']
            self.all_options['stages'] = self.all_options['stagesFl']
        elif self._trial.disease.lower() == 'breast cancer':
            self.all_options['firstLineTherapy'] = self.all_options['therapiesFirstLineBc']
            self.all_options['secondLineTherapy'] = self.all_options['therapiesSecondLineBc']
            self.all_options['laterTherapy'] = self.all_options['therapiesLaterLineBc']
            self.all_options['laterTherapies'] = self.all_options['laterTherapy']
            self.all_options['supportiveTherapiesRequired'] = self.all_options['supportiveTherapiesBc']
            self.all_options['supportiveTherapiesExcluded'] = self.all_options['supportiveTherapiesBc']
            self.all_options['therapiesRequired'] = self.all_options['therapiesBc']
            self.all_options['therapiesExcluded'] = self.all_options['therapiesBc']
            self.all_options['plannedTherapies'] = self.all_options['plannedTherapiesBc']
            self.all_options['plannedTherapiesRequired'] = self.all_options['plannedTherapiesBc']
            self.all_options['plannedTherapiesExcluded'] = self.all_options['plannedTherapiesBc']
            self.all_options['stage'] = self.all_options['stagesBc']
            self.all_options['stages'] = self.all_options['stagesBc']
        elif self._trial.disease.lower() == 'chronic lymphocytic leukemia':
            self.all_options['firstLineTherapy'] = self.all_options['therapiesFirstLineCll']
            self.all_options['secondLineTherapy'] = self.all_options['therapiesSecondLineCll']
            self.all_options['laterTherapy'] = self.all_options['therapiesLaterLineCll']
            self.all_options['laterTherapies'] = self.all_options['laterTherapy']
            self.all_options['supportiveTherapiesRequired'] = self.all_options['supportiveTherapiesCll']
            self.all_options['supportiveTherapiesExcluded'] = self.all_options['supportiveTherapiesCll']
            self.all_options['therapiesRequired'] = self.all_options['therapiesCll']
            self.all_options['therapiesExcluded'] = self.all_options['therapiesCll']
            self.all_options['plannedTherapies'] = self.all_options['plannedTherapiesCll']
            self.all_options['plannedTherapiesRequired'] = self.all_options['plannedTherapiesCll']
            self.all_options['plannedTherapiesExcluded'] = self.all_options['plannedTherapiesCll']
            self.all_options['stage'] = self.all_options['stagesCll']
            self.all_options['stages'] = self.all_options['stagesCll']

        for k, v in self.all_options.items():
            self._all_value_options[k] = v
            self._all_value_options[f'u{k}'] = v

    # Map PatientInfo.disease (title, lowercased) → the per-disease therapy
    # outcomes options key in all_options. Diseases not listed here fall back
    # to the union `therapyOutcome` key.
    _DISEASE_TO_OUTCOME_KEY = {
        'multiple myeloma': 'therapyOutcomeMm',
        'follicular lymphoma': 'therapyOutcomeFl',
        'breast cancer': 'therapyOutcomeBc',
        'chronic lymphocytic leukemia': 'therapyOutcomeCll',
    }

    def _therapy_outcome_options_key(self):
        if not self._patient_info:
            return 'therapyOutcome'
        disease = (self._patient_info.disease or '').lower()
        return self._DISEASE_TO_OUTCOME_KEY.get(disease, 'therapyOutcome')

    def get_context_from_user(self):
        out = {}

        if not self._patient_info:
            return out

        out['disease'] = str(self._patient_info.disease).lower()
        out['gender'] = self._patient_info.gender

        return out

    def get_field_name(self, field):
        return AttributeNames.get_by_snake_case(field.name)

    def get_label(self, field_name):
        out = ATTR_LABEL_MAPPING.get(field_name, inflection.humanize(inflection.underscore(field_name)).title())
        out = out.replace(" Required", "")
        out = out.replace(" Uln ", " ULN ")
        out = out.replace("Her2 ", "HER2 ")
        out = out.replace("Hr ", "HR ")
        out = out.replace("Hrd ", "HRD ")
        out = out.replace("Tnbc ", "TNBC ")
        out = out.replace(" Imwg", " (IMWG)")
        out = out.replace("Brca1 ", "BRCA1 ")
        out = out.replace("Brca2 ", "BRCA2 ")
        out = out.replace("Pik3ca ", "PIK3CA ")
        out = out.replace("Tp53 ", "TP53 ")
        out = out.replace("Esr1 ", "ESR1 ")
        out = re.sub(' Min$', ' Minimum', out)
        out = re.sub(' Max$', ' Maximum', out)
        return out

    def get_value(self, field_name, value):
        if value is None:
            return value

        if field_name in ['tumorGrade', 'tumorGradeMin', 'tumorGradeMax']:
            return self._value_options.tumor_grades.get(str(value))
        elif field_name in ['supportiveTherapies']:
            return self.get_supportive_therapies()
        elif field_name in ['laterTherapies']:
            return self.get_later_therapies()
        elif field_name in ['geneticMutations']:
            return self.get_genetic_mutations()
        else:
            return value

    def get_uvalue(self, field_name, value, matched_value=None):
        if value is None:
            return value

        def styled_value(title, is_bold=False):
            if is_bold:
                return f'**{title}**'
            return title

        if field_name in ['ulastTreatment']:
            return f'since {value}'
        elif field_name in ['uhrStatus']:
            options = {x['value']: x['label'] for x in self.all_options['hrStatus']['options']}
            return options.get(value)
        elif field_name in ['ugeneticMutations']:
            if matched_value is None:
                matched_value = []
            genes_options = {x['value']: x['label'] for x in self.all_options['mutationGenesRequired']['options']}
            variants_options = {x['value']: x['label'] for x in self.all_options['mutationVariantsRequired']['options']}
            origins_options = {x['value']: x['label'] for x in self.all_options['mutationOriginsRequired']['options']}
            interpretations_options = {x['value']: x['label'] for x in self.all_options['mutationInterpretationsRequired']['options']}
            options = {**genes_options, **variants_options, **origins_options, **interpretations_options}
            return [styled_value(options.get(x), bool(x in matched_value)) for x in value]
        else:
            return value

    def is_blank(self, field_name, value, search_type):
        if value is None:
            return True
        elif str(value) == '[]':
            return True
        elif str(value).lower() in ['none', 'null']:
            return True
        elif search_type == "str_value" and str(value) == '':
            return True
        elif value is False and field_name in ATTR_FALSE_VALUE_IS_BLANK:
            return True
        elif self._context['gender'] == 'M' and field_name in ATTR_SKIP_FOR_MALE:
            return True
        return False

    def get_options(self, field_name):
        res = ATTR_OPTIONS_MAPPING.get(field_name)
        if not res:
            res = self._all_value_options.get(field_name)
            if res:
                res = res['options']
        return res

    def get_options_for(self, therapy_line):
        if therapy_line == 'first_line_therapy':
            return self.all_options['firstLineTherapy']['options']
        elif therapy_line == 'second_line_therapy':
            return self.all_options['secondLineTherapy']['options']
        elif therapy_line == 'later_therapy':
            return self.all_options['laterTherapy']['options']
        elif therapy_line == 'later_therapies':
            return self.all_options['laterTherapies']['options']
        elif therapy_line == 'supportive_therapies':
            return self.all_options['supportiveTherapiesRequired']['options']

        return []

    def get_field_type(self, field):
        field_type = type(field)

        if field_type == models.fields.DateField:
            return 'date'
        if field_type in [models.fields.IntegerField, models.fields.PositiveIntegerField]:
            return 'int'
        if field_type == models.fields.DecimalField:
            return 'float'
        if field_type == models.fields.FloatField:
            return 'float'
        if field_type == models.fields.CharField:
            return 'string'
        if field_type == models.fields.TextField:
            return 'string'
        if field_type == models.fields.BooleanField:
            return 'boolean'
        if field_type == models.fields.json.JSONField:
            return 'multiselect'
        if field_type == models.fields.related.ForeignKey:
            return 'select'
        return str(field_type)

    def get_field(self, field, value, user_field, user_value, subform_details, units_data):

        field_name = self.get_field_name(field)

        extra_attrs = {}
        utype = None
        if subform_details:
            extra_attrs['subform_details'] = subform_details

        ureadonly = subform_details is not None

        type = ATTR_TYPE_MAPPING.get(field_name)
        options = self.get_options(field_name)
        if not type and options:
            type = 'select'
        if not type:
            type = self.get_field_type(field)

        uoptions = self.get_options(f'u{user_field}') or self.get_options(user_field)
        if not utype:
            utype = ATTR_TYPE_MAPPING.get(f'u{user_field}')
        if not utype and uoptions:
            utype = 'select'
        uvalue = self.get_uvalue(f'u{user_field}', user_value)

        return {
            **extra_attrs,
            **units_data,
            'name': field_name,
            'label': self.get_label(field_name),
            'type': type,
            'value': self.get_value(field_name, value),
            'options': options,
            'ureadonly': ureadonly,
            'ufield': user_field,
            'uvalue': uvalue,
            'utype': utype or type,
            'uoptions': uoptions,
            'dependencies': ATTR_DEPS_MAPPING.get(user_field, []) if user_field else [],
        }

    def details(self):
        out = {}
        trial = self._trial
        subform_attrs = self.get_user_subform_attrs()
        user_details = self.get_user_details(subform_attrs=subform_attrs)

        computed_fields = self.computed_general_fields()

        for attr in trial.__class__._meta.fields:
            attr_name = attr.name
            if attr_name in ATTR_NAME_TO_SKIP:
                continue
            if attr_name in THERAPIES_ATTRS_UNDERSCORED:
                continue

            if attr_name == 'trial_type':
                attr_value = get_nested_attribute(trial, 'trial_type.title')
            else:
                attr_value = getattr(trial, attr_name)
            user_field = None
            user_value = None
            subform_details = subform_attrs[attr_name] if attr_name in subform_attrs else None

            units_data = {}
            if attr_name in user_details:
                user_data = user_details[attr_name]
                user_field = AttributeNames.get_by_snake_case(user_data['ufield'])
                user_value = user_data['uvalue']

                if self._patient_info:
                    units_details = patient_info_attr_units_for(user_data['ufield'], self._patient_info)
                    if units_details:
                        units_data = {
                            'units': units_details['options'].get(units_details['default']),
                            'uunits': units_details['options'].get(units_details['uvalue']),
                            'uunitsOptions': ValueOptions.to_value_and_label(units_details['options']),
                        }

                field = self.get_field(attr, attr_value, user_field, user_value, subform_details, units_data)
                out[field['name']] = field
                out[field['name']]['search_type'] = user_data.get('search_type')
            elif attr_name in ATTR_NAME_ALWAYS_INCLUDED:
                field = self.get_field(attr, attr_value, user_field, user_value, subform_details, units_data)
                out[field['name']] = field

        therapies = self.therapies(subform_attrs)
        return {
            **out,
            **computed_fields,
            **therapies,
        }

    def computed_general_fields(self):
        computed_fields = {}

        recruitment_status = self._study_info.recruitment_status if self._study_info else None
        distance_units = (self._study_info.distance_units if self._study_info else None) or 'km'
        distance = self._trial.get_distance(
            self._patient_info,
            distance_units,
            recruitment_status=recruitment_status
        )
        if distance:
            distance = f'{distance} {distance_units}'

        computed_fields['distance'] = {
          "name": "distance",
          "label": "distance",
          "type": "string",
          "value": distance,
          "options": None,
          "ureadonly": True,
          "ufield": None,
          "uvalue": None,
          "utype": "string",
          "uoptions": None,
          "dependencies": []
        }

        goodness_score = getattr(self._trial, 'goodness_score', None)
        if goodness_score is None:
            goodness_score = self._trial.get_goodness_score(self._patient_info)
        computed_fields['goodnessScore'] = {
          "name": "goodnessScore",
          "label": "goodness Score",
          "type": "string",
          "value": str(goodness_score),
          "options": None,
          "ureadonly": True,
          "ufield": None,
          "uvalue": None,
          "utype": "string",
          "uoptions": None,
          "dependencies": []
        }

        match_score = self._trial.get_match_score(self._patient_info)
        match_score_value = '' if match_score is None else f'{str(match_score)}%'
        computed_fields['matchScore'] = {
          "name": "matchScore",
          "label": "match Score",
          "type": "string",
          "value": '',
          "options": None,
          "ureadonly": True,
          "ufield": None,
          "uvalue": match_score_value,
          "utype": "string",
          "uoptions": None,
          "dependencies": []
        }

        distance_penalty = self._trial.get_distance_penalty(self._patient_info)
        if distance_penalty:
            distance_penalty = str(distance_penalty)

        computed_fields['distancePenalty'] = {
          "name": "distancePenalty",
          "label": "distance Penalty",
          "type": "string",
          "value": distance_penalty,
          "options": None,
          "ureadonly": True,
          "ufield": None,
          "uvalue": None,
          "utype": "string",
          "uoptions": None,
          "dependencies": []
        }

        locations_name = self._trial.sorted_locations_by_distance(
            self._patient_info.geo_point,
            recruitment_status=recruitment_status
        )
        locations_name = [x.location.title for x in locations_name if x.location]
        computed_fields['locationsName'] = {
          "name": "locationsName",
          "label": "Locations Name",
          "type": "multiselect",
          "value": locations_name,
          "options": None,
          "ureadonly": False,
          "ufield": None,
          "uvalue": None,
          "utype": "multiselect",
          "uoptions": None,
          "dependencies": []
        }

        return computed_fields

    def therapies(self, subform_attrs):
        out = {}
        if not self._patient_info:
            return out

        for field_name in THERAPIES_ATTRS_UNDERSCORED:
            user_value = None
            value = getattr(self._trial, field_name)

            trial_field = self._trial.__class__._meta.get_field(field_name)
            trial_field_name = self.get_field_name(trial_field)
            options = self.get_options(trial_field_name)
            uoptions = None
            subform_details = subform_attrs[field_name] if field_name in subform_attrs else None

            if value is None or len(value) == 0:
                continue

            field_attrs = {}
            if subform_details:
                field_attrs['subform_details'] = subform_details

            field = {
                **field_attrs,
                'name': trial_field_name,
                'label': self.get_label(field_name),
                'type': 'multiselect',
                'value': value,
                'options': options,
                'ureadonly': True,
                'ufield': self.get_field_name(trial_field),
                'uvalue': user_value,
                'utype': 'multiselect',
                'uoptions': uoptions,
                'dependencies': [],
            }

            out[field['name']] = field

        return out

    def get_genetic_mutations(self):
        def get_origins(val):
            return self.all_options['geneticMutationOriginsPerGene']['options'].get(val, [])

        def get_variants(val):
            return self.all_options['geneticMutationAllVariants']['options'].get(val, [])

        def get_mutation(val):
            gene = val.get('gene')
            variant_options = []
            origin_options = []

            if gene:
                variant_options = get_variants(gene)
                origin_options = get_origins(gene)

            variant = val.get('variant')
            origin = val.get('origin')
            interpretation = val.get('interpretation')
            row_complete = gene and (variant or origin or interpretation)
            return row_complete, {
                'gene': {
                    'type': 'select',
                    'value': gene,
                    'options': self.all_options['geneticMutationGenes']['options']
                },
                'variant': {
                    'type': 'select',
                    'value': variant,
                    'options': variant_options
                },
                'origin': {
                    'type': 'select',
                    'value': origin,
                    'options': origin_options,
                    'readonly': origin_options == [],
                    'disabled': origin_options == []
                },
                'interpretation': {
                    'type': 'select',
                    'value': interpretation,
                    'options': self.all_options['geneticMutationInterpretations']['options']
                }
            }

        out = []
        is_finished_row = True
        for item in self._patient_info.genetic_mutations:
            is_finished_row, mutation = get_mutation(item)
            out.append(mutation)

        if is_finished_row:
            out.append(
                {
                    'gene': {
                        'type': 'select',
                        'value': None,
                        'options': self.all_options['geneticMutationGenes']['options']
                    },
                    'variant': {
                        'type': 'select',
                        'value': None,
                        'options': []
                    },
                    'origin': {
                        'type': 'select',
                        'value': None,
                        'options': []
                    },
                    'interpretation': {
                        'type': 'select',
                        'value': None,
                        'options': []
                    },
                }
            )
        return out

    def get_supportive_therapies(self):
        def get_therapy(val):
            if isinstance(val, dict):
                therapy = val.get('therapy')
                date = val.get('date')
            else:
                therapy = val
                date = None
            row_complete = therapy
            return row_complete, {
                'therapy': {
                    'type': 'select',
                    'value': therapy,
                    'options': self.all_options['supportiveTherapiesRequired']['options']
                },
                'date': {
                    'type': 'date',
                    'value': date,
                },
            }

        out = []
        is_finished_row = True
        for item in self._patient_info.supportive_therapies:
            is_finished_row, therapy = get_therapy(item)
            out.append(therapy)

        if is_finished_row:
            out.append(
                {
                    'therapy': {
                        'type': 'select',
                        'value': None,
                        'options': self.all_options['supportiveTherapiesRequired']['options']
                    },
                    'date': {
                        'type': 'date',
                        'value': None,
                    },
                }
            )
        return out

    def get_later_therapies(self):
        def get_therapy(val):
            if isinstance(val, dict):
                therapy = val.get('therapy')
                date = val.get('date')
            else:
                therapy = val
                date = None
            row_complete = therapy
            return row_complete, {
                'therapy': {
                    'type': 'select',
                    'value': therapy,
                    'options': self.all_options['laterTherapies']['options']
                },
                'date': {
                    'type': 'date',
                    'value': date,
                },
            }

        out = []
        is_finished_row = True
        for item in self._patient_info.later_therapies:
            is_finished_row, therapy = get_therapy(item)
            out.append(therapy)

        if is_finished_row:
            out.append(
                {
                    'therapy': {
                        'type': 'select',
                        'value': None,
                        'options': self.all_options['laterTherapies']['options']
                    },
                    'date': {
                        'type': 'date',
                        'value': None,
                    },
                }
            )
        return out

    def get_user_subform_attrs(self):
        out = {}
        if not self._patient_info:
            return out

        tmp = {}
        for _group, data in SUBFORM_ATTRS_MAPPING.items():

            if callable(data):
                pi_field_names = data(self._patient_info)
            else:
                pi_field_names = data

            for pi_field_name in pi_field_names:
                if pi_field_name in out:
                    continue

                field = self._patient_info.__class__._meta.get_field(pi_field_name)
                field_name = self.get_field_name(field)
                value = self._patient_info_attr.get_value(pi_field_name)
                if pi_field_name in THERAPY_LINES_ATTRS_UNDERSCORED:
                    options = self.get_options_for(pi_field_name)
                else:
                    options = self.get_options(f'u{field_name}') or self.get_options(field_name)

                utype = ATTR_TYPE_MAPPING.get(field_name, self.get_field_type(field))
                if options is not None:
                    utype = 'select'

                val = {
                    'name': field_name,
                    'label': self.get_label(field_name),
                    'type': utype,
                    'value': self.get_value(field_name, value),
                    'options': options,
                }

                tmp[pi_field_name] = val

        for group, data in SUBFORM_ATTRS_MAPPING.items():
            group_data = []

            if callable(data):
                pi_field_names = data(self._patient_info)
            else:
                pi_field_names = data

            for pi_field_name in pi_field_names:
                group_data.append(tmp[pi_field_name])

            out[group] = group_data

        return out

    def get_user_details(self, subform_attrs):
        out = {}
        if not self._patient_info:
            return out

        mapping = USER_TO_TRIAL_ATTRS_MAPPING

        for user_attr in mapping.keys():
            trial_attr_meta = mapping[user_attr]

            subform_details = subform_attrs[user_attr] if user_attr in subform_attrs else None
            ureadonly = ('is_computed_value' in trial_attr_meta and trial_attr_meta['is_computed_value'] is True) or subform_details is not None

            if "disease" in trial_attr_meta and (self._patient_info_attr.disease_code is None or self._patient_info_attr.disease_code not in trial_attr_meta["disease"]):
                continue

            if trial_attr_meta["type"] == "min_value":
                if 'attr_min' in trial_attr_meta:
                    attr_name_min = trial_attr_meta["attr_min"]
                else:
                    attr_name_min = f'{trial_attr_meta["attr"]}_min'
                out[attr_name_min] = {
                    'ureadonly': ureadonly,
                    'ufield': user_attr,
                    'uvalue': self.get_uvalue(f'u{user_attr}', self._patient_info_attr.get_value(user_attr)),
                }
                if subform_details:
                    out[attr_name_min]['subform_details'] = subform_details

            elif trial_attr_meta["type"] == "max_value":
                if 'attr_max' in trial_attr_meta:
                    attr_name_max = trial_attr_meta["attr_max"]
                else:
                    attr_name_max = f'{trial_attr_meta["attr"]}_max'
                out[attr_name_max] = {
                    'ureadonly': ureadonly,
                    'ufield': user_attr,
                    'uvalue': self.get_uvalue(f'u{user_attr}', self._patient_info_attr.get_value(user_attr)),
                }
                if subform_details:
                    out[attr_name_max]['subform_details'] = subform_details

            elif trial_attr_meta["type"] == "min_max_value":
                if 'attr_min' in trial_attr_meta:
                    attr_name_min = trial_attr_meta["attr_min"]
                else:
                    attr_name_min = f'{trial_attr_meta["attr"]}_min'
                out[attr_name_min] = {
                    'ureadonly': ureadonly,
                    'ufield': user_attr,
                    'uvalue': self.get_uvalue(f'u{user_attr}', self._patient_info_attr.get_value(user_attr)),
                }
                if subform_details:
                    out[attr_name_min]['subform_details'] = subform_details

                if 'attr_max' in trial_attr_meta:
                    attr_name_max = trial_attr_meta["attr_max"]
                else:
                    attr_name_max = f'{trial_attr_meta["attr"]}_max'
                out[attr_name_max] = {
                    'ureadonly': ureadonly,
                    'ufield': user_attr,
                    'uvalue': self.get_uvalue(f'u{user_attr}', self._patient_info_attr.get_value(user_attr)),
                }
                if subform_details:
                    out[attr_name_max]['subform_details'] = subform_details

                if "uln_attr_min" in trial_attr_meta and "uln_attr_max" in trial_attr_meta:
                    uln_value = self._patient_info_attr.get_uln_value(user_attr)
                    if uln_value:
                        uln_value = round(uln_value, 4)
                    out[trial_attr_meta["uln_attr_min"]] = {
                        'ureadonly': True,
                        'ufield': user_attr,
                        'uvalue': uln_value
                    }
                    if subform_details:
                        out[trial_attr_meta["uln_attr_min"]]['subform_details'] = subform_details

                    out[trial_attr_meta["uln_attr_max"]] = {
                        'ureadonly': True,
                        'ufield': user_attr,
                        'uvalue': uln_value
                    }
                    if subform_details:
                        out[trial_attr_meta["uln_attr_max"]]['subform_details'] = subform_details

            elif trial_attr_meta["type"] == ATTR_MAPPING_TYPE_COMPUTED:
                attr_names = trial_attr_meta["attr"]
                if isinstance(attr_names, str):
                    attr_names = [attr_names]
                for attr_name in attr_names:
                    uvalue_function = trial_attr_meta["uvalue_function"][attr_name]
                    uvalue = uvalue_function(self._patient_info)
                    uvalue = self.get_uvalue(f'u{attr_name}', uvalue)
                    out[attr_name] = {
                        'ureadonly': ureadonly,
                        'ufield': user_attr,
                        'uvalue': uvalue
                    }
                    if subform_details:
                        out[attr_name]['subform_details'] = subform_details

            else:
                if isinstance(trial_attr_meta["attr"], list):
                    attr_name = trial_attr_meta["attr"][0]
                else:
                    attr_name = trial_attr_meta["attr"]

                out[attr_name] = {
                    'ureadonly': ureadonly,
                    'ufield': user_attr,
                    'uvalue': self.get_uvalue(f'u{user_attr}', self._patient_info_attr.get_value(user_attr)),
                }
                if subform_details:
                    out[attr_name]['subform_details'] = subform_details
                if trial_attr_meta["type"] == "str_value":
                    out[attr_name]['search_type'] = "str_value"

        return out

    def details_potentials_first_view(self):
        self.details()

    def details_all_details_in_groups_view(self):
        self.details()
