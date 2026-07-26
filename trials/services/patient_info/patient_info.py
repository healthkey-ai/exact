"""
PatientInfo — plain Python data container for patient clinical data.

Previously a Django model (managed=False). Converted to a plain Python class
to eliminate DB table requirements and migrations.
"""
from functools import cached_property

from django.db.models import (
    BooleanField,
    CharField,
    DateField,
    DecimalField,
    FloatField,
    IntegerField,
    JSONField,
    TextField,
)


def _f(field_cls, name, default=None, **kwargs):
    """Create a standalone Django field instance with name/column/default set.

    type(returned_object) == field_cls, which is what the matching engine
    checks when deciding how to handle blank values.
    """
    field = field_cls(**kwargs)
    field.name = name
    field.column = name
    field.attname = name
    field.default = default
    return field


# Field registry — mirrors the Django model's fields in declaration order.
# Each entry is a real Django field instance (no model attached) so that
# type() returns the correct Django field class for existing type-switch code.
_FIELDS = [
    # Core
    _f(CharField, 'external_id', max_length=255),
    _f(TextField, 'status'),
    _f(TextField, 'languages_skills'),
    # Disease block
    _f(IntegerField, 'patient_age'),
    _f(CharField, 'gender', max_length=2),
    _f(FloatField, 'weight'),
    _f(CharField, 'weight_units', default='kg', max_length=2),
    _f(FloatField, 'height'),
    _f(CharField, 'height_units', default='cm', max_length=2),
    _f(FloatField, 'bmi'),
    _f(TextField, 'ethnicity'),
    _f(IntegerField, 'systolic_blood_pressure'),
    _f(IntegerField, 'diastolic_blood_pressure'),
    # Location
    _f(CharField, 'country', max_length=255),
    _f(CharField, 'region', max_length=255),
    _f(CharField, 'postal_code', max_length=20),
    _f(FloatField, 'longitude'),
    _f(FloatField, 'latitude'),
    # Disease & Performance
    _f(TextField, 'disease', default='multiple myeloma'),
    _f(TextField, 'stage'),
    _f(IntegerField, 'karnofsky_performance_score', default=100),
    _f(IntegerField, 'ecog_performance_status'),
    _f(BooleanField, 'no_other_active_malignancies', default=True),
    _f(BooleanField, 'no_pre_existing_conditions'),
    _f(IntegerField, 'peripheral_neuropathy_grade'),
    # Myeloma-specific
    _f(TextField, 'cytogenic_markers'),
    _f(TextField, 'molecular_markers'),
    _f(JSONField, 'stem_cell_transplant_history', default=list),
    _f(BooleanField, 'plasma_cell_leukemia', default=False),
    _f(TextField, 'progression'),
    _f(BooleanField, 'measurable_disease_imwg'),
    # Lymphoma-specific
    _f(TextField, 'gelf_criteria_status'),
    _f(IntegerField, 'flipi_score'),
    _f(TextField, 'flipi_score_options'),
    _f(IntegerField, 'tumor_grade'),
    # Vitals
    _f(IntegerField, 'heartrate'),
    _f(IntegerField, 'heartrate_variability'),
    # Treatment
    _f(TextField, 'prior_therapy'),
    _f(TextField, 'first_line_therapy'),
    _f(DateField, 'first_line_date'),
    _f(TextField, 'first_line_outcome'),
    _f(TextField, 'second_line_therapy'),
    _f(DateField, 'second_line_date'),
    _f(TextField, 'second_line_outcome'),
    _f(TextField, 'later_therapy'),
    _f(JSONField, 'later_therapies', default=list),
    # Component concept_ids supplied directly by PROMOP (promop#189) under OMOP mode.
    # None when the consumer has not sent them; used by component_category_lookup for types.
    _f(JSONField, 'therapy_component_ids', default=None),
    _f(DateField, 'later_date'),
    _f(TextField, 'later_outcome'),
    _f(TextField, 'old_supportive_therapies'),
    _f(JSONField, 'supportive_therapies', default=list),
    _f(DateField, 'supportive_therapy_date'),
    _f(IntegerField, 'relapse_count'),
    _f(CharField, 'treatment_refractory_status', max_length=255),
    _f(DateField, 'last_treatment'),
    # Blood block
    _f(DecimalField, 'absolute_neutrophile_count', decimal_places=2, max_digits=10),
    _f(CharField, 'absolute_neutrophile_count_units', default='CELLS/UL', max_length=10),
    _f(IntegerField, 'platelet_count'),
    _f(CharField, 'platelet_count_units', default='CELLS/UL', max_length=10),
    _f(DecimalField, 'white_blood_cell_count', decimal_places=2, max_digits=10),
    _f(CharField, 'white_blood_cell_count_units', default='CELLS/L', max_length=10),
    _f(DecimalField, 'red_blood_cell_count', decimal_places=2, max_digits=10),
    _f(CharField, 'red_blood_cell_count_units', default='CELLS/L', max_length=10),
    _f(DecimalField, 'serum_calcium_level', decimal_places=2, max_digits=10),
    _f(CharField, 'serum_calcium_level_units', default='MG/DL', max_length=15),
    _f(IntegerField, 'creatinine_clearance_rate'),
    _f(DecimalField, 'serum_creatinine_level', decimal_places=2, max_digits=10),
    _f(CharField, 'serum_creatinine_level_units', default='MG/DL', max_length=15),
    _f(DecimalField, 'hemoglobin_level', decimal_places=2, max_digits=10),
    _f(CharField, 'hemoglobin_level_units', default='G/DL', max_length=5),
    _f(TextField, 'bone_lesions'),
    _f(BooleanField, 'meets_crab'),
    _f(IntegerField, 'estimated_glomerular_filtration_rate'),
    _f(BooleanField, 'renal_adequacy_status', default=False),
    _f(BooleanField, 'hepatic_adequacy_status', default=False),
    _f(BooleanField, 'haematological_adequacy_status', default=False),
    _f(IntegerField, 'liver_enzyme_levels_ast'),
    _f(IntegerField, 'liver_enzyme_levels_alt'),
    _f(IntegerField, 'liver_enzyme_levels_alp'),
    _f(DecimalField, 'serum_bilirubin_level_total', decimal_places=2, max_digits=10),
    _f(CharField, 'serum_bilirubin_level_total_units', default='MG/DL', max_length=15),
    _f(DecimalField, 'serum_bilirubin_level_direct', decimal_places=2, max_digits=10),
    _f(CharField, 'serum_bilirubin_level_direct_units', default='MG/DL', max_length=15),
    _f(DecimalField, 'albumin_level', decimal_places=2, max_digits=10),
    _f(CharField, 'albumin_level_units', default='G/DL', max_length=15),
    _f(IntegerField, 'kappa_flc'),
    _f(IntegerField, 'lambda_flc'),
    _f(BooleanField, 'meets_slim'),
    _f(BooleanField, 'meets_lugano'),
    _f(BooleanField, 'meets_gelf'),
    # Labs block
    _f(DecimalField, 'monoclonal_protein_serum', decimal_places=2, max_digits=10),
    _f(DecimalField, 'monoclonal_protein_urine', decimal_places=2, max_digits=10),
    _f(IntegerField, 'lactate_dehydrogenase_level'),
    _f(BooleanField, 'pulmonary_function_test_result', default=False),
    _f(BooleanField, 'bone_imaging_result', default=False),
    _f(IntegerField, 'clonal_plasma_cells'),
    _f(IntegerField, 'ejection_fraction'),
    # Behavior block
    _f(BooleanField, 'consent_capability', default=True),
    _f(BooleanField, 'caregiver_availability_status', default=False),
    _f(BooleanField, 'contraceptive_use', default=False),
    _f(BooleanField, 'no_pregnancy_or_lactation_status', default=True),
    _f(BooleanField, 'pregnancy_test_result', default=False),
    _f(BooleanField, 'no_mental_health_disorder_status', default=True),
    _f(BooleanField, 'no_concomitant_medication_status', default=True),
    _f(CharField, 'concomitant_medication_details', max_length=255),
    _f(BooleanField, 'no_tobacco_use_status', default=True),
    _f(CharField, 'tobacco_use_details', max_length=255),
    _f(BooleanField, 'no_substance_use_status', default=True),
    _f(CharField, 'substance_use_details', max_length=255),
    _f(BooleanField, 'no_geographic_exposure_risk', default=True),
    _f(CharField, 'geographic_exposure_risk_details', max_length=255),
    _f(BooleanField, 'no_hiv_status', default=True),
    _f(BooleanField, 'no_hepatitis_b_status', default=True),
    _f(BooleanField, 'no_hepatitis_c_status', default=True),
    _f(BooleanField, 'no_active_infection_status', default=True),
    _f(TextField, 'concomitant_medications'),
    _f(DateField, 'concomitant_medication_date'),
    # Breast Cancer
    _f(BooleanField, 'bone_only_metastasis_status', default=False),
    _f(TextField, 'menopausal_status'),
    _f(BooleanField, 'metastatic_status', default=False),
    _f(IntegerField, 'toxicity_grade'),
    _f(TextField, 'planned_therapies'),
    _f(TextField, 'histologic_type'),
    _f(TextField, 'biopsy_grade_depr'),
    _f(IntegerField, 'biopsy_grade'),
    _f(BooleanField, 'measurable_disease_by_recist_status', default=False),
    _f(TextField, 'estrogen_receptor_status'),
    _f(TextField, 'progesterone_receptor_status'),
    _f(TextField, 'her2_status'),
    _f(BooleanField, 'tnbc_status', default=False),
    _f(TextField, 'hrd_status'),
    _f(TextField, 'hr_status'),
    _f(TextField, 'tumor_stage'),
    _f(TextField, 'nodes_stage'),
    _f(TextField, 'distant_metastasis_stage'),
    _f(TextField, 'staging_modalities'),
    # Genetic Mutations
    _f(JSONField, 'genetic_mutations', default=list),
    _f(IntegerField, 'pd_l1_tumor_cels'),
    _f(TextField, 'pd_l1_assay'),
    _f(IntegerField, 'pd_l1_ic_percentage'),
    _f(IntegerField, 'pd_l1_combined_positive_score'),
    _f(IntegerField, 'ki67_proliferation_index'),
    # CLL-specific
    _f(TextField, 'binet_stage'),
    _f(TextField, 'protein_expressions'),
    _f(TextField, 'richter_transformation'),
    _f(TextField, 'tumor_burden'),
    _f(IntegerField, 'lymphocyte_doubling_time'),
    _f(BooleanField, 'tp53_disruption'),
    _f(BooleanField, 'measurable_disease_iwcll'),
    _f(BooleanField, 'hepatomegaly'),
    _f(BooleanField, 'autoimmune_cytopenias_refractory_to_steroids'),
    _f(BooleanField, 'lymphadenopathy'),
    _f(FloatField, 'largest_lymph_node_size'),
    _f(BooleanField, 'splenomegaly'),
    _f(FloatField, 'spleen_size'),
    _f(TextField, 'disease_activity'),
    _f(BooleanField, 'btk_inhibitor_refractory'),
    _f(BooleanField, 'bcl2_inhibitor_refractory'),
    _f(FloatField, 'absolute_lymphocyte_count'),
    _f(FloatField, 'qtcf_value'),
    _f(FloatField, 'serum_beta2_microglobulin_level'),
    _f(FloatField, 'clonal_bone_marrow_b_lymphocytes'),
    _f(IntegerField, 'clonal_b_lymphocyte_count'),
    _f(BooleanField, 'bone_marrow_involvement'),
    # MCL-specific
    # mipi_risk / mipi_c_risk / bulky_disease_criteria / high_risk_mcl_criteria
    # are derived in normalize._normalize_mcl_derivations from age / ECOG / WBC /
    # LDH / Ki67 / markers / sizes (#41). Caller-supplied values are overwritten
    # when the patient's disease is MCL.
    _f(TextField, 'morphologic_variant'),
    _f(FloatField, 'largest_lesion_size'),
    _f(IntegerField, 'p53_ihc'),  # p53 IHC expression %, range 0-100
    _f(TextField, 'disease_behavior'),
    _f(TextField, 'disease_subtype'),
    _f(JSONField, 'extranodal_sites', default=list),
    _f(TextField, 'mipi_risk'),
    _f(TextField, 'mipi_c_risk'),
    _f(TextField, 'bulky_disease_criteria'),
    _f(TextField, 'high_risk_mcl_criteria'),
]

_FIELD_MAP = {f.name: f for f in _FIELDS}

# Pre-computed defaults dict (callable defaults are expanded at __init__ time)
_DEFAULTS = {f.name: f.default for f in _FIELDS}


class _Meta:
    """Minimal replica of Django's Options used by the matching-engine internals."""

    def get_field(self, name):
        try:
            return _FIELD_MAP[name]
        except KeyError:
            from django.core.exceptions import FieldDoesNotExist
            raise FieldDoesNotExist(f"PatientInfo has no field named '{name}'")

    def get_fields(self, include_parents=True, include_hidden=False):
        return list(_FIELDS)

    app_label = 'trials'
    model_name = 'patientinfo'


class PatientInfo:
    """
    In-memory data container for patient clinical data.

    Constructed by resolve_patient_info() from API request JSON, then passed
    through normalize_patient_info() to fill in computed fields.

    Provides a _meta shim so existing matching-engine code that calls
    _meta.get_field() and _meta.get_fields() continues to work without change.
    """

    _meta = _Meta()

    def __init__(self, **kwargs):
        # Apply field defaults first
        for name, default in _DEFAULTS.items():
            if callable(default):
                setattr(self, name, default())
            else:
                setattr(self, name, default)
        # geo_point is PostGIS-only; not in _FIELDS, default None
        self.geo_point = None
        # Override with caller-supplied values
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __str__(self):
        return f"PatientInfo(age={self.patient_age}, gender={self.gender})"

    @cached_property
    def attributes_service(self):
        from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes
        return PatientInfoAttributes(self)

    @cached_property
    def geolocation(self):
        return self

    @cached_property
    def mutation_genes(self):
        from trials.services.patient_info.genetic_mutations import GeneticMutations
        return GeneticMutations.mutation_genes(self.genetic_mutations)

    @cached_property
    def mutation_variants(self):
        from trials.services.patient_info.genetic_mutations import GeneticMutations
        return GeneticMutations.mutation_variants(self.genetic_mutations)

    @cached_property
    def mutation_origins(self):
        from trials.services.patient_info.genetic_mutations import GeneticMutations
        return GeneticMutations.mutation_origins(self.genetic_mutations)

    @cached_property
    def mutation_interpretations(self):
        from trials.services.patient_info.genetic_mutations import GeneticMutations
        return GeneticMutations.mutation_interpretations(self.genetic_mutations)

    @property
    def abnormal_kappa_lambda_ratio(self):
        return self.attributes_service.abnormal_kappa_lambda_ratio

    @property
    def meets_meas_or_bone_status(self):
        return self.attributes_service.meets_meas_or_bone_status
