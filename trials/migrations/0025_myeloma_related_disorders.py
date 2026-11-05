from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trials', '0024_cb_clinical_contract_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='MyelomaRelatedDisorder',
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
            name='myeloma_related_disorders_excluded',
            field=models.JSONField(blank=True, default=list),
        ),
    ]
