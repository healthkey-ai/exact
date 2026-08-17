from django.db import migrations, models


def seed_markers(apps, schema_editor):
    """Populate the new catalogs so no install is left with empty ones.

    The DeleteModel operations below drop the seeded `trials_marker*` rows, and
    `migrate` does not run `seed_reference_data` (only the Makefile target
    does). Without this, `/form-settings/` would serve `{'none': 'None'}` and
    CTOMOP marker-label normalization would drop every marker — reading as
    *unknown* to the matcher, which turns hard exclusions into "potential" —
    until an operator reseeded by hand. Seed inline instead: same source and
    semantics as `LoadMarkers`, idempotent via update_or_create.

    No alias guard here, deliberately. `RunPython.database_forwards` already
    calls `router.allow_migrate(alias, 'trials')`, which resolves correctly for
    every mode: single-DB seeds; split-DB skips (the CB-owned catalog is never
    migrated); and under `TRIALS_DB_MIGRATE` — which exists precisely for a
    trials copy we own and keep current, see `exact/db_router.py` — the copy
    gets seeded like any other DB we own. An earlier `alias != 'default'` guard
    here broke exactly that last case: the tables were dropped and recreated
    empty, and seeding was skipped.
    """
    alias = schema_editor.connection.alias

    from trials.services.markers_mapper import MarkersMapper

    mapper = MarkersMapper()
    for model_name, data in (('CytogenicMarker', mapper.cytogenic()),
                             ('MolecularMarker', mapper.molecular())):
        model = apps.get_model('trials', model_name)
        for code, obj in data.items():
            model.objects.using(alias).update_or_create(
                code=code,
                defaults={'title': obj['name'], 'description': obj['description']},
            )


class Migration(migrations.Migration):
    """Align the trials app with CB's catalog schema.

    EXACT reads an externally-managed CB catalog, so the trials-app model state
    must be a strict subset of CB's schema — otherwise every CB-derived catalog
    (including the pruned Harvard extract) needs DDL before EXACT can read it.

    NOTE: intentional, irreversible data loss on single-database installs.
    Reversing restores the schema but not the contents of
    `rawdataitem.old_raw_data` / `.extracted_data` (removed upstream in CB
    0407), `trial.omop_intervention_concept_ids` (EXACT-only, never populated
    by CB), the dropped `trials_marker*` rows (re-seedable — see
    `seed_markers`), or the RawDataItem / TrialUniverse* tables, which EXACT
    declares but never reads and the Harvard extract DROPs outright.
    Split-database deployments lose nothing: the router keeps this migration
    off the CB-owned catalog.

    Leaves stale `django_content_type` / `auth_permission` rows for the deleted
    models on single-DB installs — Django never removes them. Harmless; clear
    them with `manage.py remove_stale_contenttypes` if you care.
    """

    dependencies = [
        ('trials', '0008_trial_omop_intervention_concept_ids'),
    ]

    operations = [
        # ── Port of CB 0375: the `_mcl` suffix is dropped upstream because
        # "largest lesion size" is a generic measurement, not MCL-specific.
        # RenameField (not remove+add) so existing values survive.
        migrations.RenameField(
            model_name='trial',
            old_name='lesion_size_mcl_min',
            new_name='largest_lesion_size_min',
        ),
        migrations.RenameField(
            model_name='trial',
            old_name='lesion_size_mcl_max',
            new_name='largest_lesion_size_max',
        ),

        # ── Markers: EXACT's consolidated trio -> CB's two flat catalogs.
        migrations.CreateModel(
            name='CytogenicMarker',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('code', models.TextField(db_index=True, unique=True)),
                ('title', models.TextField(db_index=True, unique=True)),
                ('description', models.TextField(blank=True, null=True)),
            ],
            options={
                'verbose_name': 'cytogenetic marker',
                'verbose_name_plural': 'cytogenetic markers',
            },
        ),
        migrations.CreateModel(
            name='MolecularMarker',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True, db_index=True)),
                ('code', models.TextField(db_index=True, unique=True)),
                ('title', models.TextField(db_index=True, unique=True)),
                ('description', models.TextField(blank=True, null=True)),
            ],
            options={
                'abstract': False,
            },
        ),
        # AlterUniqueTogether MUST precede the RemoveFields it references, or
        # replaying the migration dies with "MarkerCategoryConnection has no
        # field named 'marker'". (When this was generated on the sibling branch
        # the autodetector emitted it after the removals; whatever the cause,
        # this order is the correct one and matches CB's equivalent 0382.)
        migrations.AlterUniqueTogether(
            name='markercategoryconnection',
            unique_together=None,
        ),
        migrations.RemoveField(
            model_name='marker',
            name='categories',
        ),
        migrations.RemoveField(
            model_name='markercategoryconnection',
            name='marker',
        ),
        migrations.RemoveField(
            model_name='markercategoryconnection',
            name='category',
        ),
        migrations.DeleteModel(
            name='Marker',
        ),
        migrations.DeleteModel(
            name='MarkerCategory',
        ),
        migrations.DeleteModel(
            name='MarkerCategoryConnection',
        ),

        # ── ?therapy_id=: EXACT-only column CB never had or populated.
        migrations.RemoveIndex(
            model_name='trial',
            name='idx_omop_intervention_cids_gin',
        ),
        migrations.RemoveField(
            model_name='trial',
            name='omop_intervention_concept_ids',
        ),

        # ── Models EXACT declares but never reads; absent from the Harvard
        # extract, which DROPs all three.
        migrations.DeleteModel(
            name='RawDataItem',
        ),
        migrations.AlterUniqueTogether(
            name='trialuniverseentry',
            unique_together=None,
        ),
        migrations.RemoveField(
            model_name='trialuniverseentry',
            name='universe',
        ),
        migrations.RemoveField(
            model_name='trialuniverseentry',
            name='trial',
        ),
        migrations.DeleteModel(
            name='TrialUniverse',
        ),
        migrations.DeleteModel(
            name='TrialUniverseEntry',
        ),

        migrations.RunPython(seed_markers, migrations.RunPython.noop),
    ]
