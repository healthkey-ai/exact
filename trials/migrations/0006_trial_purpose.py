"""Add TrialPurpose lookup + Trial.purpose FK + seed (#44).

Purpose codes mirror the top-level keys of
`trials.trial_taxonomy.TRIAL_TAXONOMY` (treatment, prevention,
diagnostic, screening, supportive_care, health_services_research,
basic_science, device_feasibility, other). The seed step uses
`update_or_create` keyed on `code` so re-running refreshes titles to
the canonical values.
"""
import django.db.models.deletion
from django.db import migrations, models


_PURPOSE_TITLES: dict[str, str] = {
    'treatment': 'Treatment',
    'prevention': 'Prevention',
    'diagnostic': 'Diagnostic',
    'screening': 'Screening',
    'supportive_care': 'Supportive Care',
    'health_services_research': 'Health Services Research',
    'basic_science': 'Basic Science',
    'device_feasibility': 'Device Feasibility',
    'other': 'Other',
}


def _seed_purposes(apps, schema_editor):
    TrialPurpose = apps.get_model('trials', 'TrialPurpose')
    for code, title in _PURPOSE_TITLES.items():
        TrialPurpose.objects.update_or_create(code=code, defaults={'title': title})


def _noop_reverse(apps, schema_editor):
    # TrialPurpose rows are referenced by Trial.purpose (PROTECT FK).
    # Blanket-deleting them would raise ProtectedError on any DB with
    # existing trials whose purpose is set; the field is nullable so a
    # cleanup migration can null out references before purging.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('trials', '0005_seed_trial_taxonomy'),
    ]

    operations = [
        migrations.CreateModel(
            name='TrialPurpose',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('code', models.TextField(db_index=True, unique=True)),
                ('title', models.TextField(db_index=True, unique=True)),
            ],
            options={
                'abstract': False,
            },
        ),
        migrations.AddField(
            model_name='trial',
            name='purpose',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='trials',
                to='trials.trialpurpose',
            ),
        ),
        migrations.RunPython(_seed_purposes, _noop_reverse),
    ]
