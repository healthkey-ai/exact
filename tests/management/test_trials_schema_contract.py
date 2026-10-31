"""Read-model coverage for the frozen CB 0449 storage contract (#659)."""
import pytest
from decimal import Decimal
from django.db import models
from rest_framework.renderers import JSONRenderer

from exact_matching.patient_info.configs import TRIAL_ATTRS_JSON_AS_A_LIST
from trials.models import Trial

pytestmark = pytest.mark.django_db


# Frozen from CB migration 0449. These are storage slots, not reader semantics.
CB_0449_SOURCES = (
    'pre_existing_conditions_excluded', 'stem_cell_transplant_history_excluded',
    'cytogenic_markers_required', 'cytogenic_markers_excluded',
    'molecular_markers_required', 'molecular_markers_excluded',
    'histologic_types_required', 'estrogen_receptor_statuses_required',
    'progesterone_receptor_statuses_required', 'her2_statuses_required',
    'hrd_statuses_required', 'hr_statuses_required',
    'tumor_stages_required', 'tumor_stages_excluded',
    'nodes_stages_required', 'nodes_stages_excluded',
    'distant_metastasis_stages_required', 'distant_metastasis_stages_excluded',
    'staging_modalities_required', 'mutation_genes_required',
    'mutation_variants_required', 'mutation_origins_required', 'mutation_interpretations_required',
    'concomitant_medications_excluded', 'stages', 'binet_stages_required',
    'protein_expressions_required', 'protein_expressions_excluded',
    'richter_transformations_required', 'richter_transformations_excluded',
    'tumor_burdens_required', 'disease_activities_required',
    'morphologic_variants_required', 'morphologic_variants_excluded',
    'disease_behaviors_required', 'disease_subtypes_required',
    'mipi_risks_required', 'mipi_c_risks_required', 'extranodal_sites_required',
    'bulky_disease_criteria_required', 'high_risk_mcl_criteria_required',
    'high_risk_mcl_criteria_excluded', 'high_risk_mcl_criteria_sufficient_any',
)


@pytest.mark.parametrize('source', CB_0449_SOURCES)
def test_all_43_cb0449_fields_have_exact_storage_defaults(source):
    field = Trial._meta.get_field('omop_' + source)
    assert isinstance(field, models.JSONField)
    assert field.null is False
    assert field.default is list
    assert field.db_default == []
    assert field.get_default() == []
    assert field.get_default() is not field.get_default()


def test_all_55_configured_lists_have_model_counterparts_without_activating_readers():
    sources = set(TRIAL_ATTRS_JSON_AS_A_LIST) - {
        'omop_languages_skills_required', 'myeloma_related_disorders_excluded',
    }
    assert len(sources) == 55
    assert len(set(CB_0449_SOURCES)) == 43
    for source in sources:
        assert isinstance(Trial._meta.get_field('omop_' + source), models.JSONField)
    # The shared matcher registry remains legacy; storage alone activates nothing.
    assert not {'omop_' + source for source in CB_0449_SOURCES} & set(TRIAL_ATTRS_JSON_AS_A_LIST)


def test_rbc_model_keeps_fractional_thresholds_and_json_values():
    trial = Trial.objects.create(disease='multiple myeloma',
                                 red_blood_cell_count_min=Decimal('4.70'),
                                 red_blood_cell_count_max=Decimal('6.10'))
    loaded = Trial.objects.get(pk=trial.pk)
    assert loaded.red_blood_cell_count_min == Decimal('4.70')
    assert loaded.red_blood_cell_count_max == Decimal('6.10')
    assert not Trial.objects.filter(pk=trial.pk, red_blood_cell_count_min__lte=Decimal('4.69')).exists()
    assert Trial.objects.filter(pk=trial.pk, red_blood_cell_count_min__lte=Decimal('4.70')).exists()
    assert JSONRenderer().render({'min': loaded.red_blood_cell_count_min,
                                  'max': loaded.red_blood_cell_count_max}) == b'{"min":4.7,"max":6.1}'
