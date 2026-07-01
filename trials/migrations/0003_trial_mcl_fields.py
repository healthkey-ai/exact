from django.contrib.postgres.indexes import GinIndex
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trials', '0002_morphologicvariant'),
    ]

    operations = [
        migrations.AddField(
            model_name='trial',
            name='lesion_size_mcl_min',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='trial',
            name='lesion_size_mcl_max',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='trial',
            name='morphologic_variants_required',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='morphologic_variants_excluded',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='disease_behaviors_required',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='disease_subtypes_required',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='extranodal_sites_required',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='bulky_disease_criteria_required',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='mipi_risks_required',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='trial',
            name='mipi_c_risks_required',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddIndex(
            model_name='trial',
            index=GinIndex(
                fields=['morphologic_variants_required', 'morphologic_variants_excluded'],
                name='idx_morph_variants_pair_gin',
                opclasses=['jsonb_ops', 'jsonb_ops'],
            ),
        ),
        migrations.AddIndex(
            model_name='trial',
            index=GinIndex(
                fields=['disease_behaviors_required'],
                name='idx_disease_behaviors_gin',
                opclasses=['jsonb_ops'],
            ),
        ),
        migrations.AddIndex(
            model_name='trial',
            index=GinIndex(
                fields=['disease_subtypes_required'],
                name='idx_disease_subtypes_gin',
                opclasses=['jsonb_ops'],
            ),
        ),
        migrations.AddIndex(
            model_name='trial',
            index=GinIndex(
                fields=['extranodal_sites_required'],
                name='idx_extranodal_sites_gin',
                opclasses=['jsonb_ops'],
            ),
        ),
        migrations.AddIndex(
            model_name='trial',
            index=GinIndex(
                fields=['bulky_disease_criteria_required'],
                name='idx_bulky_disease_gin',
                opclasses=['jsonb_ops'],
            ),
        ),
        migrations.AddIndex(
            model_name='trial',
            index=GinIndex(
                fields=['mipi_risks_required'],
                name='idx_mipi_risks_gin',
                opclasses=['jsonb_ops'],
            ),
        ),
        migrations.AddIndex(
            model_name='trial',
            index=GinIndex(
                fields=['mipi_c_risks_required'],
                name='idx_mipi_c_risks_gin',
                opclasses=['jsonb_ops'],
            ),
        ),
    ]
