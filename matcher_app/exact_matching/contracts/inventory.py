"""Frozen #660 inventory, not a compiler, live registry, or readiness declaration.

CB owns the meanings; changing this snapshot requires reviewing its producer.
No imports from CB, PROMOP models, or the legacy matcher are needed to read it.
"""
from dataclasses import dataclass


CB_PRODUCER = '480dbf53a1e544911cb611baca9a67c3a1b715f1'
CB_CONSUMER = '78b91f66ce0d7fa8e80f2d4162fed10a8c2107c3'
PROMOP_PRODUCER = 'c7138fab05a4e2fe865d0f210770aaf927fb13ba'
REGISTRY_VERSION = 'cb-trial-criteria-1'
APPLICABILITY_SOURCE = (
    'exact@a0d256902bb658e78a49d542b2caf8c954de4290:'
    'matcher_app/exact_matching/patient_info/configs.py:USER_TO_TRIAL_ATTRS_MAPPING'
)
GENOMIC_GROUP = (
    'mutation_genes_required', 'mutation_variants_required',
    'mutation_origins_required', 'mutation_interpretations_required',
)
MCL_GROUP = (
    'high_risk_mcl_criteria_required', 'high_risk_mcl_criteria_excluded',
    'high_risk_mcl_criteria_sufficient_any',
)


@dataclass(frozen=True)
class EvidenceSource:
    operands: tuple[str, ...]
    physical_source: str
    unresolved_gates: tuple[str, ...]
    owner: str = 'PROMOP'


SOURCES = {
    'therapy': EvidenceSource(
        ('first_line_therapy_id', 'second_line_therapy_id', 'later_therapy_ids',
         'therapy_component_ids', 'therapy_type_ids', 'therapy_history_complete',
         'therapy_ids_provenance'),
        'Episode/EpisodeEvent/DrugExposure plus TherapyHistoryAttestation; '
        'reference regimen/class membership is not patient administration',
        ('promop#1070', 'promop#1072', 'promop#1924', 'promop#2124')),
    'planned': EvidenceSource(
        ('planned_therapies',), 'PatientRecord pending edit or planned-treatment assertion, NOT administration',
        ('promop#1070', 'promop#2124')),
    'supportive': EvidenceSource(
        ('supportive_therapies', 'supportive_therapy_start_date', 'supportive_therapy_end_date'),
        'PatientRecord pending edit or explicitly scoped supportive-treatment event',
        ('promop#1070', 'promop#2124')),
    'condition': EvidenceSource(
        ('preexisting_conditions', 'no_pre_existing_conditions', 'disease'),
        'ConditionOccurrence with index malignancy/activity context and explicit assessment evidence',
        ('CB#5352', 'promop#1988', 'promop#2124')),
    'sct': EvidenceSource(
        ('sct_date', 'stem_cell_transplant_history', 'prior_therapy'),
        'Procedure/Observation/Condition event plus type, dates, history assessment and disease scope',
        ('CB#5354', 'promop#2124')),
    'scalar': EvidenceSource(
        (), 'PatientRecord value/pending-edit provenance or exact Measurement/Observation '
        'question+source key+physical answer; neither label nor question alone proves a result',
        ('promop#1224', 'promop#2124')),
    'concept': EvidenceSource(
        (), 'Person/Condition/Observation evidence with role, mapping and vocabulary identity; '
        'a PatientRecord summary string alone is not concept evidence',
        ('CB#5619', 'promop#2124')),
    'genomics': EvidenceSource(
        ('genetic_mutations',), 'Measurement parent and same-person event-typed linked '
        'Measurement/Observation components; source-only concept 0 remains unmapped evidence',
        ('promop#1229', 'promop#1240', 'promop#2124')),
    'medication': EvidenceSource(
        ('concomitant_medications', 'concomitant_medication_date'),
        'DrugExposure or explicit concomitant-treatment assertion with protocol/date scope',
        ('CB#5353', 'promop#2124')),
    'language': EvidenceSource(
        ('english_read', 'english_write', 'english_speak', 'english_understand',
         'spanish_read', 'spanish_write', 'spanish_speak', 'spanish_understand'),
        'PatientRecord capability booleans; EXACT-owned transitional pair builder '
        'uses Language/LanguageSkillLevel IDs and retains asked-language scope',
        ('CB#5356',), owner='PROMOP capabilities / EXACT transitional pairs'),
    'counted': EvidenceSource(
        ('largest_lesion_size', 'largest_lymph_node_size', 'spleen_size', 'absolute_lymphocyte_count',
         'p53_ihc', 'ki67_proliferation_index', 'ecog_performance_status', 'patient_age',
         'lactate_dehydrogenase_level', 'lactate_dehydrogenase_level_uln',
         'white_blood_cell_count', 'genetic_mutations', 'morphologic_variant'),
        'Typed unit-bearing operands / linked finding assertions; '
        'CB rule identities and versioned derivation, not a flattened score-only result',
        ('CB#5343', 'promop#1981', 'exact#657', 'promop#2124')),
    'bulky': EvidenceSource(
        ('largest_lesion_size', 'largest_lymph_node_size', 'spleen_size'),
        'Typed size operands with exact question, units and date scope; CB SIZE_RULES identities',
        ('promop#1981', 'promop#2124')),
    'unbound_scalar': EvidenceSource(
        (), 'Encoded approved finding where applicable; otherwise the current CB '
        'ANSWER_REFERENCE_TABLES has no literal catalog binding for this family. '
        'MIPI risk columns also are not stored on this PatientRecord revision; '
        'do not invent a scalar recipe or duplicate the attributes formula',
        ('CB#5619', 'promop#1224', 'exact#657', 'promop#2124')),
}


@dataclass(frozen=True)
class CriterionSpec:
    field: str
    family: str
    operator: str
    semantic_role: str
    matcher_operand: str
    source: str
    applicability: tuple[str, ...] = ('all',)
    recipe: str = ''
    companions: tuple[str, ...] = ()
    group_fields: tuple[str, ...] = ()

    @property
    def role(self):
        if self.field.endswith('_sufficient_any'):
            return 'sufficient_any'
        return 'excluded' if self.field.endswith('_excluded') else 'required'

    @property
    def representation(self):
        return self.operator if self.operator in ('concept_set', 'language_pair') else 'envelope'

    @property
    def dependency_closure(self):
        return tuple(sorted({self.field, *self.companions, *self.group_fields}))

    @property
    def evidence_source(self):
        return SOURCES[self.source]

    @property
    def applicability_source(self):
        return APPLICABILITY_SOURCE

    @property
    def semantic_owner(self):
        return 'CB criterion semantics; exact_matching shared contract; PROMOP clinical evidence'

    @property
    def actual_consumer_capability(self):
        return 'Unified v1 contract not consumed at CB promop pins; existing therapy/language paths are separate'

    @property
    def source_operands(self):
        return tuple(dict.fromkeys((self.matcher_operand, *self.evidence_source.operands)))


# Explicit 55 entries; never discover scope from the current config at runtime.
CRITERIA = (
    CriterionSpec('planned_therapies_required', 'plannedtherapy', 'concept_set', 'planned_treatment', 'planned_therapies', 'planned'),
    CriterionSpec('planned_therapies_excluded', 'plannedtherapy', 'concept_set', 'planned_treatment', 'planned_therapies', 'planned'),
    CriterionSpec('therapies_required', 'therapy', 'concept_set', 'regimen', 'therapies', 'therapy'),
    CriterionSpec('therapies_excluded', 'therapy', 'concept_set', 'regimen', 'therapies', 'therapy'),
    CriterionSpec('therapy_types_required', 'therapycomponentcategory', 'concept_set', 'therapy_class', 'therapy_types', 'therapy'),
    CriterionSpec('therapy_types_excluded', 'therapycomponentcategory', 'concept_set', 'therapy_class', 'therapy_types', 'therapy'),
    CriterionSpec('therapy_components_required', 'therapycomponent', 'concept_set', 'component', 'therapy_components', 'therapy'),
    CriterionSpec('therapy_components_excluded', 'therapycomponent', 'concept_set', 'component', 'therapy_components', 'therapy'),
    CriterionSpec('supportive_therapies_required', 'therapy', 'concept_set', 'supportive_treatment', 'supportive_therapies', 'supportive'),
    CriterionSpec('supportive_therapies_excluded', 'therapy', 'concept_set', 'supportive_treatment', 'supportive_therapies', 'supportive'),
    CriterionSpec('pre_existing_conditions_excluded', 'preexistingconditioncategory', 'condition', 'condition_with_qualifiers', 'pre_existing_condition_categories', 'condition', companions=('disease',)),
    CriterionSpec('stem_cell_transplant_history_excluded', 'stemcelltransplant', 'temporal_sct', 'transplant_history', 'stem_cell_transplant_history', 'sct', ('MM', 'FL'), companions=('day_since_stem_cell_transplant_required', 'stem_cell_transplant_history_required')),
    CriterionSpec('ethnicity_required', 'ethnicity', 'concept_set', 'race_or_ethnicity_answer', 'ethnicity', 'concept'),
    CriterionSpec('cytogenic_markers_required', 'cytogenicmarker', 'finding', 'asserted_finding', 'cytogenic_markers', 'genomics', ('MM', 'CLL', 'MCL'), recipe='genetic_mutations.status'),
    CriterionSpec('cytogenic_markers_excluded', 'cytogenicmarker', 'finding', 'asserted_finding', 'cytogenic_markers', 'genomics', ('MM', 'CLL', 'MCL'), recipe='genetic_mutations.status'),
    CriterionSpec('molecular_markers_required', 'molecularmarker', 'finding', 'asserted_finding', 'molecular_markers', 'genomics', ('MM', 'CLL', 'MCL'), recipe='genetic_mutations.status'),
    CriterionSpec('molecular_markers_excluded', 'molecularmarker', 'finding', 'asserted_finding', 'molecular_markers', 'genomics', ('MM', 'CLL', 'MCL'), recipe='genetic_mutations.status'),
    CriterionSpec('histologic_types_required', 'histologictype', 'concept_set', 'histology', 'histologic_type', 'concept', ('BC',)),
    CriterionSpec('estrogen_receptor_statuses_required', 'estrogenreceptorstatus', 'coded_result', 'coded_answer', 'estrogen_receptor_status', 'scalar', ('BC',), recipe='estrogen_receptor_status'),
    CriterionSpec('progesterone_receptor_statuses_required', 'progesteronereceptorstatus', 'coded_result', 'coded_answer', 'progesterone_receptor_status', 'scalar', ('BC',), recipe='progesterone_receptor_status'),
    CriterionSpec('her2_statuses_required', 'her2status', 'coded_result', 'coded_answer', 'her2_status', 'scalar', ('BC',), recipe='her2_status'),
    CriterionSpec('hrd_statuses_required', 'hrdstatus', 'coded_result', 'coded_answer', 'hrd_status', 'scalar', ('BC',), recipe='hrd_status'),
    CriterionSpec('hr_statuses_required', 'hrstatus', 'coded_result', 'coded_answer', 'hr_status', 'scalar', ('BC',), recipe='hr_status'),
    CriterionSpec('tumor_stages_required', 'tumorstage', 'coded_result', 'coded_answer', 'tumor_stage', 'scalar', ('BC',), recipe='tumor_stage'),
    CriterionSpec('tumor_stages_excluded', 'tumorstage', 'coded_result', 'coded_answer', 'tumor_stage', 'scalar', ('BC',), recipe='tumor_stage'),
    CriterionSpec('nodes_stages_required', 'nodesstage', 'coded_result', 'coded_answer', 'nodes_stage', 'scalar', ('BC',), recipe='nodes_stage'),
    CriterionSpec('nodes_stages_excluded', 'nodesstage', 'coded_result', 'coded_answer', 'nodes_stage', 'scalar', ('BC',), recipe='nodes_stage'),
    CriterionSpec('distant_metastasis_stages_required', 'distantmetastasisstage', 'coded_result', 'coded_answer', 'distant_metastasis_stage', 'scalar', ('BC',), recipe='distant_metastasis_stage'),
    CriterionSpec('distant_metastasis_stages_excluded', 'distantmetastasisstage', 'coded_result', 'coded_answer', 'distant_metastasis_stage', 'scalar', ('BC',), recipe='distant_metastasis_stage'),
    CriterionSpec('staging_modalities_required', 'stagingmodality', 'coded_result', 'coded_answer', 'staging_modalities', 'scalar', ('BC',), recipe='staging_modalities'),
    CriterionSpec('mutation_genes_required', 'mutationgene', 'genomic_subject', 'genomic_subject_not_result', 'genetic_mutations', 'genomics', ('BC',), group_fields=GENOMIC_GROUP),
    CriterionSpec('mutation_variants_required', 'mutationcode', 'genomic_subject', 'genomic_subject_not_result', 'genetic_mutations', 'genomics', ('BC',), group_fields=GENOMIC_GROUP),
    CriterionSpec('mutation_origins_required', 'mutationorigin', 'bound_origin', 'genomic_origin', 'genetic_mutations', 'genomics', ('BC',), recipe='genetic_mutations.origin', group_fields=GENOMIC_GROUP),
    CriterionSpec('mutation_interpretations_required', 'mutationinterpretation', 'bound_interpretation', 'genomic_interpretation', 'genetic_mutations', 'genomics', ('BC',), recipe='genetic_mutations.interpretation', group_fields=GENOMIC_GROUP),
    CriterionSpec('concomitant_medications_excluded', 'concomitantmedication', 'medication', 'concomitant_treatment', 'concomitant_medications', 'medication', companions=('concomitant_medications_washout_period_duration',)),
    CriterionSpec('languages_skills_required', 'language', 'language_pair', 'language_skill_pair', 'languages_skills', 'language'),
    CriterionSpec('stages', 'cancerstage', 'coded_result', 'coded_answer', 'stage', 'scalar', recipe='stage'),
    CriterionSpec('binet_stages_required', 'binetstage', 'coded_result', 'coded_answer', 'binet_stage', 'scalar', ('CLL',), recipe='binet_stage'),
    CriterionSpec('protein_expressions_required', 'proteinexpression', 'coded_result', 'coded_answer', 'protein_expressions', 'scalar', ('CLL', 'MCL', 'FL'), recipe='protein_expressions'),
    CriterionSpec('protein_expressions_excluded', 'proteinexpression', 'coded_result', 'coded_answer', 'protein_expressions', 'scalar', ('CLL', 'MCL', 'FL'), recipe='protein_expressions'),
    CriterionSpec('richter_transformations_required', 'richtertransformation', 'concept_set', 'transformation', 'richter_transformation', 'concept', ('CLL',)),
    CriterionSpec('richter_transformations_excluded', 'richtertransformation', 'concept_set', 'transformation', 'richter_transformation', 'concept', ('CLL',)),
    CriterionSpec('tumor_burdens_required', 'tumorburden', 'coded_result', 'coded_answer', 'tumor_burden', 'scalar', ('CLL',), recipe='tumor_burden'),
    CriterionSpec('disease_activities_required', 'diseaseactivity', 'coded_result', 'coded_answer', 'disease_activity', 'scalar', ('CLL',), recipe='disease_activity'),
    CriterionSpec('morphologic_variants_required', 'morphologicvariant', 'concept_set', 'morphology', 'morphologic_variant', 'concept', ('MCL',)),
    CriterionSpec('morphologic_variants_excluded', 'morphologicvariant', 'concept_set', 'morphology', 'morphologic_variant', 'concept', ('MCL',)),
    CriterionSpec('disease_behaviors_required', 'diseasebehavior', 'coded_result', 'coded_answer', 'disease_behavior', 'unbound_scalar', ('MCL',), recipe='disease_behavior'),
    CriterionSpec('disease_subtypes_required', 'diseasesubtype', 'coded_result', 'coded_answer', 'disease_subtype', 'unbound_scalar', ('MCL',), recipe='disease_subtype'),
    CriterionSpec('mipi_risks_required', 'mipirisk', 'coded_result', 'coded_answer', 'mipi_risk', 'unbound_scalar', ('MCL',), recipe='mipi_risk'),
    CriterionSpec('mipi_c_risks_required', 'mipicrisk', 'coded_result', 'coded_answer', 'mipi_c_risk', 'unbound_scalar', ('MCL',), recipe='mipi_c_risk'),
    CriterionSpec('extranodal_sites_required', 'extranodalsite', 'concept_set', 'site', 'extranodal_sites', 'concept', ('MCL',)),
    CriterionSpec('bulky_disease_criteria_required', 'bulkydiseasecriteria', 'counted_rule', 'derived_size_rule', 'bulky_disease_criteria', 'bulky', ('MCL',)),
    CriterionSpec('high_risk_mcl_criteria_required', 'highriskmclcriteria', 'counted_rule', 'derived_or_asserted_rule', 'high_risk_mcl_criteria', 'counted', ('MCL',), recipe='genetic_mutations.status', companions=('high_risk_mcl_criteria_min_count',), group_fields=MCL_GROUP),
    CriterionSpec('high_risk_mcl_criteria_excluded', 'highriskmclcriteria', 'counted_rule', 'derived_or_asserted_rule', 'high_risk_mcl_criteria', 'counted', ('MCL',), recipe='genetic_mutations.status', companions=('high_risk_mcl_criteria_min_count',), group_fields=MCL_GROUP),
    CriterionSpec('high_risk_mcl_criteria_sufficient_any', 'highriskmclcriteria', 'counted_rule', 'derived_or_asserted_rule', 'high_risk_mcl_criteria', 'counted', ('MCL',), recipe='genetic_mutations.status', companions=('high_risk_mcl_criteria_min_count',), group_fields=MCL_GROUP),
)
