"""Populate the moved component→category lookup so type matching is not empty
between the model move (#262) and the next vocab sync / rebuild.

The lookup moved from the ``trials`` app to ``vocab_mirror`` (0003 here +
``trials.0020_delete_componentcategoryomoplookup``). Without this, an existing
deployment would drop the populated ``trials`` table and start the new one empty
— and since the activation gate deliberately lets an *empty* lookup through, OMOP
"type" criteria would silently resolve to ``[]`` (false negatives) until an
operator ran a rebuild. So we rebuild the payload here, from the local
component↔category M2M graph (the source of truth), exactly as
``build_component_category_lookup`` does. No release stamp is written (there may be
no active release at migrate time); the payload is release-independent and the next
sync stamps it for the release it activates.
"""
from django.db import migrations


def populate(apps, schema_editor):
    Connection = apps.get_model('trials', 'TherapyComponentCategoryConnection')
    Lookup = apps.get_model('vocab_mirror', 'ComponentCategoryOmopLookup')
    db = schema_editor.connection.alias  # where vocab_mirror lives ('default')

    # Same derivation as build_component_category_lookup: component omop_concept_id
    # → union of the CB category codes it belongs to. The M2M read routes to the
    # trials DB via the router; the write goes to `db` (default).
    pairs = (
        Connection.objects
        .filter(component__omop_concept_id__isnull=False, category__isnull=False)
        .values_list('component__omop_concept_id', 'category__code')
    )
    lookup = {}
    for concept_id, code in pairs:
        if not code:
            continue
        lookup.setdefault(concept_id, set()).add(code)

    rows = [
        Lookup(component_concept_id=cid, category_codes=sorted(codes))
        for cid, codes in lookup.items()
    ]
    if rows:
        Lookup.objects.using(db).bulk_create(rows, ignore_conflicts=True)


class Migration(migrations.Migration):

    dependencies = [
        ('vocab_mirror', '0003_componentcategoryomoplookup_componentlookupstamp'),
        # Read the M2M in its current shape (harmless if the trials DB is external).
        ('trials', '0019_rename_ctomop_to_promop_help_text'),
    ]

    operations = [
        # Reverse is a no-op: reversing 0003 drops the table (and its rows) anyway.
        migrations.RunPython(populate, migrations.RunPython.noop),
    ]
