from django.db import migrations

MYELOMA_RELATED_DISORDERS = [
    ('mgus', 'Monoclonal Gammopathy of Undetermined Significance (MGUS)',
     'MGUS; monoclonal gammopathy of undetermined significance; monoclonal gammopathy'),
    ('smoldering_multiple_myeloma', 'Smoldering Multiple Myeloma',
     'SMM; smoldering myeloma; smouldering myeloma'),
    ('waldenstrom_macroglobulinemia', 'Waldenström Macroglobulinemia',
     "Waldenstrom macroglobulinemia; Waldenström's macroglobulinemia; WM"),
    ('igm_multiple_myeloma', 'IgM Multiple Myeloma', 'IgM myeloma; IgM MM'),
    ('al_amyloidosis', 'Light-Chain (AL) Amyloidosis',
     'AL amyloidosis; primary amyloidosis; light chain amyloidosis; AL amyloid'),
    ('poems_syndrome', 'POEMS Syndrome',
     'POEMS syndrome; polyneuropathy, organomegaly, endocrinopathy, monoclonal protein, skin changes'),
]


def seed(apps, schema_editor):
    Model = apps.get_model('trials', 'MyelomaRelatedDisorder')
    for code, title, hint in MYELOMA_RELATED_DISORDERS:
        Model.objects.update_or_create(code=code, defaults={'title': title, 'llm_hint': hint})


def unseed(apps, schema_editor):
    Model = apps.get_model('trials', 'MyelomaRelatedDisorder')
    Model.objects.filter(code__in=[c for c, _, _ in MYELOMA_RELATED_DISORDERS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('trials', '0025_myeloma_related_disorders'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
