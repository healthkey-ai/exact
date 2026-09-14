from django.db.models import BigIntegerField, Sum
from django.db.models.expressions import RawSQL

from trials.models import Trial
from trials.services.user_to_trial_attrs_mapper import UserToTrialAttrsMapper


class BlankAttributeRecordsCount:
    """Count trials that are blank for each user-attribute candidate.

    For every entry returned by `UserToTrialAttrsMapper.potential_attrs_to_check`,
    sums a per-trial `(CASE WHEN <blank-condition> THEN <then> END)` expression
    across the scope. Used to rank candidate attributes by how many trials they
    would unlock.

    The CASE-WHEN strings come from a static mapping (`USER_TO_TRIAL_ATTRS_MAPPING`)
    so there's no user-supplied SQL on the path. Migrating from the deprecated
    `.extra(select=…)` to `aggregate(Sum(RawSQL(…)))` (#25) — same SQL emitted,
    same return shape; only the Django-side surface changes. The remaining
    `.extra()` callsites in trials/querysets/trial.py:214-227 are tracked
    separately in #97.
    """

    def counts(self, scope=None, patient_info=None):
        if scope is None:
            scope = Trial.objects.all()

        if patient_info is None:
            return {}

        sql_conditions = UserToTrialAttrsMapper().potential_attrs_to_check(patient_info)
        if not sql_conditions:
            return {}

        # BigIntegerField, not IntegerField: when `counts` is passed into
        # the mapper, the CASE-WHEN's ELSE branch uses arbitrary count
        # values that sum across the catalog can exceed 2^31. Postgres
        # SUM yields bigint; declaring IntegerField would silently
        # truncate via Django's IntegerField.to_python coercion.
        aggregations = {
            attr: Sum(RawSQL(sql_conditions[attr], []), output_field=BigIntegerField())
            for attr in sql_conditions
        }
        out = scope.aggregate(**aggregations)
        return {k: v for k, v in out.items() if v is not None}
