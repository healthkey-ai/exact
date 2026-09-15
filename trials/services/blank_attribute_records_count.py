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
        # Re-derive the scope by primary key before aggregating.
        #
        # The CASE-WHEN strings name columns UNQUALIFIED (`age_low_limit`, not
        # `trials_trial.age_low_limit`), which is fine while the aggregate runs
        # against the table itself. It stops being fine the moment the scope
        # carries a `.distinct()`, a join or a slice: Django then computes the
        # aggregate over a DERIVED TABLE, whose select list does not carry those
        # columns, and Postgres answers `column "age_low_limit" does not exist`
        # — a 500 on the search endpoint.
        #
        # `by_location` is exactly such a scope (it joins LocationTrial and
        # ends in `.distinct()`), so every search carrying a resolvable country
        # hit this. Qualifying the columns would NOT help: the outer query's
        # FROM holds only the derived table, so `trials_trial.age_low_limit`
        # fails too, just with a different message.
        #
        # `pk__in` keeps the same set of trials — the ids come from the same
        # scope — while giving the aggregate a plain, un-joined queryset to run
        # against. It fixes the whole class rather than the one filter that
        # happened to expose it, and it leaves the shared queryset code
        # byte-identical to CancerBot's (docs/porting-from-cancerbot.md).
        flat = Trial.objects.filter(pk__in=scope.values('pk'))
        out = flat.aggregate(**aggregations)
        return {k: v for k, v in out.items() if v is not None}
