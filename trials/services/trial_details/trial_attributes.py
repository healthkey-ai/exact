import types

import inflection
import re

from django.db import models
from django.db.models import Q

from trials.services.patient_info.promop_adapter import PROMOP_NEGATED_FIELDS
from trials.services.patient_info.normalize import (
    recompute_note, therapy_line_is_authorable,
)
from trials.services.patient_info.configs import THERAPY_LINES_ATTRS_UNDERSCORED, ATTR_MAPPING_TYPE_COMPUTED, \
    THERAPIES_ATTRS_UNDERSCORED, THERAPY_LINES_ATTRS
from trials.services.therapy_match_profile import (
    THERAPY_MATCH_PROFILE, omop_therapy_enabled, omop_therapy_types_enabled)
from trials.services.trial_details.configs import *
from trials.services.therapies_mapper import *
from trials.services.user_to_trial_attrs_mapper import *
from trials.services.utils import disease_attr_applies
from trials.services.value_options import ValueOptions
from trials.db_compat import missing_columns


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
        elif self._trial.disease.lower() == 'mantle cell lymphoma':
            self.all_options['firstLineTherapy'] = self.all_options['therapiesFirstLineMcl']
            self.all_options['secondLineTherapy'] = self.all_options['therapiesSecondLineMcl']
            self.all_options['laterTherapy'] = self.all_options['therapiesLaterLineMcl']
            self.all_options['laterTherapies'] = self.all_options['laterTherapy']
            self.all_options['supportiveTherapiesRequired'] = self.all_options['supportiveTherapiesMcl']
            self.all_options['supportiveTherapiesExcluded'] = self.all_options['supportiveTherapiesMcl']
            self.all_options['therapiesRequired'] = self.all_options['therapiesMcl']
            self.all_options['therapiesExcluded'] = self.all_options['therapiesMcl']
            self.all_options['plannedTherapies'] = self.all_options['plannedTherapiesMcl']
            self.all_options['plannedTherapiesRequired'] = self.all_options['plannedTherapiesMcl']
            self.all_options['plannedTherapiesExcluded'] = self.all_options['plannedTherapiesMcl']
            self.all_options['stage'] = self.all_options['stagesMcl']
            self.all_options['stages'] = self.all_options['stagesMcl']

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
        'mantle cell lymphoma': 'therapyOutcomeMcl',
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
        elif self._context.get('gender') == 'M' and field_name in ATTR_SKIP_FOR_MALE:
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

    def get_field(self, field, value, user_field, user_value, subform_details, units_data,
                  user_readonly=False):

        field_name = self.get_field_name(field)

        extra_attrs = {}
        utype = None
        if subform_details:
            extra_attrs['subform_details'] = subform_details

        ureadonly = user_readonly or subform_details is not None

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

        # Columns the trials database does not have. This loop walks every
        # field the model declares and reads it, so a corpus behind the models
        # (see #360) turns the eligibility table into a 500: the deferred
        # column is loaded on attribute access and the query names a column
        # that is not there. Skipping is the right answer for a details view —
        # the corpus holds no value for the attribute, so there is nothing to
        # show. Empty whenever the schema matches, which is every deployment
        # that is not reading the legacy corpus.
        absent = set(missing_columns(trial.__class__, trial._state.db))

        for attr in trial.__class__._meta.fields:
            attr_name = attr.name
            if attr_name in ATTR_NAME_TO_SKIP:
                continue
            if attr_name in THERAPIES_ATTRS_UNDERSCORED:
                continue
            if attr_name in absent:
                continue

            if attr_name == 'trial_type':
                attr_value = get_nested_attribute(trial, 'trial_type.title')
            else:
                attr_value = getattr(trial, attr_name)
            user_field = None
            user_value = None
            subform_details = subform_attrs[attr_name] if attr_name in subform_attrs else None

            units_data = {}
            stored_value_used = False
            if attr_name in user_details:
                user_data = user_details[attr_name]
                # Port CB's patient-attribute resolution: patient and trial
                # names may differ (e.g. measurable_disease_imwg_required).
                subform_details = user_data.get('subform_details') or subform_attrs.get(attr_name)
                user_field = AttributeNames.get_by_snake_case(user_data['ufield'])
                user_value = user_data['uvalue']
                canonical_value = user_value

                if self._patient_info:
                    units_details = patient_info_attr_units_for(user_data['ufield'], self._patient_info)
                    uunits_label = (
                        unit_label(units_details['options'], units_details['uvalue'])
                        if units_details else None
                    )
                    # A ×ULN row shows a RATIO, not a physical measurement: the
                    # stored unit label would describe a number that is not in
                    # that unit (and the row is read-only anyway). An unknown
                    # stored code is the other refusal: keeping the converted
                    # value is better than pairing a raw number with the
                    # DEFAULT unit label.
                    if uunits_label is not None and 'uln' not in attr_name.lower():
                        units_data = {
                            'units': unit_label(units_details['options'], units_details['default']),
                            'uunits': uunits_label,
                            'uunitsOptions': ValueOptions.to_value_and_label(units_details['options']),
                        }
                        # `uvalue` is converted into the matcher's unit, while
                        # `uunits` names the patient's STORED unit. Pairing them
                        # printed 120 g/L as "12 g/Liter" and, on a writable row,
                        # would seed the editor with the converted number.
                        user_value = getattr(
                            self._patient_info, user_data['ufield'], user_value)
                        stored_value_used = True

                field = self.get_field(
                    attr, attr_value, user_field, user_value, subform_details, units_data,
                    user_readonly=user_data['ureadonly'],
                )
                if stored_value_used:
                    # The graph compares this value with the trial's, which is
                    # in the matcher's unit. Keep it beside the stored display
                    # value so that comparison does not silently change scale.
                    field['uvalue_canonical'] = canonical_value
                out[field['name']] = field
                out[field['name']]['search_type'] = user_data.get('search_type')
            elif attr_name in ATTR_NAME_ALWAYS_INCLUDED:
                field = self.get_field(attr, attr_value, user_field, user_value, subform_details, units_data)
                out[field['name']] = field

        therapies = self.therapies(subform_attrs)
        return self.with_patient_field_names({
            **out,
            **computed_fields,
            **therapies,
        }, negated_sources=self._patient_negated_sources())

    def _patient_negated_sources(self):
        """The canonical answers captured before the record was normalized.

        Empty when the caller built this patient without provenance (an old
        management command, a fixture): the row then carries no
        `negative_source`, and the client opens no editor — the fail-closed
        default (#5667).
        """
        if not self._patient_info:
            return {}
        return getattr(self._patient_info, '_negated_sources', None) or {}

    @staticmethod
    def with_patient_field_names(fields, negated_sources=None):
        """Stamp every row with the patient attribute it is about, in snake_case.

        `ufield` is camelCase for the UI, and a client that wants to WRITE
        the value addresses PROMOP, whose API is snake_case. Deriving the
        name client-side is a trap: PROMOP answers an unknown field with 200
        and no change (its `partial_update` dropped the old 405 on purpose),
        so a name converted wrongly is a save that appears to work. (That is
        PROMOP's `dev`; its `main` still answers 405, which is the older and
        louder behaviour — a client must not rely on either.)

        The derivation is not safe even when it looks it: `p53_ihc`
        camelizes to `p53Ihc`, and a standard snake-caser turns that back
        into `p_53_ihc`, a field nobody has.

        What this is NOT is a promise that the value can be written.
        `upatientField` names the attribute; whether PROMOP will accept a
        write to it — and where that write lands — is answered by
        `GET /api/v1/patient-records/writable-fields/`, which is caller-aware
        and moves with the vocabulary. EXACT cannot answer it: 19 of
        its own user fields have no column on PROMOP's `PatientRecord` at
        all, measured against its `dev` on 2026-09-12 (`mipi_risk`,
        `high_risk_mcl_criteria`, `p53_ihc`, …), and a key here that
        implied otherwise would put EXACT's authority behind exactly the
        silent 200 this exists to prevent.

        There is a THIRD question neither answers, and it matters as much:
        which values EXACT itself recomputes. An edit to one of those is
        overwritten by the next match whatever PROMOP thinks of the write —
        `normalize.py` sets `tnbc_status` on every save and `mipi_risk` on
        every MCL one, among a dozen others — none of them from anything
        the reader typed.

        `ureadonly` is not that answer, though it is the obvious candidate:
        `get_field` sets it from the presence of a subform, nothing more.
        Nor is the mapping's `is_computed_value`, which is a display flag —
        `_normalize_mcl_derivations` overwrites four fields on consecutive
        lines and only one of them carries it, while `meets_gelf` and
        `meets_lugano` carry it and are plain stored booleans nothing
        derives. A draft of this change exposed that flag as `ucomputed`
        and would have hidden the only control that can set those two while
        offering one over `tnbc_status`, which the next save undoes.

        So EXACT does not answer it yet and this key does not pretend to:
        making the overwrite set explicit in `normalize.py` is #449. Until
        then an editing client should treat a value whose provenance it
        cannot see as provisional.

        Two further traps the name alone does not close, both for the
        client: a `×ULN` row inherits the base field's `ufield`, so editing
        it would write the raw lab value; and `None` covers two different
        situations — a trial-only row with nothing to write ever, and a row
        whose data exists but not under one field (the therapy lines).

        Stamped here rather than in `get_field` because rows are built in
        three places — `get_field`, `computed_general_fields`, `therapies` —
        and a key added to one is a key the others quietly lack, which a
        client cannot tell from "this row is about nothing".

        And the name is checked, not trusted: `therapies` puts the TRIAL
        attribute in `ufield`, so `therapiesRequired` would have become
        `therapies_required`, which is not a patient attribute at all.
        """
        for field in fields.values():
            if not isinstance(field, dict):
                continue
            ufield = field.get('ufield')
            patient_field = AttributeNames.get_by_camel_case(ufield) if ufield else None
            field['upatientField'] = (
                patient_field if patient_field in USER_TO_TRIAL_ATTRS_MAPPING else None
            )
            # The negated row's canonical answer, with the availability the
            # raw boundary saw (#5667). `uvalue` cannot carry this: null means
            # both "answered unknown" and "never in the context". A separate
            # key, not `subform_details`, so an old client does not read it as
            # a computed subform.
            canonical = PROMOP_NEGATED_FIELDS.get(field['upatientField'])
            source = (negated_sources or {}).get(canonical) if canonical else None
            if source is not None:
                field['negative_source'] = source
            # Whether a stored value for it survives. EXACT recomputes these
            # on every match from the inputs it was given, so a write upstream
            # is replaced before the matcher ever sees it — accepted, then
            # undone, with no error anywhere (#449). A client that offers an
            # edit box for one of them is offering a control that does
            # nothing, which is worse than offering none.
            #
            # `recompute_note` rather than the set alone, because the set is
            # what this module WRITES and that is not the whole question.
            # `abnormal_kappa_lambda_ratio` and `meets_meas_or_bone_status`
            # are properties with no column and no setter — recomputed on
            # every read, and dropped from an inbound payload before they
            # reach the instance. They are in neither the set nor any
            # condition, so they answered `false` here: "this value is the
            # reader's", over a field an edit cannot reach at all. Worse than
            # the case the key exists for.
            note = recompute_note(field['upatientField'])
            field['upatientRecomputed'] = note is not None
            # The same answer with its reason, for a client that can say more
            # than yes or no. A boolean calls `mipi_risk` recomputed for a
            # breast-cancer patient, whose `mipi_risk` nothing here touches —
            # so the control is hidden from the one reader who could have used
            # it, which is the false positive #449 named on `meets_gelf`.
            field['upatientRecomputedWhen'] = note
        return fields

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
        if goodness_score is None and self._patient_info is not None:  # #318: no patient → leave blank
            goodness_score = self._trial.get_goodness_score(self._patient_info)
        computed_fields['goodnessScore'] = {
          "name": "goodnessScore",
          "label": "goodness Score",
          "type": "string",
          "value": '' if goodness_score is None else str(goodness_score),
          "options": None,
          "ureadonly": True,
          "ufield": None,
          "uvalue": None,
          "utype": "string",
          "uoptions": None,
          "dependencies": []
        }

        match_score = self._trial.get_match_score(self._patient_info) if self._patient_info is not None else None  # #318
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
            self._patient_info.geo_point if self._patient_info is not None else None,  # #318
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

    #: The trial vocabulary that owns each OMOP-mapped therapy level's titles.
    #: Types are deliberately absent: they carry drug-class concept_ids but
    #: EXACT has no curated title table for them (`TherapyComponentCategory.
    #: omop_concept_id` is unused), so the release-pinned mirror is their only
    #: source (#672).
    _OMOP_THERAPY_VOCAB = {
        'therapies_required': 'Therapy',
        'therapies_excluded': 'Therapy',
        'therapy_components_required': 'TherapyComponent',
        'therapy_components_excluded': 'TherapyComponent',
    }

    def _omop_field_ids(self):
        """`{field_name: [concept_id_str]}` for the rows the OMOP profile flips."""
        fields = {}
        for field_name in THERAPIES_ATTRS_UNDERSCORED:
            active_col = getattr(THERAPY_MATCH_PROFILE, field_name, field_name)
            if omop_therapy_enabled() and active_col != field_name:
                fields[field_name] = [
                    str(item) for item in (getattr(self._trial, active_col) or [])
                ]
        return fields

    def _prefetch_omop_therapy_titles(self):
        """Resolve every flipped row's titles once, for the whole detail.

        One query per trial vocabulary (regimens and components share their
        model between the required and excluded rows) plus one mirror call for
        every id neither vocabulary knows, instead of a round per row.
        """
        fields = self._omop_field_ids()
        ids_by_model = {}
        for field_name, ids in fields.items():
            model_name = self._OMOP_THERAPY_VOCAB.get(field_name)
            if model_name:
                ids_by_model.setdefault(model_name, set()).update(
                    int(item) for item in ids if item.isdigit())
        titles_by_model = {}
        if ids_by_model:
            from trials import models as trial_models
            for model_name, numeric in ids_by_model.items():
                model = getattr(trial_models, model_name)
                titles_by_model[model_name] = {
                    str(concept_id): title for concept_id, title in (
                        model.objects.filter(omop_concept_id__in=sorted(numeric))
                        .order_by('id')
                        .values_list('omop_concept_id', 'title')
                    )
                }
        cache = {}
        unresolved = set()
        for field_name, ids in fields.items():
            titles = dict(titles_by_model.get(self._OMOP_THERAPY_VOCAB.get(field_name), {}))
            cache[field_name] = titles
            unresolved.update(item for item in ids if not titles.get(item))
        if unresolved:
            from exact_matching.matcher import _resolve_omop_concepts
            mirrored = {
                str(item['code']): item['title']
                for item in _resolve_omop_concepts(sorted(unresolved))
                if item.get('title')
            }
            for titles in cache.values():
                for concept_id, title in mirrored.items():
                    titles.setdefault(concept_id, title)
        self._omop_title_cache = cache

    def _omop_option_map(self, field_name, value):
        """`[{value, label}]` for a row whose `value` is a list of concept_ids.

        The trial's own vocabulary is the first source: its rows carry both the
        concept id the backfill wrote and the title the legacy option map used,
        so the label does not depend on a mirror release covering the concept.
        The pinned mirror is the fallback (and the only source for therapy
        types). An id neither source knows stays visible as
        "Unknown concept (<id>)" rather than being dropped or inventing a title.
        """
        ids = [str(item) for item in (value or [])]
        cache = getattr(self, '_omop_title_cache', None)
        titles = cache.get(field_name) if cache is not None else None
        if titles is None:
            self._prefetch_omop_therapy_titles()
            titles = getattr(self, '_omop_title_cache', {}).get(field_name, {})
        return [
            {'value': item,
             'label': titles.get(item) or f'Unknown concept ({item})'}
            for item in ids
        ]

    def therapies(self, subform_attrs):
        # Renders the trial's therapy criteria; patient values (uvalue) are None
        # here and the per-criterion match status is filled in by the caller. Runs
        # with or without a patient — without one the criteria still render and the
        # caller marks them 'unknown' (#318), consistent with the other criteria.
        self._prefetch_omop_therapy_titles()
        out = {}
        for field_name in THERAPIES_ATTRS_UNDERSCORED:
            user_value = None
            active_col = getattr(THERAPY_MATCH_PROFILE, field_name, field_name)
            value = getattr(self._trial, active_col)

            trial_field = self._trial.__class__._meta.get_field(field_name)
            trial_field_name = self.get_field_name(trial_field)
            options = self.get_options(trial_field_name)
            if omop_therapy_enabled() and active_col != field_name:
                # Under the OMOP profile the column holds concept_ids while the
                # option map is keyed by the legacy code, so every row rendered a
                # bare number (#672, #680). Resolve the ids to titles and keep an
                # unresolved one visible rather than dropping it or inventing a
                # title. Display-only: these rows are read-only and the editor
                # reads `uoptions`, which stays None.
                options = self._omop_option_map(field_name, value)
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
                # Once per entry, the way the row site does it, rather than
                # once for the boolean and again for the note.
                entry_note = recompute_note(pi_field_name)
                if therapy_line_is_authorable(
                        pi_field_name,
                        getattr(self._patient_info, 'prior_therapy', None)):
                    # The line this field projects is one the patient's own
                    # history already claims, and PROMOP now writes it through
                    # the therapy-line service. It is not recomputed behind the
                    # reader, so the pencil is real.
                    entry_note = None
                if pi_field_name in out:
                    continue

                field = self._patient_info.__class__._meta.get_field(pi_field_name)
                field_name = self.get_field_name(field)
                units_details = patient_info_attr_units_for(pi_field_name, self._patient_info)
                uunits_label = (
                    unit_label(units_details['options'], units_details['uvalue'])
                    if units_details else None
                )
                if units_details and uunits_label is None:
                    # Unknown stored code: keep the matcher's value and attach
                    # no label rather than mislabel it with the default unit.
                    units_details = None
                value = self._patient_info_attr.get_value(pi_field_name)
                if units_details:
                    # The editor shows and writes in the PATIENT's stored unit,
                    # and `uunits` below names that unit. `get_value` converts
                    # measurements into the matcher's unit, so using its number
                    # here would put 12 (g/dL) beside "G/Liter" and submit 12
                    # into a field that expects 120.
                    value = getattr(self._patient_info, pi_field_name)
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
                    # The patient attribute this entry IS — taken from the
                    # mapping rather than derived from `name`, which is the
                    # camelCase spelling and cannot be turned back reliably
                    # (`p53_ihc` camelises to `p53Ihc`, which a snake-caser
                    # returns as `p_53_ihc`). The row's own `upatientField`
                    # has to be derived; this one does not, and taking the
                    # harder route here would be inventing a problem.
                    'upatientField': pi_field_name,
                    # Same question as on a row, and it is asked here for the
                    # opposite reason. A subform exists because the value
                    # above it is COMPUTED: the reader cannot write
                    # `tnbc_status`, they write the receptor statuses it is
                    # computed from. So the entries are the writable part —
                    # except where an entry is itself computed, and then it is
                    # no more writable than the row that opened the dialog.
                    #
                    # Only the therapy groups contain such an entry today:
                    # `first_line_therapy` and its date and outcome are
                    # derived from the therapy lines. Every statically listed
                    # input is raw data. (`creatinine_clearance_rate` looks
                    # like a counter-example and is not one — EXACT reads it
                    # and never writes it; the derived renal value is
                    # `estimated_glomerular_filtration_rate`, which is in no
                    # subform.)
                    #
                    # Through `recompute_note` like the row above, not off the
                    # set: the set is what `normalize.py` WRITES, and a value
                    # that is not stored at all — a read-only property with no
                    # column — is in neither it nor any condition, so reading
                    # the set alone answers `false` for one, which a client
                    # takes as "this is yours to edit" over a field an edit
                    # cannot reach.
                    'upatientRecomputed': entry_note is not None,
                    'upatientRecomputedWhen': entry_note,
                }

                # The unit the PATIENT's value is stored in, the same way a row
                # carries it. Without this a subform prints a bare number and
                # an editor falls back to the descriptor's unit, which is the
                # vocabulary's and not this patient's — and CRAB, whose inputs
                # are exactly these labs, converts through the stored one. A
                # calcium typed in the wrong scale flips a clinical composite
                # with nothing on screen to show which scale was meant.
                if units_details:
                    val['uunits'] = uunits_label
                    val['units'] = unit_label(units_details['options'], units_details['default'])

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

        def user_value_for_trial(user_attr):
            # A trial can state its own adequacy cutoffs; the stored patient-level
            # boolean only encodes the defaults (ported from CB, #630).
            if user_attr == 'haematological_adequacy_status':
                return self._patient_info_attr.haematological_adequacy_status_for_trial(self._trial)
            return self._patient_info_attr.get_value(user_attr)

        for user_attr in mapping.keys():
            trial_attr_meta = mapping[user_attr]

            subform_details = subform_attrs[user_attr] if user_attr in subform_attrs else None
            ureadonly = ('is_computed_value' in trial_attr_meta and trial_attr_meta['is_computed_value'] is True) or subform_details is not None

            if "disease" in trial_attr_meta and (
                self._patient_info_attr.disease_code is None
                or not disease_attr_applies(trial_attr_meta["disease"], self._patient_info_attr.disease_code)
            ):
                continue

            if trial_attr_meta["type"] == "min_value":
                if 'attr_min' in trial_attr_meta:
                    attr_name_min = trial_attr_meta["attr_min"]
                else:
                    attr_name_min = f'{trial_attr_meta["attr"]}_min'
                out[attr_name_min] = {
                    'ureadonly': ureadonly,
                    'ufield': user_attr,
                    'uvalue': self.get_uvalue(f'u{user_attr}', user_value_for_trial(user_attr)),
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
                    'uvalue': self.get_uvalue(f'u{user_attr}', user_value_for_trial(user_attr)),
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
                    'uvalue': self.get_uvalue(f'u{user_attr}', user_value_for_trial(user_attr)),
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
                    'uvalue': self.get_uvalue(f'u{user_attr}', user_value_for_trial(user_attr)),
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

                user_value = user_value_for_trial(user_attr)
                if user_attr == 'hepatic_adequacy_status':
                    user_value = self._patient_info_attr.hepatic_adequacy_status_for_trial(self._trial)
                out[attr_name] = {
                    'ureadonly': ureadonly,
                    'ufield': user_attr,
                    'uvalue': self.get_uvalue(f'u{user_attr}', user_value),
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
