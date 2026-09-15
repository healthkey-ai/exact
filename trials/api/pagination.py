import math
from django.core.paginator import Paginator
from django.utils.functional import cached_property
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


def count_over_keys(scope):
    """Count a trial scope without deduplicating whole rows.

    The same technique `CountDistinctByKey` applies to paginated responses,
    exposed for the endpoints that count WITHOUT paginating — `/trials/count/`
    measured 7.1s against 0.018s on the same scope.

    Falls through to a plain `.count()` for anything that is not a distinct
    model scope, so a caller never has to decide.
    """
    query = getattr(scope, 'query', None)
    if query is None or not getattr(query, 'distinct', False) or query.values_select:
        return scope.count()
    return (
        scope.model._base_manager
        .using(scope.db)
        .filter(pk__in=scope.values('pk'))
        .count()
    )


class CountDistinctByKey(Paginator):
    """Count a DISTINCT trial scope over its primary keys, not its rows.

    `by_location` joins LocationTrial and then puts the trials back with
    `.distinct()`, so Django renders `.count()` as

        SELECT COUNT(*) FROM (SELECT DISTINCT <~150 wide columns> FROM
                              trials_trial JOIN trials_locationtrial …) sub

    — deduplicating whole rows, ~150 columns at a time, across 43k join rows.
    The paginator calls this for EVERY paginated response, and the goodness
    annotations compound it. Measured twice, independently, on a 3,114-trial
    corpus with 43,021 locationtrial rows — same answer, 2,037 trials, always:

        through Django's paginator, country scope     5.1s / 7.7s
        worst real view shape (sort=distance + geo)      22.9s
        counted over primary keys                    0.012s-0.035s

    The two columns are two machines, warm; a third reading of 48.9s did not
    reproduce and is left out rather than quoted. The ratio is the point: the
    ids are already distinct, so counting them needs none of that width.

    The ids are already distinct, so counting them needs none of that width.
    `_base_manager` because this counts a set of ids that the scope already
    decided; `using(db)` so a split-database deployment stays on the alias the
    scope was built for.

    Only for a `.distinct()` scope — a plain `.count()` is already a single
    aggregate and re-deriving it would cost a pointless subquery.
    """

    @cached_property
    def count(self):
        return count_over_keys(self.object_list)


class TrialsPagination(PageNumberPagination):
    django_paginator_class = CountDistinctByKey
    page_size = 20
    page_size_query_param = 'limit'
    max_page_size = 200
    page_query_param = 'page'

    def get_page_size(self, request):
        """Reject `?limit=N` requests where N > max_page_size with 400 (#33).

        DRF's default behavior silently clamps the value to max_page_size,
        which both hides abuse (a client asking for 10000 rows looks fine
        in logs) and gives the caller fewer results than they requested
        without telling them.
        """
        if not self.page_size_query_param:
            return self.page_size

        raw = request.query_params.get(self.page_size_query_param)
        if raw is None:
            return self.page_size

        try:
            requested = int(raw)
        except (TypeError, ValueError):
            # Don't echo unbounded user input back in the error body — a
            # 1MB ?limit=<garbage> would otherwise reflect into the 400
            # response.
            raise ValidationError({
                self.page_size_query_param: ['must be a positive integer']
            })
        if requested < 1:
            raise ValidationError({
                self.page_size_query_param: [f'must be >= 1, got {requested}']
            })
        if requested > self.max_page_size:
            raise ValidationError({
                self.page_size_query_param: [
                    f'{requested} exceeds maximum allowed value {self.max_page_size}'
                ]
            })
        return requested

    def get_paginated_response(self, data, extra_keys: dict = None):
        total_items = self.page.paginator.count
        page_size = self.page.paginator.per_page
        total_pages = math.ceil(total_items / page_size)

        if extra_keys is None:
            extra_keys = {}

        return Response({
            'results': data,
            'count': total_pages,
            'itemsTotalCount': total_items,
            'next': self.get_next_link(),
            'previous': self.get_previous_link(),
            **extra_keys,
        })
