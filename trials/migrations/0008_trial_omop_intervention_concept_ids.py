from django.contrib.postgres.indexes import GinIndex
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trials", "0007_alter_trial_benefit_score_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="trial",
            name="omop_intervention_concept_ids",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddIndex(
            model_name="trial",
            index=GinIndex(
                fields=["omop_intervention_concept_ids"],
                name="idx_omop_intervention_cids_gin",
                opclasses=["jsonb_ops"],
            ),
        ),
    ]
