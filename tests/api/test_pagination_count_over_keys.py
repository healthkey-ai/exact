"""The paginator must not count a DISTINCT trial scope row-by-row (#492).

`by_location` joins LocationTrial and restores one row per trial with
`.distinct()`, so Django renders `.count()` as a COUNT over a subquery that
selects DISTINCT over every column of the trial — ~150 of them, across 43k
join rows — and the paginator does that for every paginated response.

Measured on a 3,114-trial corpus (43,021 locationtrial rows), same answer
(2,037) both ways: 5.1s through Django's paginator, 48.9s for the raw
annotated `.count()` depending on cache state, against 0.015s counted over
primary keys.

Asserted on the SHAPE of the SQL rather than on elapsed time, which would be
flaky: the old form names every trial column in the count query, the new one
names the key and nothing else.
"""
import pytest
from django.core.paginator import Paginator
from django.test.utils import CaptureQueriesContext
from django.db import connection

from tests.factories import TrialFactory
from trials.api.pagination import CountDistinctByKey, count_over_keys
from trials.api.pagination import TrialsPagination
from trials.models import Country, Location, LocationTrial, Trial


@pytest.fixture
def sited(db):
    """Two trials, one of them at two sites — so a join without `.distinct()`
    would double-count it and the fixture can tell the two apart."""
    country = Country.objects.create(title='Countland')
    a, b = TrialFactory(), TrialFactory()
    for i, trial in ((0, a), (1, a), (2, b)):
        LocationTrial.objects.create(
            trial=trial,
            location=Location.objects.create(
                city=f'c{i}', title=f'Site {i}', country=country,
            ),
        )
    return country


def _count_sql(paginator_class, scope):
    with CaptureQueriesContext(connection) as captured:
        count = paginator_class(scope, 20).count
    sql = ' '.join(q['sql'] for q in captured.captured_queries)
    return count, sql


@pytest.mark.django_db
class TestCountDistinctByKey:
    def _scope(self, country):
        return Trial.objects.filter(
            locationtrial__location__country=country
        ).distinct().order_by('id')

    def test_it_counts_the_same_trials_as_django_does(self, sited):
        scope = self._scope(sited)
        assert CountDistinctByKey(scope, 20).count == Paginator(scope, 20).count == 2

    def test_the_count_query_names_the_key_and_not_every_column(self, sited):
        """The defect and the fix are only distinguishable by shape."""
        scope = self._scope(sited)
        _, ours = _count_sql(CountDistinctByKey, scope)
        _, djangos = _count_sql(Paginator, scope)

        # Naming one WIDE column rather than counting columns: a count that
        # survives a Django version change or a new field on the model.
        wide = '"trials_trial"."brief_title"'
        assert wide in djangos, (
            'fixture assumes Django counts by selecting the whole row'
        )
        assert wide not in ours, (
            'the count query still selects trial columns — it is deduplicating '
            'rows rather than keys'
        )

    def test_a_plain_scope_is_left_alone(self, sited):
        """No `.distinct()`, no subquery: a plain count is already one
        aggregate and re-deriving it would cost a pointless round trip."""
        plain = Trial.objects.all().order_by('id')
        _, ours = _count_sql(CountDistinctByKey, plain)
        _, djangos = _count_sql(Paginator, plain)
        assert ours == djangos


@pytest.mark.django_db
class TestItIsActuallyWiredIn:
    """The class is not the fragile part — the one line that installs it is.

    Deleting `django_paginator_class` leaves the class, its three tests and the
    whole suite green while the fix does nothing in production. That is the
    mutation worth guarding, and the first version of this file did not.
    """

    def test_the_trials_paginator_uses_it(self):
        assert TrialsPagination.django_paginator_class is CountDistinctByKey

    def test_the_count_endpoint_counts_over_keys(self, sited):
        """`/trials/count/` counts without paginating, so the paginator cannot
        cover it — and it is the endpoint whose whole job is the count."""
        scope = Trial.objects.filter(
            locationtrial__location__country=sited
        ).distinct()
        with CaptureQueriesContext(connection) as captured:
            count = count_over_keys(scope)
        sql = ' '.join(q['sql'] for q in captured.captured_queries)

        assert count == 2
        assert '"trials_trial"."brief_title"' not in sql


@pytest.mark.django_db
class TestScopesItMustLeaveAlone:
    def test_a_values_scope_keeps_djangos_answer(self, sited):
        """A `.values()` page yields distinct VALUE TUPLES; counting distinct
        PKs answers a different question. Measured 1 against 2 before the
        guard — unreachable today, but this paginator serves three viewsets."""
        for scope in (
            Trial.objects.values('register').distinct(),
            Trial.objects.values_list('register', flat=True).distinct(),
        ):
            assert count_over_keys(scope) == scope.count()
