"""Standalone model mirror; CB owns all physical external catalog changes.

Retired marker and ETL models leave runtime/state only: preserve every physical
legacy table/row. The split-DB router must keep these operations off CB.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trials', '0027_cb_marker_catalogs'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.RemoveField(model_name='marker', name='categories'),
                migrations.DeleteModel(name='MarkerCategoryConnection'),
                migrations.DeleteModel(name='Marker'),
                migrations.DeleteModel(name='MarkerCategory'),
                migrations.DeleteModel(name='RawDataItem'),
            ],
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_binet_stages_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_bulky_disease_criteria_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_concomitant_medications_excluded',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_cytogenic_markers_excluded',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_cytogenic_markers_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_disease_activities_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_disease_behaviors_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_disease_subtypes_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_distant_metastasis_stages_excluded',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_distant_metastasis_stages_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_estrogen_receptor_statuses_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_extranodal_sites_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_her2_statuses_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_high_risk_mcl_criteria_excluded',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_high_risk_mcl_criteria_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_high_risk_mcl_criteria_sufficient_any',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_histologic_types_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_hr_statuses_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_hrd_statuses_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_mipi_c_risks_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_mipi_risks_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_molecular_markers_excluded',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_molecular_markers_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_morphologic_variants_excluded',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_morphologic_variants_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_mutation_genes_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_mutation_interpretations_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_mutation_origins_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_mutation_variants_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_nodes_stages_excluded',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_nodes_stages_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_pre_existing_conditions_excluded',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_progesterone_receptor_statuses_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_protein_expressions_excluded',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_protein_expressions_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_richter_transformations_excluded',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_richter_transformations_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_stages',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_staging_modalities_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_stem_cell_transplant_history_excluded',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_tumor_burdens_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_tumor_stages_excluded',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='omop_tumor_stages_required',
            field=models.JSONField(blank=True, db_default=[], default=list),
        ),
        migrations.AlterField(
            model_name='trial',
            name='red_blood_cell_count_max',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AlterField(
            model_name='trial',
            name='red_blood_cell_count_min',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
    ]
