"""Drop the standalone UNIQUE on `Identity.sub` (#700).

`sub` is unique only within an issuer; the real key is the composite
(issuer, sub) constraint 0001 already created. A forward AlterField rather
than an edit of 0001, so databases that already applied 0001 lose the global
unique index too.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="identity",
            name="sub",
            field=models.CharField(max_length=255),
        ),
    ]
