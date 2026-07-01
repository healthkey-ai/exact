import math
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class TrialsPagination(PageNumberPagination):
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
