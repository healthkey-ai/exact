from django.db import migrations, models, router


def seed_markers(apps, schema_editor):
    """Populate the new catalogs so a single-DB install isn't left empty.

    On a single-database install the DeleteModel operations below drop the
    seeded `trials_marker*` rows, and `migrate` does not run
    `seed_reference_data` (only the Makefile target does), so `/form-settings/`
    would serve `{'none': 'None'}` and CTOMOP marker-label normalization would
    drop every marker — reading as *unknown* to the matcher — until an operator
    reseeded by hand. Seed inline instead: same source and semantics as
    `LoadMarkers`, idempotent via update_or_create.

    Deliberately scoped to EXACT's own database. Under `TRIALS_DB_MIGRATE` the
    router would allow writes to the 'trials' alias, but that catalog is
    CB-owned and already carries these rows — EXACT does not seed reference
    data into it (ADR: the trials DB is externally managed, read-only).
    """
    alias = schema_editor.connection.alias
    if alias != 'default':
        return
    if not router.allow_migrate(alias, 'trials', model_name='cytogenicmarker'):
        return

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
        # AlterUniqueTogether MUST precede the RemoveFields it references —
        # the autodetector emits it after them, and replaying the migration
        # then dies with "MarkerCategoryConnection has no field named 'marker'".
        # This order matches CB's equivalent migration (0382).
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
