"""Health check for the platform's load balancer.

Checks the default database, deliberately. A liveness probe that only
proves Django booted stays green through a deploy whose migrations never
ran — the service then answers every real request with a 500 while the
platform reports it healthy. One round trip is cheap next to that.

Deliberately does NOT touch the `trials` database. That alias points at an
external host this service does not own, so probing it here would let an
unrelated outage take this service out of rotation even though the trial
endpoints degrade gracefully on their own.
"""
from django.db import connection
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response


@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:
        return Response(
            {'status': 'error', 'database': 'unavailable'},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return Response({'status': 'ok', 'database': 'ok'})
