"""
Regression test for #26 — sorted_locations_by_distance must honor the
locationtrial_set prefetch declared in TrialsViewSet.get_queryset, instead
of issuing a fresh `self.locationtrial_set.select_related('location')`
query per trial.

The serializer (`TrialSerializer.to_representation`) calls this method on
every trial in the page. Pre-fix, that meant N additional queries per
page (one per Trial). Post-fix, the prefetched cache is reused and the
query count stays flat regardless of page size.
"""
import pytest
from django.db.models import Prefetch

from trials.models import LocationTrial, Trial
from tests.factories import LocationFactory, TrialFactory


def _build_n_trials_with_locations(n):
    loc_a = LocationFactory(title=f'loc_a_{n}')
    loc_b = LocationFactory(title=f'loc_b_{n}')
    trials = []
    for i in range(n):
        trial = TrialFactory()
        LocationTrial.objects.create(trial=trial, location=loc_a, recruitment_status='RECRUITING')
        LocationTrial.objects.create(trial=trial, location=loc_b, recruitment_status='RECRUITING')
        trials.append(trial)
    return trials


def _list_qs():
    """Mirror the prefetch TrialsViewSet.get_queryset uses for /trials/."""
    return Trial.objects.prefetch_related(
        Prefetch(
            'locationtrial_set',
            queryset=LocationTrial.objects.select_related('location'),
        )
    )


class TestLocationTrialPrefetch:
    @pytest.mark.django_db
    def test_sorted_locations_uses_prefetch_when_available(self, django_assert_num_queries):
        """With the list-endpoint prefetch in place, calling
        sorted_locations_by_distance per Trial must issue zero
        additional queries.
        """
        _build_n_trials_with_locations(3)

        # Materialise the prefetched queryset — 1 query for trials, 1 for
        # locationtrial_set, 1 for the joined location rows.
        trials = list(_list_qs())
        assert len(trials) == 3

        # The hot path: serialise each Trial's location list. Pre-fix this
        # was N additional queries (one per trial); post-fix it's zero.
        with django_assert_num_queries(0):
            for trial in trials:
                locations = trial.sorted_locations_by_distance(None)
                # Touch .location to make sure it's resolved without a
                # fresh query (select_related is part of the prefetch).
                assert all(lt.location is not None for lt in locations)

    @pytest.mark.django_db
    def test_query_count_constant_across_page_sizes(self, django_assert_num_queries):
        """Pre-fix: query count grew linearly with page size (N+constant).
        Post-fix: same constant query count regardless of page size."""
        # Small page
        _build_n_trials_with_locations(2)
        trials_small = list(_list_qs())
        with django_assert_num_queries(0):
            for trial in trials_small:
                trial.sorted_locations_by_distance(None)

        # Larger page — still zero extra queries after the prefetch
        # materialisation above.
        _build_n_trials_with_locations(5)
        trials_large = list(_list_qs())
        with django_assert_num_queries(0):
            for trial in trials_large:
                trial.sorted_locations_by_distance(None)

    @pytest.mark.django_db
    def test_fallback_path_still_works_without_prefetch(self):
        """Callers that don't prefetch (e.g. ad-hoc model-method use
        outside the list endpoint) must keep getting correct results
        via the original QuerySet path."""
        trials = _build_n_trials_with_locations(1)
        trial = Trial.objects.get(pk=trials[0].pk)  # NOT prefetched

        locations = trial.sorted_locations_by_distance(None)
        # Two LocationTrial rows seeded above.
        assert len(list(locations)) == 2

    @pytest.mark.django_db
    def test_recruitment_status_filter_applies_in_python_when_prefetched(self):
        """Filter must work the same whether we're using the prefetched
        cache (Python filter) or the fallback (DB filter)."""
        # Need two distinct locations: (trial, location) is uniquely
        # constrained on LocationTrial.
        loc_a = LocationFactory(title='filterloc_a')
        loc_b = LocationFactory(title='filterloc_b')
        trial = TrialFactory()
        LocationTrial.objects.create(trial=trial, location=loc_a, recruitment_status='RECRUITING')
        LocationTrial.objects.create(trial=trial, location=loc_b, recruitment_status='COMPLETED')

        # Prefetched path
        trial_prefetched = _list_qs().get(pk=trial.pk)
        result = trial_prefetched.sorted_locations_by_distance(None, recruitment_status='RECRUITING')
        assert all(lt.recruitment_status == 'RECRUITING' for lt in result)
        assert len(list(result)) == 1

        # Fallback path
        trial_plain = Trial.objects.get(pk=trial.pk)
        result_plain = trial_plain.sorted_locations_by_distance(None, recruitment_status='RECRUITING')
        assert all(lt.recruitment_status == 'RECRUITING' for lt in result_plain)
        assert len(list(result_plain)) == 1

    @pytest.mark.django_db
    def test_closest_location_wins_with_geo_point(self):
        """The novel with-geo branch (Python distance sort via geopy):
        closest location must be returned, and missing-geo LocationTrials
        must sort last regardless of how the prefetched cache happened
        to be ordered.
        """
        from django.contrib.gis.geos import Point as GeoPoint

        # Patient is in Boston (~42.36 N, ~-71.06 E).
        boston = GeoPoint(-71.0589, 42.3601, srid=4326)
        # Three locations: one near Boston, one in San Francisco, one with
        # no geo at all. (PointField stores x=lon, y=lat.)
        near = LocationFactory(title='cambridge', city='cambridge')
        far = LocationFactory(title='sf', city='sf')
        no_geo = LocationFactory(title='no_geo', city='unknown')
        near.geo_point = GeoPoint(-71.1097, 42.3736, srid=4326)
        near.save()
        far.geo_point = GeoPoint(-122.4194, 37.7749, srid=4326)
        far.save()
        # no_geo.geo_point left None

        trial = TrialFactory()
        LocationTrial.objects.create(trial=trial, location=no_geo, recruitment_status='RECRUITING')
        LocationTrial.objects.create(trial=trial, location=far, recruitment_status='RECRUITING')
        LocationTrial.objects.create(trial=trial, location=near, recruitment_status='RECRUITING')

        # Prefetched path
        trial_prefetched = _list_qs().get(pk=trial.pk)
        result = trial_prefetched.sorted_locations_by_distance(boston)
        assert len(result) == 1
        assert result[0].location.title == 'cambridge'

        # Fallback path (no prefetch) must return the same closest pick.
        trial_plain = Trial.objects.get(pk=trial.pk)
        result_plain = trial_plain.sorted_locations_by_distance(boston)
        assert len(result_plain) == 1
        assert result_plain[0].location.title == 'cambridge'

    @pytest.mark.django_db
    def test_get_distance_obj_uses_prefetch_cache(self, django_assert_num_queries):
        """Companion fix for the actual production N+1: TrialSerializer
        falls through to instance.get_distance() when distance isn't DB-
        annotated, which calls get_distance_obj. Before the fix that
        method's .filter(recruitment_status__in=...) clone issued a fresh
        query per Trial.
        """
        from django.contrib.gis.geos import Point as GeoPoint

        loc = LocationFactory(title='loc_for_distance', city='boston')
        loc.geo_point = GeoPoint(-71.0589, 42.3601, srid=4326)
        loc.save()
        # 3 trials, each with one RECRUITING LocationTrial
        trials = []
        for _ in range(3):
            t = TrialFactory()
            LocationTrial.objects.create(trial=t, location=loc, recruitment_status='RECRUITING')
            trials.append(t)

        # Patient with geo
        class _Stub:
            geo_point = GeoPoint(-71.1097, 42.3736, srid=4326)
        pi = _Stub()

        # Prefetched + with recruitment_status filter (the previously
        # broken path).
        trials_pf = list(_list_qs())
        with django_assert_num_queries(0):
            for t in trials_pf:
                t.get_distance_obj(pi, recruitment_status='RECRUITING')
