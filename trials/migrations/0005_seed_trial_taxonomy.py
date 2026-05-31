"""Seed TrialType rows from trial_taxonomy at migrate time.

Connection rows (TrialTypeDiseaseConnection) are NOT created here —
Disease rows are seeded later by domain loaders, so they don't exist
at migration time on a fresh DB. The loader
`trials.services.loaders.load_trial_taxonomy.LoadTrialTaxonomy` fills
in connections after Disease rows are present (called from
`tests/conftest.py` for the test suite and from
`seed_reference_data` for production).

The taxonomy is authoritative for titles — we use update_or_create so
that re-running the migration (or running it on top of a manually
edited table) refreshes the title to the canonical taxonomy value.

This migration coexists with the legacy `_seed_trial_types` in
`seed_reference_data.py` (which seeds a disjoint set of legacy codes
like `chemo_and_steroids`, `antibody_based_immunotherapies`, …). Pruning
those legacy codes is tracked as a follow-up.
"""
from django.db import migrations

from trials.trial_taxonomy import ALL_TRIAL_TYPES


def _seed_trial_types(apps, schema_editor):
    TrialType = apps.get_model('trials', 'TrialType')

    for code, title, _diseases in ALL_TRIAL_TYPES:
        TrialType.objects.update_or_create(
            code=code, defaults={'title': title}
        )


def _noop_reverse(apps, schema_editor):
    # TrialType rows are referenced by Trial.trial_type (PROTECT FK in
    # production). Blanket-deleting them would raise ProtectedError on
    # any DB with existing Trials. Leave them in place on rollback; a
    # deliberate cleanup migration can purge later if needed.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('trials', '0004_trial_hot_path_indexes'),
    ]

    operations = [
        migrations.RunPython(_seed_trial_types, _noop_reverse),
    ]
