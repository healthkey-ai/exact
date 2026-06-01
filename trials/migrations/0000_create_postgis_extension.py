from django.contrib.postgres.operations import CreateExtension
from django.db import migrations


class Migration(migrations.Migration):
    # Must create the postgis extension before 0001_initial builds the
    # geometry column (trials.Site.geo_point). run_before places this ahead of
    # the existing initial migration without editing it.
    initial = True

    dependencies = []

    run_before = [
        ('trials', '0001_initial'),
    ]

    operations = [
        CreateExtension('postgis'),
    ]
