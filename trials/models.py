import inflection

from django.core.validators import MaxValueValidator
from django.db import models
from django.contrib.gis.db.models import PointField
from django.contrib.gis.db.models.functions import Distance
from geopy.distance import distance as geopy_distance
from django.contrib.postgres.indexes import GinIndex, GistIndex, Index
from django.contrib.postgres.search import SearchVector
from django.db.models import TextField, JSONField, Case, When, Value, IntegerField, F, Q
from django.db.models.functions import Length, Lower
from django.contrib.postgres.fields import ArrayField
from django.contrib.gis.geos import Point

from django.utils.functional import cached_property

from trials.querysets.trial import TrialQuerySet
from trials.constants import TRIAL_SCORE_MAX
from trials.enums import PriorTherapyLines
from trials.services.patient_info.convertors.base_convertor import BaseConvertor
from trials.services.patient_info.convertors.egfr_calculator import EgfrCalculator
from trials.services.trial_details.trial_templates import TrialTemplates
from trials.services.user_to_trial_attr_matcher import UserToTrialAttrMatcher
from trials.services.user_to_trial_attrs_mapper import UserToTrialAttrsMapper

TextField.register_lookup(Length, 'length')


class TimeStampMixin(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        abstract = True


class RawDataItem(TimeStampMixin):
    record_id = models.CharField(unique=True, max_length=100)
    source_name = models.CharField(max_length=50)
    raw_data = models.TextField(blank=True, null=True)
    old_raw_data = models.JSONField(default=dict, blank=True)
    extracted_data = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.record_id} from '{self.source_name}'"


class Country(TimeStampMixin):
    title = models.TextField(blank=False, null=False, db_index=True, unique=True)
    sort_key = models.IntegerField(blank=True, null=True)

    def __str__(self):
        return self.title


class State(TimeStampMixin):
    country = models.ForeignKey(Country, models.SET_NULL, blank=True, null=True)
    title = models.TextField(blank=False, null=False)

    class Meta:
        unique_together = ['title', 'country']

    def __str__(self):
        return self.title


class Location(TimeStampMixin):
    city = models.TextField(blank=False, null=False)
    title = models.TextField(blank=False, null=False, db_index=True, unique=True)
    state = models.ForeignKey(State, models.CASCADE, blank=True, null=True)
    country = models.ForeignKey(Country, models.CASCADE, blank=True, null=True)
    geo_point = PointField(blank=True, null=True, srid=4326, geography=True)

    class Meta:
        indexes = [
            GistIndex(fields=['geo_point']),
        ]

    def __str__(self):
        return self.title


class LocationTrial(TimeStampMixin):
    location = models.ForeignKey(Location, models.CASCADE, blank=True, null=True)
    trial = models.ForeignKey('Trial', models.CASCADE, blank=True, null=True)
    is_recruiting = models.BooleanField(null=False, default=False)
    recruitment_status = models.TextField(default='')
    location_contacts = models.JSONField(blank=True, default=list)

    class Meta:
        unique_together = ['location', 'trial']
        indexes = [
            Index(fields=['trial', 'location']),
        ]


class Disease(TimeStampMixin):
    code = models.TextField(blank=False, null=False, db_index=True, unique=True)
    title = models.TextField(blank=False, null=False, db_index=True, unique=True)

    def __str__(self):
        return self.title


class Marker(TimeStampMixin):
    code = models.TextField(blank=False, null=False, db_index=True, unique=True)
    title = models.TextField(blank=False, null=False, db_index=True, unique=True)
    description = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.title

    categories = models.ManyToManyField(
        'MarkerCategory',
        blank=True,
        through='MarkerCategoryConnection',
        through_fields=('marker', 'category'),
        related_name='category_markers'
    )


class MarkerCategory(TimeStampMixin):
    code = models.TextField(blank=False, null=False, db_index=True, unique=True)
    title = models.TextField(blank=False, null=False)

    def __str__(self):
        return self.title


class MarkerCategoryConnection(TimeStampMixin):
    marker = models.ForeignKey(Marker, models.CASCADE, blank=True, null=True)
    category = models.ForeignKey(MarkerCategory, models.CASCADE, blank=True, null=True)

    class Meta:
        unique_together = ['marker', 'category']


class ConcomitantMedication(TimeStampMixin):
    code = models.TextField(blank=False, null=False, db_index=True, unique=True)
    title = models.TextField(blank=False, null=False, db_index=True, unique=True)

    def __str__(self):
        return self.title

    diseases = models.ManyToManyField(
        'Disease',
        blank=True,
        through='ConcomitantMedicationDisease',
        through_fields=('concomitant_medication', 'disease'),
        related_name='disease_concomitant_medications'
    )


class ConcomitantMedicationDisease(TimeStampMixin):
    concomitant_medication = models.ForeignKey(ConcomitantMedication, models.CASCADE, blank=True, null=True)
    disease = models.ForeignKey(Disease, models.CASCADE, blank=True, null=True)

    class Meta:
        unique_together = ['concomitant_medication', 'disease']


class Therapy(TimeStampMixin):
    code = models.TextField(blank=False, null=False, db_index=True, unique=True)
    title = models.TextField(blank=False, null=False, db_index=True, unique=True)
    description = models.TextField(blank=True, null=True)
    omop_concept_id = models.BigIntegerField(blank=True, null=True, db_index=True, help_text="OMOP concept_id for this code (pinned PROMOP release); null when the regimen has no clean standard concept and is expanded into its components/classes at backfill. Source for the trial omop_* columns.")

    def full_title(self):
        # Sort in Python so a `prefetch_related('components')` cache is reused.
        # `.order_by('id')` would issue a fresh query per therapy (an N+1 that
        # dominated ValueOptions.all_options() — ~288 queries), since ordering
        # builds a new queryset that ignores the prefetched cache.
        components = ", ".join(
            c.title for c in sorted(self.components.all(), key=lambda c: c.id)
        )
        return f"{self.title} ({components})" if components else self.title

    def __str__(self):
        return self.title

    components = models.ManyToManyField(
        'TherapyComponent',
        blank=True,
        through='TherapyComponentConnection',
        through_fields=('therapy', 'component'),
        related_name='component_therapies'
    )


class TherapyRound(TimeStampMixin):
    code = models.TextField(blank=False, null=False, db_index=True, unique=True)
    title = models.TextField(blank=False, null=False)

    def __str__(self):
        return self.title


class TherapyRoundConnection(TimeStampMixin):
    therapy = models.ForeignKey(Therapy, models.CASCADE, blank=True, null=True)
    round = models.ForeignKey(TherapyRound, models.CASCADE, blank=True, null=True)

    class Meta:
        unique_together = ['therapy', 'round']


class DiseaseRoundTherapyConnection(TimeStampMixin):
    disease = models.ForeignKey(Disease, models.CASCADE, blank=True, null=True)
    round = models.ForeignKey(TherapyRound, models.CASCADE, blank=True, null=True)
    therapy = models.ForeignKey(Therapy, models.CASCADE, blank=True, null=True)

    class Meta:
        unique_together = ['disease', 'round', 'therapy']


class TherapyComponent(TimeStampMixin):
    code = models.TextField(blank=False, null=False, db_index=True, unique=True)
    title = models.TextField(blank=False, null=False)
    omop_concept_id = models.BigIntegerField(blank=True, null=True, db_index=True, help_text="OMOP concept_id for this drug component (pinned PROMOP release); null when unmapped. Source for the trial omop_therapy_components_* columns.")

    def __str__(self):
        return self.title

    categories = models.ManyToManyField(
        'TherapyComponentCategory',
        blank=True,
        through='TherapyComponentCategoryConnection',
        through_fields=('component', 'category'),
        related_name='category_components'
    )


class TherapyComponentConnection(TimeStampMixin):
    therapy = models.ForeignKey(Therapy, models.CASCADE, blank=True, null=True)
    component = models.ForeignKey(TherapyComponent, models.CASCADE, blank=True, null=True)

    class Meta:
        unique_together = ['therapy', 'component']


class TherapyComponentCategory(TimeStampMixin):
    code = models.TextField(blank=False, null=False, db_index=True, unique=True)
    title = models.TextField(blank=False, null=False, db_index=True, unique=True)
    omop_concept_id = models.BigIntegerField(blank=True, null=True, db_index=True, help_text="Unused: drug-class 'types' are not OMOP-mapped (EXACT ADR 0001 decision A, #4502). Types resolve via the component concept_id -> category code lookup (#4503), not a class concept_id.")

    def __str__(self):
        return self.title


class TherapyComponentCategoryConnection(TimeStampMixin):
    category = models.ForeignKey(TherapyComponentCategory, models.CASCADE, blank=True, null=True)
    component = models.ForeignKey(TherapyComponent, models.CASCADE, blank=True, null=True)

    class Meta:
        unique_together = ['category', 'component']


class OmopConcept(TimeStampMixin):
    """OMOP concept_id → display title + vocabulary.

    Resolves the concept_ids stored in the trial ``omop_*`` therapy columns (and
    patient ``*_therapy_id``) to human-readable names for the API — the runtime title
    projection for RUNTIME-APPLICABLE concepts (regimen / component). Drug-class "type"
    concepts are NOT projected here (ADR-A/#4502, #4580): their crosswalk rows are
    ``crosswalk_only`` and keep their ``omop_name`` for audit in
    :class:`TherapyOmopMapping` only. Populated from the curated
    ``therapy_omop_mapping.csv`` by ``load_therapy_omop_concept_ids``.
    Ported from CancerBot (CB owns the upstream); EXACT reads/populates it locally.
    """
    concept_id = models.BigIntegerField(primary_key=True)
    concept_name = models.TextField(blank=False, null=False)
    vocabulary_id = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.concept_id}: {self.concept_name}"


class TherapyOmopMapping(TimeStampMixin):
    """Per-row cb↔OMOP therapy crosswalk, materialized from the curated
    ``docs/omop/mapping/therapy_omop_mapping.csv`` by
    ``load_therapy_omop_concept_ids``.

    One row per ``(level, cb_code)`` — the authoritative, queryable/auditable
    record behind the ``omop_concept_id`` written onto the vocab models and the
    :class:`OmopConcept` titles. Unlike those, it keeps the *unmapped* rows
    (``needs_review`` / ``no_omop``, null ``omop_concept_id``) so coverage / SME
    gaps are visible in the DB, not just in the file. ``omop_concept_id`` is a
    plain (indexed) column, not a FK, so unreviewed rows and concepts not yet in
    :class:`OmopConcept` can coexist without ordering/integrity coupling.
    Ported from CancerBot (CB owns the upstream, #4476); EXACT reads/populates it locally.
    """
    LEVEL_CHOICES = [('regimen', 'regimen'), ('component', 'component'), ('category', 'category')]
    # auto/curated/llm carry a PROMOP-verified concept_id that IS loaded as a runtime vocab
    # mapping; needs_review/no_omop carry none. crosswalk_only/deferred carry a concept_id
    # for AUDIT ONLY and are NOT runtime-applicable (#4580): crosswalk_only = category rows
    # (drug-class types are not OMOP-mapped, ADR-A/#4502); deferred = component codes not yet
    # authored in the vocab (Finding B) that would become applicable if the code is added.
    MATCH_CHOICES = [
        ('auto', 'auto'), ('curated', 'curated'), ('llm', 'llm'),
        ('needs_review', 'needs_review'), ('no_omop', 'no_omop'),
        ('crosswalk_only', 'crosswalk_only'), ('deferred', 'deferred'),
    ]
    level = models.CharField(max_length=16, choices=LEVEL_CHOICES)
    cb_code = models.CharField(max_length=255)
    omop_concept_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    omop_name = models.TextField(blank=True, null=True)
    omop_vocab = models.TextField(blank=True, null=True)
    match = models.CharField(max_length=16, choices=MATCH_CHOICES)

    class Meta:
        unique_together = ['level', 'cb_code']
        indexes = [
            models.Index(fields=['match'], name='idx_therapy_omop_map_match'),
        ]

    def __str__(self):
        return f"{self.level}:{self.cb_code} -> {self.omop_concept_id or '(none)'} [{self.match}]"


class TherapyDisease(TimeStampMixin):
    therapy = models.ForeignKey(Therapy, models.CASCADE, blank=True, null=True)
    disease = models.ForeignKey(Disease, models.CASCADE, blank=True, null=True)

    class Meta:
        unique_together = ['therapy', 'disease']


class Trial(TimeStampMixin):
    code = models.CharField(max_length=255, blank=False, null=False, unique=True, db_index=True)
    is_validated = models.BooleanField(blank=False, null=False, default=False, db_index=True)
    validated_at = models.DateTimeField(db_index=True, blank=True, null=True)
    is_labeled = models.BooleanField(blank=False, null=False, default=False, db_index=True)
    labeled_at = models.DateTimeField(db_index=True, blank=True, null=True)

    # Study Info
    study_id = models.CharField(unique=False, max_length=100, db_index=True)
    register = models.TextField(default='')
    brief_title = models.TextField(default='')
    official_title = models.TextField(default='')
    schedule_of_assessment = models.TextField(blank=True, null=True)
    locations_name = models.JSONField(blank=True, default=list)
    intervention_treatments = models.JSONField(blank=True, default=list)
    intervention_treatments_text = models.TextField(default='')
    sponsor_name = models.TextField(default='')
    researchers = models.JSONField(blank=True, default=list)
    researchers_emails = models.JSONField(null=False, blank=True, default=list)
    contact_email = models.TextField(default='')
    link = models.TextField(default='')
    enrollment_count = models.PositiveIntegerField(blank=True, null=True)
    patient_burden_score = models.PositiveIntegerField(
        blank=True, null=True,
        validators=[MaxValueValidator(TRIAL_SCORE_MAX)],
        help_text=f'Patient burden on a 0–{TRIAL_SCORE_MAX} scale (higher = more burden).',
    )
    risk_score = models.PositiveIntegerField(
        blank=True, null=True,
        validators=[MaxValueValidator(TRIAL_SCORE_MAX)],
        help_text=f'Risk on a 0–{TRIAL_SCORE_MAX} scale (higher = more risk).',
    )
    benefit_score = models.PositiveIntegerField(
        blank=True, null=True,
        validators=[MaxValueValidator(TRIAL_SCORE_MAX)],
        help_text=f'Benefit on a 0–{TRIAL_SCORE_MAX} scale (higher = more benefit).',
    )

    # Study Dates
    submitted_date = models.DateField(blank=True, null=True, default=None)
    posted_date = models.DateField(blank=True, null=True, default=None)
    last_update_date = models.DateField(blank=True, null=True, default=None)
    first_enrolment_date = models.DateField(blank=True, null=True, default=None)

    # Study Design
    target_sample_size = models.IntegerField(blank=True, null=True)
    recruitment_status = models.TextField(default='')
    study_type = models.TextField(default='')
    study_design = models.TextField(blank=True, null=True)
    phases = models.JSONField(blank=True, default=list)
    phase_code_min = models.IntegerField(blank=True, null=True)
    trial_type = models.ForeignKey("TrialType", models.PROTECT, blank=True, null=True)
    purpose = models.ForeignKey("TrialPurpose", models.PROTECT, blank=True, null=True, related_name='trials')

    # PATIENT REQUIREMENTS
    brief_summary = models.TextField(blank=True, null=True)
    lay_summary = models.TextField(blank=True, null=True)
    participation_criteria = models.TextField(blank=True, null=True)

    languages_skills_required = models.JSONField(blank=True, null=False, default=list)

    age_low_limit = models.IntegerField(blank=True, null=True)
    age_high_limit = models.IntegerField(blank=True, null=True)
    gender = models.CharField(max_length=3, blank=True, null=True)
    ethnicity_required = models.JSONField(blank=True, null=False, default=list)
    # OMOP demographics cutover (#4447): concept_id mirrors of the legacy demographic
    # fields, filled by backfill_omop_demographics_columns. Read-only in EXACT;
    # matching reads these only once the demographics profile/config are flipped.
    # omop_ethnicity_required = race concept_ids as STRINGS in JSONB (mirrors the
    # omop_therapies_* convention); omop_gender_concept_id = single OMOP gender concept.
    omop_ethnicity_required = models.JSONField(blank=True, null=False, default=list)
    omop_gender_concept_id = models.BigIntegerField(blank=True, null=True, db_index=True)
    consent_capability_required = models.BooleanField(blank=True, null=True, db_index=True)
    no_tobacco_use_required = models.BooleanField(blank=True, null=True, db_index=True)
    no_substance_use_required = models.BooleanField(blank=True, null=True, db_index=True)
    negative_pregnancy_test_result_required = models.BooleanField(blank=True, db_index=True, null=True)
    no_pregnancy_or_lactation_required = models.BooleanField(blank=True, null=True, db_index=True)
    contraceptive_use_requirement = models.BooleanField(blank=True, null=True, db_index=True)
    no_geographic_exposure_risk_required = models.BooleanField(blank=True, null=True, db_index=True)
    no_mental_health_disorder_required = models.BooleanField(blank=True, null=True, db_index=True)
    no_concomitant_medication_required = models.BooleanField(blank=True, null=True, db_index=True)
    caregiver_availability_required = models.BooleanField(blank=True, null=True, db_index=True)

    concomitant_medications_excluded = models.JSONField(blank=True, null=True, default=list)
    concomitant_medications_washout_period_duration = models.IntegerField(blank=True, null=True)

    # Viral Infection Status
    no_hiv_required = models.BooleanField(blank=True, null=True, db_index=True)
    no_hepatitis_b_required = models.BooleanField(blank=True, null=True, db_index=True)
    no_hepatitis_c_required = models.BooleanField(blank=True, null=True, db_index=True)

    # Disease / Treatments
    disease = models.TextField(blank=True, null=True)
    condition_code_icd_10 = models.TextField(blank=True, null=True)
    condition_code_snomed_ct = models.TextField(blank=True, null=True)
    stages = models.JSONField(null=False, blank=True, default=list)
    has_stages = models.BooleanField(blank=True, null=True, db_index=True)
    prior_therapy_text = models.TextField(blank=True, null=True)
    no_prior_therapy_text = models.TextField(blank=True, null=True)
    not_refractory_required = models.BooleanField(blank=True, null=True, db_index=True)
    refractory_required = models.BooleanField(blank=True, null=True, db_index=True)
    prior_therapy_lines = models.CharField(max_length=50, blank=True, null=True, choices=PriorTherapyLines.choices)
    therapy_lines_count_min = models.IntegerField(blank=True, null=True)
    therapy_lines_count_max = models.IntegerField(blank=True, null=True)
    therapies_required = models.JSONField(blank=True, null=True, default=list)
    therapies_excluded = models.JSONField(blank=True, null=True, default=list)
    therapy_types_required = models.JSONField(blank=True, null=True, default=list)
    therapy_types_excluded = models.JSONField(blank=True, null=True, default=list)
    therapy_components_required = models.JSONField(blank=True, null=True, default=list)
    therapy_components_excluded = models.JSONField(blank=True, null=True, default=list)
    supportive_therapies_required = models.JSONField(blank=True, null=False, default=list)
    supportive_therapies_excluded = models.JSONField(blank=True, null=False, default=list)
    planned_therapies_required = models.JSONField(blank=True, null=False, default=list)
    planned_therapies_excluded = models.JSONField(blank=True, null=False, default=list)
    # OMOP cutover (CB epic #4447): concept_id-as-string mirrors of the therapy
    # columns, filled upstream by CB. Read-only in EXACT; matching reads these
    # only when the OMOP TherapyMatchProfile is active (Phase 3, behind a flag).
    omop_therapies_required = models.JSONField(blank=True, null=False, default=list)
    omop_therapies_excluded = models.JSONField(blank=True, null=False, default=list)
    # NOTE: no omop_therapy_types_* columns — drug-class "types" are a CB-hierarchy
    # construct, not OMOP-mapped (EXACT ADR 0001 decision A, #4502). Types keep
    # matching in CB-category-code space via therapy_types_*; a patient never
    # carries a class concept, so an omop_therapy_types_* column could never overlap.
    omop_therapy_components_required = models.JSONField(blank=True, null=False, default=list)
    omop_therapy_components_excluded = models.JSONField(blank=True, null=False, default=list)
    omop_supportive_therapies_required = models.JSONField(blank=True, null=False, default=list)
    omop_supportive_therapies_excluded = models.JSONField(blank=True, null=False, default=list)
    omop_planned_therapies_required = models.JSONField(blank=True, null=False, default=list)
    omop_planned_therapies_excluded = models.JSONField(blank=True, null=False, default=list)
    # Populated by CB during trial sync: OMOP concept_ids of the intervention arm
    # (what therapy the trial is giving/testing). Used by ?therapy_id= search.
    omop_intervention_concept_ids = models.JSONField(blank=True, null=False, default=list)
    relapse_count_min = models.IntegerField(blank=True, null=True)
    relapse_count_max = models.IntegerField(blank=True, null=True)
    remission_duration_min = models.IntegerField(blank=True, null=True)
    washout_period_duration = models.IntegerField(blank=True, null=True)
    no_other_active_malignancies_required = models.BooleanField(blank=True, null=True, db_index=True)
    peripheral_neuropathy_grade_min = models.IntegerField(blank=True, null=True)
    peripheral_neuropathy_grade_max = models.IntegerField(blank=True, null=True)
    no_plasma_cell_leukemia_required = models.BooleanField(blank=True, null=True)
    plasma_cell_leukemia_required = models.BooleanField(blank=True, null=True)
    measurable_disease_imwg_required = models.BooleanField(blank=True, null=True)

    # Disease Progression
    disease_progression_active_required = models.BooleanField(blank=True, null=True)
    disease_progression_smoldering_required = models.BooleanField(blank=True, null=True)

    # Performance Status
    karnofsky_performance_score_min = models.IntegerField(blank=True, null=True)
    karnofsky_performance_score_max = models.IntegerField(blank=True, null=True)
    ecog_performance_status_min = models.IntegerField(blank=True, null=True)
    ecog_performance_status_max = models.IntegerField(blank=True, null=True)

    # General Health
    heart_rate_min = models.IntegerField(blank=True, null=True)
    heart_rate_max = models.IntegerField(blank=True, null=True)
    systolic_blood_pressure_min = models.IntegerField(blank=True, null=True)
    systolic_blood_pressure_max = models.IntegerField(blank=True, null=True)
    diastolic_blood_pressure_min = models.IntegerField(blank=True, null=True)
    diastolic_blood_pressure_max = models.IntegerField(blank=True, null=True)
    weight_min = models.IntegerField(blank=True, null=True)
    weight_max = models.IntegerField(blank=True, null=True)
    bmi_min = models.IntegerField(blank=True, null=True)
    bmi_max = models.IntegerField(blank=True, null=True)

    # Hematologic and Biochemical
    hemoglobin_level_min = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    hemoglobin_level_max = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    platelet_count_min = models.IntegerField(blank=True, null=True)
    platelet_count_max = models.IntegerField(blank=True, null=True)
    red_blood_cell_count_min = models.IntegerField(blank=True, null=True)
    red_blood_cell_count_max = models.IntegerField(blank=True, null=True)
    white_blood_cell_count_min = models.IntegerField(blank=True, null=True)
    white_blood_cell_count_max = models.IntegerField(blank=True, null=True)
    absolute_neutrophile_count_min = models.IntegerField(blank=True, null=True)
    absolute_neutrophile_count_max = models.IntegerField(blank=True, null=True)
    serum_creatinine_level_abs_min = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    serum_creatinine_level_abs_max = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    serum_creatinine_level_uln_min = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    serum_creatinine_level_uln_max = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    creatinine_clearance_rate_min = models.IntegerField(blank=True, null=True)
    creatinine_clearance_rate_max = models.IntegerField(blank=True, null=True)
    estimated_glomerular_filtration_rate_min = models.IntegerField(blank=True, null=True)
    estimated_glomerular_filtration_rate_max = models.IntegerField(blank=True, null=True)
    renal_adequacy_required = models.BooleanField(blank=True, null=True)
    hepatic_adequacy_required = models.BooleanField(blank=True, null=True, help_text="Is a hepatic adequacy required?")
    haematological_adequacy_required = models.BooleanField(blank=True, null=True, help_text="Is a haematological adequacy required?")
    liver_enzyme_level_ast_abs_min = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    liver_enzyme_level_ast_abs_max = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    liver_enzyme_level_ast_uln_min = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    liver_enzyme_level_ast_uln_max = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    liver_enzyme_level_alt_abs_min = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    liver_enzyme_level_alt_abs_max = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    liver_enzyme_level_alt_uln_min = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    liver_enzyme_level_alt_uln_max = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    liver_enzyme_level_alp_abs_min = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    liver_enzyme_level_alp_abs_max = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    liver_enzyme_level_alp_uln_min = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    liver_enzyme_level_alp_uln_max = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    albumin_min = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    albumin_max = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    serum_bilirubin_total_level_abs_min = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    serum_bilirubin_total_level_abs_max = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    serum_bilirubin_total_level_uln_min = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    serum_bilirubin_total_level_uln_max = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    serum_bilirubin_direct_level_abs_min = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    serum_bilirubin_direct_level_abs_max = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    serum_bilirubin_direct_level_uln_min = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    serum_bilirubin_direct_level_uln_max = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)

    # Bone Health and Organ Function
    bone_imaging_result_required = models.BooleanField(blank=True, null=True, db_index=True)
    ejection_fraction_min = models.IntegerField(blank=True, null=True)
    ejection_fraction_max = models.IntegerField(blank=True, null=True)
    clonal_plasma_cells_min = models.IntegerField(blank=True, null=True)
    clonal_plasma_cells_max = models.IntegerField(blank=True, null=True)
    bone_lesions_min = models.IntegerField(blank=True, null=True)
    pulmonary_function_test_result_required = models.BooleanField(blank=True, null=True, db_index=True)

    # Lab Values
    serum_monoclonal_protein_level_min = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    serum_monoclonal_protein_level_max = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    urine_monoclonal_protein_level_min = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    urine_monoclonal_protein_level_max = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    serum_calcium_level_min = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    serum_calcium_level_max = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    lactate_dehydrogenase_level_min = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    lactate_dehydrogenase_level_max = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    ldh_units = models.DecimalField(decimal_places=2, max_digits=10, blank=True, null=True)
    toxicity_grade_max = models.IntegerField(blank=True, null=True)

    # Comorbidities
    pre_existing_conditions_excluded = models.JSONField(blank=True, null=True, default=list)
    no_active_infection_required = models.BooleanField(blank=True, null=True, db_index=True)
    no_concomitant_illness_required = models.BooleanField(blank=True, null=True, db_index=True)

    # Genetic Markers and Transplants
    cytogenic_markers_required = models.JSONField(blank=True, null=False, default=list)
    cytogenic_markers_all = models.JSONField(blank=True, null=False, default=list)
    cytogenic_markers_excluded = models.JSONField(blank=True, null=False, default=list)
    molecular_markers_required = models.JSONField(blank=True, null=False, default=list)
    molecular_markers_excluded = models.JSONField(blank=True, null=False, default=list)
    stem_cell_transplant_history = models.JSONField(blank=True, null=True, default=list)
    stem_cell_transplant_history_required = models.BooleanField(blank=True, null=True, db_index=True)
    day_since_stem_cell_transplant_required = models.IntegerField(blank=True, null=True)
    stem_cell_transplant_history_excluded = models.JSONField(blank=True, null=True, default=list)

    # Prognostic Indices
    gelf_criteria_status = models.TextField(blank=True, null=True)
    meets_gelf = models.BooleanField(blank=True, null=True, db_index=True)
    flipi_score_min = models.IntegerField(blank=True, null=True)
    flipi_score_max = models.IntegerField(blank=True, null=True)
    tumor_grade_min = models.IntegerField(blank=True, null=True)
    tumor_grade_max = models.IntegerField(blank=True, null=True)

    kappa_flc = models.IntegerField(blank=True, null=True)
    lambda_flc = models.IntegerField(blank=True, null=True)
    kappa_lambda_abnormal_required = models.BooleanField(blank=True, null=True)
    meets_crab = models.BooleanField(blank=True, null=True, db_index=True)
    meets_slim = models.BooleanField(blank=True, null=True, db_index=True)
    meets_lugano = models.BooleanField(blank=True, null=True, db_index=True)

    # BREAST CANCER
    bone_only_metastasis_required = models.BooleanField(blank=True, null=True, db_index=True)
    menopausal_status = models.TextField(blank=True, null=True)
    metastatic_required = models.BooleanField(blank=True, null=True, db_index=True)
    meets_meas_or_bone_required = models.BooleanField(blank=True, null=True, db_index=True)

    histologic_types_required = models.JSONField(blank=True, null=False, default=list)
    biopsy_grade_min = models.IntegerField(blank=True, null=True)
    biopsy_grade_max = models.IntegerField(blank=True, null=True)
    measurable_disease_by_recist_required = models.BooleanField(blank=True, null=True, db_index=True)
    estrogen_receptor_statuses_required = models.JSONField(blank=True, null=False, default=list)
    progesterone_receptor_statuses_required = models.JSONField(blank=True, null=False, default=list)
    her2_statuses_required = models.JSONField(blank=True, null=False, default=list)
    tnbc_status = models.BooleanField(blank=True, null=True, db_index=True)
    hrd_statuses_required = models.JSONField(blank=True, null=False, default=list)
    hr_statuses_required = models.JSONField(blank=True, null=False, default=list)

    tumor_stages_required = models.JSONField(blank=True, null=False, default=list)
    tumor_stages_excluded = models.JSONField(blank=True, null=False, default=list)
    nodes_stages_required = models.JSONField(blank=True, null=False, default=list)
    nodes_stages_excluded = models.JSONField(blank=True, null=False, default=list)
    distant_metastasis_stages_required = models.JSONField(blank=True, null=False, default=list)
    distant_metastasis_stages_excluded = models.JSONField(blank=True, null=False, default=list)
    staging_modalities_required = models.JSONField(blank=True, null=False, default=list)

    # Genetic Mutations
    mutation_genes_required = models.JSONField(blank=True, null=False, default=list)
    mutation_variants_required = models.JSONField(blank=True, null=False, default=list)
    mutation_origins_required = models.JSONField(blank=True, null=False, default=list)
    mutation_interpretations_required = models.JSONField(blank=True, null=False, default=list)

    # PD-L1 Expression
    pd_l1_tumor_cels_min = models.IntegerField(blank=True, null=True)
    pd_l1_tumor_cels_max = models.IntegerField(blank=True, null=True)
    pd_l1_assay = models.TextField(blank=True, null=True)
    pd_l1_ic_percentage_min = models.IntegerField(blank=True, null=True)
    pd_l1_ic_percentage_max = models.IntegerField(blank=True, null=True)
    pd_l1_combined_positive_score_min = models.IntegerField(blank=True, null=True)
    pd_l1_combined_positive_score_max = models.IntegerField(blank=True, null=True)

    ki67_proliferation_index_min = models.IntegerField(blank=True, null=True)
    ki67_proliferation_index_max = models.IntegerField(blank=True, null=True)

    # CLL
    binet_stages_required = models.JSONField(blank=True, null=False, default=list)
    protein_expressions_required = models.JSONField(blank=True, null=False, default=list)
    protein_expressions_excluded = models.JSONField(blank=True, null=False, default=list)
    richter_transformations_required = models.JSONField(blank=True, null=False, default=list)
    richter_transformations_excluded = models.JSONField(blank=True, null=False, default=list)
    tumor_burdens_required = models.JSONField(blank=True, null=False, default=list)
    lymphocyte_doubling_time_min = models.IntegerField(blank=True, null=True)
    lymphocyte_doubling_time_max = models.IntegerField(blank=True, null=True)
    tp53_disruption_required = models.BooleanField(blank=True, null=True, db_index=True)
    tp53_disruption_excluded = models.BooleanField(blank=True, null=True, db_index=True)
    measurable_disease_iwcll_required = models.BooleanField(blank=True, null=True, db_index=True)
    hepatomegaly_required = models.BooleanField(blank=True, null=True, db_index=True)
    autoimmune_cytopenias_refractory_to_steroids_required = models.BooleanField(blank=True, null=True, db_index=True)
    lymphadenopathy_required = models.BooleanField(blank=True, null=True, db_index=True)
    largest_lymph_node_size_min = models.FloatField(blank=True, null=True)
    splenomegaly_required = models.BooleanField(blank=True, null=True, db_index=True)
    spleen_size_min = models.FloatField(blank=True, null=True)
    disease_activities_required = models.JSONField(blank=True, null=False, default=list)
    btk_inhibitor_refractory_required = models.BooleanField(blank=True, null=True, db_index=True)
    btk_inhibitor_refractory_excluded = models.BooleanField(blank=True, null=True, db_index=True)
    bcl2_inhibitor_refractory_required = models.BooleanField(blank=True, null=True, db_index=True)
    bcl2_inhibitor_refractory_excluded = models.BooleanField(blank=True, null=True, db_index=True)
    absolute_lymphocyte_count_min = models.FloatField(blank=True, null=True)
    absolute_lymphocyte_count_max = models.FloatField(blank=True, null=True)
    qtcf_value_max = models.FloatField(blank=True, null=True)
    serum_beta2_microglobulin_level_min = models.FloatField(blank=True, null=True)
    serum_beta2_microglobulin_level_max = models.FloatField(blank=True, null=True)
    clonal_bone_marrow_b_lymphocytes_min = models.FloatField(blank=True, null=True)
    clonal_bone_marrow_b_lymphocytes_max = models.FloatField(blank=True, null=True)
    clonal_b_lymphocyte_count_min = models.IntegerField(blank=True, null=True)
    clonal_b_lymphocyte_count_max = models.IntegerField(blank=True, null=True)
    bone_marrow_involvement_required = models.BooleanField(blank=True, null=True, db_index=True)

    # MCL
    largest_lesion_size_min = models.FloatField(blank=True, null=True)
    largest_lesion_size_max = models.FloatField(blank=True, null=True)
    p53_ihc_min = models.IntegerField(blank=True, null=True)
    p53_ihc_max = models.IntegerField(blank=True, null=True)
    morphologic_variants_required = models.JSONField(blank=True, null=False, default=list)
    morphologic_variants_excluded = models.JSONField(blank=True, null=False, default=list)
    disease_behaviors_required = models.JSONField(blank=True, null=False, default=list)
    disease_subtypes_required = models.JSONField(blank=True, null=False, default=list)
    extranodal_sites_required = models.JSONField(blank=True, null=False, default=list)
    bulky_disease_criteria_required = models.JSONField(blank=True, null=False, default=list)
    mipi_risks_required = models.JSONField(blank=True, null=False, default=list)
    mipi_c_risks_required = models.JSONField(blank=True, null=False, default=list)
    high_risk_mcl_criteria_required = models.JSONField(blank=True, null=False, default=list)
    high_risk_mcl_criteria_excluded = models.JSONField(blank=True, null=False, default=list)
    high_risk_mcl_criteria_sufficient_any = models.JSONField(blank=True, null=False, default=list)
    high_risk_mcl_criteria_min_count = models.PositiveSmallIntegerField(blank=True, null=True, default=None)

    locations = models.ManyToManyField(
        'Location',
        blank=True,
        through='LocationTrial',
        through_fields=('trial', 'location'),
        related_name='location_trials'
    )

    class Meta:
        indexes = [
            GinIndex(
                SearchVector("brief_title", "official_title", config="english"),
                name="titles_search_vector_idx",
            ),
            Index(Lower('disease'), name='idx_trials_disease_lower'),
            Index(Lower('register'), name='idx_trials_register_lower'),
            Index(Lower('sponsor_name'), name='idx_trials_sponsor_lower'),
            Index(Lower('recruitment_status'), name='idx_trials_recr_stat_lower'),
            Index(Lower('study_type'), name='idx_trials_study_type_lower'),
            Index(Lower('gender'), name='idx_trials_gender_lower'),
            Index(Lower('pd_l1_assay'), name='idx_trials_pd_l1_assay_lower'),
            GinIndex(fields=['phases'], name='idx_phases_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['languages_skills_required'], name='idx_lang_skills_required_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['ethnicity_required'], name='idx_ethnicity_required_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['omop_ethnicity_required'], name='idx_omop_ethnicity_req_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['concomitant_medications_excluded'], name='idx_conc_med_excluded_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['stages'], name='idx_stages_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['pre_existing_conditions_excluded'], name='idx_pre_ex_cond_excluded_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['stem_cell_transplant_history_excluded'], name='idx_scth_excluded_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['histologic_types_required'], name='idx_hist_types_required_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['estrogen_receptor_statuses_required'], name='idx_est_rec_stat_required_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['progesterone_receptor_statuses_required'], name='idx_prog_rec_stat_required_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['her2_statuses_required'], name='idx_her2_stat_required_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['hrd_statuses_required'], name='idx_hrd_stat_required_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['hr_statuses_required'], name='idx_hr_stat_required_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['staging_modalities_required'], name='idx_stag_mod_required_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['therapies_required', 'therapies_excluded'], name='idx_therapies_pair_gin', opclasses=['jsonb_ops', 'jsonb_ops']),
            GinIndex(fields=['therapy_types_required', 'therapy_types_excluded'], name='idx_therapy_types_pair_gin', opclasses=['jsonb_ops', 'jsonb_ops']),
            GinIndex(fields=['therapy_components_required', 'therapy_components_excluded'], name='idx_therapy_comps_pair_gin', opclasses=['jsonb_ops', 'jsonb_ops']),
            GinIndex(fields=['supportive_therapies_required', 'supportive_therapies_excluded'], name='idx_sup_therapies_pair_gin', opclasses=['jsonb_ops', 'jsonb_ops']),
            GinIndex(fields=['planned_therapies_required', 'planned_therapies_excluded'], name='idx_planned_therapies_pair_gin', opclasses=['jsonb_ops', 'jsonb_ops']),
            GinIndex(fields=['omop_therapies_required', 'omop_therapies_excluded'], name='idx_omop_therapies_pair_gin', opclasses=['jsonb_ops', 'jsonb_ops']),
            GinIndex(fields=['omop_therapy_components_required', 'omop_therapy_components_excluded'], name='idx_omop_therapy_comps_gin', opclasses=['jsonb_ops', 'jsonb_ops']),
            GinIndex(fields=['omop_supportive_therapies_required', 'omop_supportive_therapies_excluded'], name='idx_omop_sup_therapies_gin', opclasses=['jsonb_ops', 'jsonb_ops']),
            GinIndex(fields=['omop_planned_therapies_required', 'omop_planned_therapies_excluded'], name='idx_omop_planned_therapies_gin', opclasses=['jsonb_ops', 'jsonb_ops']),
            GinIndex(fields=['omop_intervention_concept_ids'], name='idx_omop_intervention_cids_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['cytogenic_markers_required', 'cytogenic_markers_excluded'], name='idx_cytogenic_markers_pair_gin', opclasses=['jsonb_ops', 'jsonb_ops']),
            GinIndex(fields=['molecular_markers_required', 'molecular_markers_excluded'], name='idx_molecular_markers_pair_gin', opclasses=['jsonb_ops', 'jsonb_ops']),
            GinIndex(fields=['tumor_stages_required', 'tumor_stages_excluded'], name='idx_tumor_stages_pair_gin', opclasses=['jsonb_ops', 'jsonb_ops']),
            GinIndex(fields=['nodes_stages_required', 'nodes_stages_excluded'], name='idx_nodes_stages_pair_gin', opclasses=['jsonb_ops', 'jsonb_ops']),
            GinIndex(fields=['distant_metastasis_stages_required', 'distant_metastasis_stages_excluded'], name='idx_d_m_stages_pair_gin', opclasses=['jsonb_ops', 'jsonb_ops']),
            GinIndex(fields=['morphologic_variants_required', 'morphologic_variants_excluded'], name='idx_morph_variants_pair_gin', opclasses=['jsonb_ops', 'jsonb_ops']),
            GinIndex(fields=['disease_behaviors_required'], name='idx_disease_behaviors_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['disease_subtypes_required'], name='idx_disease_subtypes_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['extranodal_sites_required'], name='idx_extranodal_sites_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['bulky_disease_criteria_required'], name='idx_bulky_disease_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['mipi_risks_required'], name='idx_mipi_risks_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['mipi_c_risks_required'], name='idx_mipi_c_risks_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['high_risk_mcl_criteria_required'], name='idx_hr_mcl_required_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['high_risk_mcl_criteria_excluded'], name='idx_hr_mcl_excluded_gin', opclasses=['jsonb_ops']),
            GinIndex(fields=['high_risk_mcl_criteria_sufficient_any'], name='idx_hr_mcl_sufficient_gin', opclasses=['jsonb_ops']),
            # B-tree indexes on hot-path sort/filter columns (#27). Match the
            # actual ORDER BY direction at trials_views.py:156,158 — a plain
            # ASC NULLS LAST B-tree (Django default) cannot serve a
            # `.desc(nulls_last=True)` ORDER BY, so the planner would fall
            # back to seq scan + sort. trial_type is a ForeignKey so Django
            # auto-indexes it.
            Index(
                F('enrollment_count').desc(nulls_last=True),
                name='idx_trials_enroll_count',
            ),
            Index(
                F('last_update_date').desc(nulls_last=True),
                name='idx_trials_last_update',
            ),
        ]
        constraints = [
            # Component scores must stay on the documented 0–TRIAL_SCORE_MAX
            # scale (see trials.constants). NULL is allowed (scores are
            # optional). Enforced at the DB so bulk_create / update_or_create /
            # raw save() — which bypass field validators — can't drift the
            # scale and produce an out-of-range goodness score.
            models.CheckConstraint(
                condition=Q(benefit_score__isnull=True)
                | Q(benefit_score__gte=0, benefit_score__lte=TRIAL_SCORE_MAX),
                name='trials_benefit_score_0_20',
            ),
            models.CheckConstraint(
                condition=Q(patient_burden_score__isnull=True)
                | Q(patient_burden_score__gte=0, patient_burden_score__lte=TRIAL_SCORE_MAX),
                name='trials_patient_burden_score_0_20',
            ),
            models.CheckConstraint(
                condition=Q(risk_score__isnull=True)
                | Q(risk_score__gte=0, risk_score__lte=TRIAL_SCORE_MAX),
                name='trials_risk_score_0_20',
            ),
        ]

    def attrs_to_fill_in(self, counts):
        return UserToTrialAttrsMapper().potential_attrs_for_trial(self, counts)

    def get_match_score(self, patient_info):
        return UserToTrialAttrMatcher(trial=self, patient_info=patient_info).trial_match_score()

    def details_and_group_names(self, patient_info, template, attrs_to_fill_in):
        return TrialTemplates(trial=self, patient_info=patient_info).details_and_group_names(template, attrs_to_fill_in)

    def matching_type(self, patient_info):
        return UserToTrialAttrMatcher(trial=self, patient_info=patient_info).trial_match_status()

    @property
    def location_name(self):
        return [self.locations_name[0]] if len(self.locations_name) > 0 else []

    def get_distance_obj(self, pi, recruitment_status=None):
        """Closest distance from patient to any LocationTrial of this Trial.

        Materializes locationtrial_set as a Python list and filters in
        Python so the TrialsViewSet prefetch (`Prefetch('locationtrial_set',
        select_related('location'))`) is honored. The previous version
        cloned the queryset via `.filter(recruitment_status__in=...)`
        which issued a fresh DB query per Trial — the production N+1
        flagged in #26.
        """
        from trials.querysets.trial import get_recruitment_status_filter_values

        if hasattr(self, 'distance') and self.distance:
            return self.distance

        if not hasattr(self, 'locationtrial_set'):
            return None
        # `.all()` honors a `Prefetch` cache when one was declared;
        # `list(...)` then materializes once, after which subsequent
        # filtering happens in Python without further DB hits.
        location_trials = list(self.locationtrial_set.all())
        if not location_trials:
            return None

        status_values = get_recruitment_status_filter_values(recruitment_status)
        if status_values is not None:
            location_trials = [
                lt for lt in location_trials
                if lt.recruitment_status in status_values
            ]

        for lt in location_trials:
            if hasattr(lt, 'distance'):
                return lt.distance

        if pi is None or pi.geo_point is None:
            return None

        # Hoist the patient Point out of the loop (was rebuilt per
        # LocationTrial in the prior version).
        pi_point = Point(pi.geo_point.y, pi.geo_point.x, srid=4326)
        min_distance = None
        for lt in location_trials:
            if lt.location and lt.location.geo_point:
                p1 = Point(lt.location.geo_point.y, lt.location.geo_point.x, srid=4326)
                dist = geopy_distance(p1, pi_point)
                if min_distance is None or dist < min_distance:
                    min_distance = dist
        return min_distance

    def get_distance_penalty(self, pi, recruitment_status=None):
        if not pi or not pi.geo_point:
            return 0
        dist_obj = self.get_distance_obj(pi, recruitment_status=recruitment_status)
        if dist_obj is not None:
            return min(20, int(dist_obj.mi / 10 + 0.5))
        return 20

    def get_distance(self, pi, units, recruitment_status=None):
        dist_obj = self.get_distance_obj(pi, recruitment_status=recruitment_status)
        if dist_obj is not None:
            dist = dist_obj.mi if units == 'miles' else dist_obj.km
            return int(dist + 0.5)

    def get_goodness_score(self, patient_info, benefit_weight=25, patient_burden_weight=25,
                           risk_weight=25, distance_penalty_weight=25):
        from trials.services.utils import normalize_goodness_weights
        benefit_weight, patient_burden_weight, risk_weight, distance_penalty_weight = (
            normalize_goodness_weights(
                benefit_weight, patient_burden_weight, risk_weight, distance_penalty_weight
            )
        )
        weights_sum = benefit_weight + patient_burden_weight + risk_weight + distance_penalty_weight
        distance_penalty = self.get_distance_penalty(patient_info)
        # Component scores and the distance penalty are all on a 0–20 scale,
        # so a single normalizer maps every term to [0, 1].
        max_val = float(TRIAL_SCORE_MAX)

        # Component scores live on a 0–20 scale. Clamp before normalizing so
        # out-of-range inputs (e.g. mis-scaled data) can't drive a component
        # term past its weight and push the composite outside [0, 100].
        def _clamp(value, default):
            if value is None:
                return default
            return min(max_val, max(0.0, float(value)))

        benefit = _clamp(self.benefit_score, 0.0)
        burden = _clamp(self.patient_burden_score, max_val)
        risk = _clamp(self.risk_score, max_val)

        raw = (
            float(benefit_weight) * benefit / max_val +
            float(patient_burden_weight) * (1 - burden / max_val) +
            float(risk_weight) * (1 - risk / max_val) +
            float(distance_penalty_weight) * (1 - distance_penalty / max_val)
        ) * 100 / float(weights_sum)
        return max(0, min(100, int(raw + 0.5)))

    def sorted_locations_by_distance(self, user_geo_point, recruitment_status=None):
        """Return LocationTrial rows ordered by distance from user_geo_point.

        Honors a `locationtrial_set` prefetch when present (TrialsViewSet
        prefetches with `select_related('location')` for the list endpoint
        — see #26), filtering and sorting in Python so the prefetched
        cache isn't bypassed by a fresh `.select_related(...)` call.
        Falls back to the QuerySet path for callers that didn't prefetch,
        keeping the original DB-annotated `distance` ordering for those.

        When user_geo_point is None: returns all matching LocationTrial
        rows. When set: returns a single-element list with the closest one,
        matching the prior contract.
        """
        from trials.querysets.trial import get_recruitment_status_filter_values
        status_values = get_recruitment_status_filter_values(recruitment_status)

        cached = (
            self._prefetched_objects_cache.get('locationtrial_set')
            if hasattr(self, '_prefetched_objects_cache') else None
        )
        if cached is not None:
            location_trials = list(cached)
            if status_values is not None:
                location_trials = [
                    lt for lt in location_trials
                    if lt.recruitment_status in status_values
                ]
            if not user_geo_point:
                return location_trials

            # Hoist the patient Point so it's built once, not per LT.
            user_point = Point(user_geo_point.y, user_geo_point.x, srid=4326)

            def _distance_key(lt):
                # Sort LocationTrials with missing geo last (matches the
                # DB-side null_distance_flag ordering in the fallback).
                # `lt.pk` is the stable tie-breaker so the prefetched and
                # fallback paths can't drift apart when all-null inputs
                # tie at (1, 0.0).
                if not lt.location or not lt.location.geo_point:
                    return (1, 0.0, lt.pk)
                lt_point = Point(lt.location.geo_point.y, lt.location.geo_point.x, srid=4326)
                return (0, geopy_distance(lt_point, user_point).km, lt.pk)
            location_trials.sort(key=_distance_key)
            return [location_trials[0]] if location_trials else location_trials

        # Fallback: no prefetch cached — issue a DB query like before.
        location_trials = self.locationtrial_set.select_related('location')
        if status_values is not None:
            location_trials = location_trials.filter(recruitment_status__in=status_values)

        if not user_geo_point:
            return location_trials.all()

        locations = location_trials.annotate(
            distance=Distance('location__geo_point', user_geo_point),
            null_distance_flag=Case(
                When(location__geo_point__isnull=True, then=Value(1)),
                When(distance__isnull=True, then=Value(1)),
                default=Value(0),
                output_field=IntegerField()
            )
        ).order_by('null_distance_flag', 'distance')

        return [locations[0]] if len(locations) > 0 else locations

    def __str__(self):
        return self.study_id

    objects = TrialQuerySet.as_manager()


# ---------------------------------------------------------------------------
# Unit choices for PatientInfo fields
# ---------------------------------------------------------------------------

class GenderChoices(models.TextChoices):
    MALE = 'M', 'Male'
    FEMALE = 'F', 'Female'
    UNKNOWN = 'UN', 'Unknown'
    EMPTY = '', 'Empty'


class WeightUnits(models.TextChoices):
    KG = 'kg', 'Kilograms'
    LB = 'lb', 'Pounds'


class HeightUnits(models.TextChoices):
    CM = 'cm', 'Centimeters'
    IN = 'in', 'Inches'


class HemoglobinUnits(models.TextChoices):
    G_DL = 'G/DL', 'g/deciliter'
    G_L = 'G/L', 'g/Liter'


class AlbuminUnits(models.TextChoices):
    G_DL = 'G/DL', 'g/deciliter'
    G_L = 'G/L', 'g/Liter'


class PlateletCountUnits(models.TextChoices):
    CELLS_UL = 'CELLS/UL', 'cells/microliter'
    CELLS_L = 'CELLS/L', 'cells/Liter'


class SerumCreatinineUnits(models.TextChoices):
    MG_DL = 'MG/DL', 'mg/dL'
    MICROMOLES_L = 'MICROMOLES/L', 'micromoles/L'


class SerumBilirubinUnits(models.TextChoices):
    MG_DL = 'MG/DL', 'mg/dL'
    MICROMOLES_L = 'MICROMOLES/L', 'micromoles/L'


class SerumCalciumUnits(models.TextChoices):
    MG_DL = 'MG/DL', 'mg/dL'
    MICROMOLES_L = 'MICROMOLES/L', 'micromoles/L'


class PreExistingConditionCategory(models.Model):
    code = models.CharField(max_length=50, null=False, blank=False, unique=True)
    title = models.CharField(max_length=255, null=False, blank=False, unique=True)

    def __str__(self):
        return self.title




class TrialPreExistingCondition(models.Model):
    trial = models.ForeignKey(Trial, on_delete=models.CASCADE, related_name='pre_existing_conditions', null=False)
    condition = models.TextField(null=False, blank=False)
    category = models.ForeignKey(PreExistingConditionCategory, on_delete=models.CASCADE, null=False)

    def __str__(self):
        return self.condition

    class Meta:
        unique_together = ['trial', 'condition']




# ---------------------------------------------------------------------------
# Options / enum models (25 models — needed by ValueOptions and matching)
# ---------------------------------------------------------------------------

class OptionsListMixin(TimeStampMixin):
    code = models.TextField(blank=False, null=False, db_index=True, unique=True)
    title = models.TextField(blank=False, null=False, db_index=True, unique=True)

    class Meta:
        abstract = True


class OptionsListIntCodeMixin(TimeStampMixin):
    code = models.IntegerField(blank=True, null=False, db_index=True, unique=True)
    title = models.TextField(blank=False, null=False, db_index=True, unique=True)

    class Meta:
        abstract = True


class Ethnicity(OptionsListMixin):
    # OMOP migration (#4447): 'ethnicity' holds race categories (no separate race
    # field), so this maps to OMOP RACE concepts. concept_id pinned to the PROMOP
    # release; null when unmapped (e.g. 'other'). Source for the trial
    # omop_ethnicity_* cols. Ported from CancerBot; populated by
    # load_ethnicity_omop_concept_ids.
    omop_concept_id = models.BigIntegerField(blank=True, null=True, db_index=True, help_text="OMOP race concept_id (PROMOP) for this ethnicity value; null when unmapped.")


class StemCellTransplant(OptionsListMixin):
    pass


class HistologicType(OptionsListMixin):
    sort_key = models.IntegerField(blank=True, null=True)


class EstrogenReceptorStatus(OptionsListMixin):
    pass


class ProgesteroneReceptorStatus(OptionsListMixin):
    pass


class Her2Status(OptionsListMixin):
    pass


class HrStatus(OptionsListMixin):
    pass


class HrdStatus(OptionsListMixin):
    pass


class TrialType(OptionsListMixin):
    diseases = models.ManyToManyField(
        'Disease',
        blank=True,
        through='TrialTypeDiseaseConnection',
        through_fields=('trial_type', 'disease'),
        related_name='disease_trial_types'
    )

    def __str__(self):
        return self.title


class TrialPurpose(OptionsListMixin):
    """High-level purpose of a trial (treatment / prevention / diagnostic /
    screening / supportive_care / health_services_research / basic_science /
    device_feasibility / other). Codes mirror the top-level keys of
    `trials.trial_taxonomy.TRIAL_TAXONOMY`.
    """
    def __str__(self):
        return self.title


class MutationOrigin(OptionsListMixin):
    pass


class MutationInterpretation(OptionsListMixin):
    pass


class MutationGene(OptionsListMixin):
    origins = models.ManyToManyField(
        'MutationOrigin',
        blank=True,
        through='MutationGeneOriginConnection',
        through_fields=('gene', 'origin'),
        related_name='origin_genes'
    )


class MutationGeneOriginConnection(TimeStampMixin):
    gene = models.ForeignKey(MutationGene, models.CASCADE, blank=True, null=True)
    origin = models.ForeignKey(MutationOrigin, models.CASCADE, blank=True, null=True)

    class Meta:
        unique_together = ['gene', 'origin']


class MutationCode(OptionsListMixin):
    gene = models.ForeignKey(MutationGene, models.CASCADE, blank=True, null=True)


class TumorStage(OptionsListMixin):
    pass


class NodesStage(OptionsListMixin):
    pass


class DistantMetastasisStage(OptionsListMixin):
    pass


class StagingModality(OptionsListMixin):
    pass


class ToxicityGrade(OptionsListIntCodeMixin):
    pass


class PlannedTherapy(OptionsListMixin):
    diseases = models.ManyToManyField(
        'Disease',
        blank=True,
        through='PlannedTherapyDiseaseConnection',
        through_fields=('planned_therapy', 'disease'),
        related_name='disease_planned_therapies'
    )


class PlannedTherapyDiseaseConnection(TimeStampMixin):
    planned_therapy = models.ForeignKey(PlannedTherapy, models.CASCADE, blank=True, null=True)
    disease = models.ForeignKey(Disease, models.CASCADE, blank=True, null=True)

    class Meta:
        unique_together = ['planned_therapy', 'disease']


class TrialTypeDiseaseConnection(TimeStampMixin):
    trial_type = models.ForeignKey(TrialType, models.CASCADE, blank=True, null=True)
    disease = models.ForeignKey(Disease, models.CASCADE, blank=True, null=True)

    class Meta:
        unique_together = ['trial_type', 'disease']


class Language(OptionsListMixin):
    pass


class LanguageSkillLevel(OptionsListMixin):
    pass


class BinetStage(OptionsListMixin):
    pass


class ProteinExpression(OptionsListMixin):
    pass


class MorphologicVariant(OptionsListMixin):
    pass


class HighRiskMclCriteria(OptionsListMixin):
    pass


class RichterTransformation(OptionsListMixin):
    pass


class TumorBurden(OptionsListMixin):
    pass


class PreferredCountry(OptionsListMixin):
    sort_key = models.IntegerField(blank=True, null=True)


# ---------------------------------------------------------------------------
# Trial universe (lightweight trial categorisation)
# ---------------------------------------------------------------------------

class TrialUniverse(TimeStampMixin):
    title = models.CharField(max_length=255, null=False, blank=False, unique=True, db_index=True)
    code = models.CharField(max_length=255, null=False, blank=False, unique=True, db_index=True)
    description = models.TextField(null=False, blank=True)

    @property
    def short_description(self):
        max_length = 100
        if self.description and len(self.description) > max_length:
            return self.description[:max_length - 3] + '...'
        return self.description


class TrialUniverseEntry(TimeStampMixin):
    universe = models.ForeignKey(TrialUniverse, on_delete=models.CASCADE, null=False, blank=False,
                                 related_name='entries', db_index=True)
    trial = models.ForeignKey(Trial, on_delete=models.CASCADE, null=False, blank=False)

    class Meta:
        unique_together = ('universe', 'trial')
