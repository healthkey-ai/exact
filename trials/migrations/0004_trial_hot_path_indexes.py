"""
Add B-tree indexes to Trial.enrollment_count and Trial.last_update_date (#27).

These columns are used as ORDER BY targets at trials_views.py:156,158 (both
`.desc(nulls_last=True)`) and last_update_date is also filtered by
`by_date_since()` at querysets/trial.py:517. The indexes are declared with
the same DESC NULLS LAST ordering so the planner can do an index scan
with early termination on the LIMIT-paginated list endpoint — a plain
ASC NULLS LAST B-tree (Django's default Index) cannot serve a DESC NULLS
LAST query and the planner would fall back to seq scan + sort.

trial_type is a ForeignKey — Django auto-indexes FKs, so no separate
operation here.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('trials', '0003_trial_mcl_fields'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='trial',
            index=models.Index(
                models.F('enrollment_count').desc(nulls_last=True),
                name='idx_trials_enroll_count',
            ),
        ),
        migrations.AddIndex(
            model_name='trial',
            index=models.Index(
                models.F('last_update_date').desc(nulls_last=True),
                name='idx_trials_last_update',
            ),
        ),
    ]
