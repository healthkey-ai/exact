"""No request body may produce a 5xx, and a refused one may leave no row.

Two rules, stated over GENERATED bodies rather than remembered ones. The
first version of this file was sixteen hand-written examples with a docstring
arguing for generation, and the next review found two 5xx it did not cover:
a non-object body to the erasure endpoint, and a string PostgreSQL `jsonb`
cannot store. Both are "an input nobody enumerated", which is what a
generator is for and a list is not.

No `hypothesis` in this repo, and adding a dependency is not this change's
call, so the generator is the same seeded LCG the frontend property tests
use: a failure names a seed, and the seed reproduces it.

The second rule is the one three rounds kept re-opening in new spellings —
a GET that wrote, a 400 that wrote, a 500 that wrote — and it is now a
property rather than three fixes.
"""
import json

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from user_state.models import TrialSearchPreferences

PREFS = '/user-state/trial-search-preferences/'
FORGET = '/user-state/internal/forget/'


def lcg(seed):
    state = seed & 0xFFFFFFFF

    def rand():
        nonlocal state
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        return state / 0x100000000

    return rand


# Characters and shapes that parse as JSON and have each broken something:
# a NUL and a lone surrogate are refused by `jsonb`; deep nesting used to
# exhaust the parser's stack; `[]` and `{}` read as set filters.
NASTY_STRINGS = [
    '', ' ', 'x' * 300, 'x\x00y', '\ud800', '\U0001f9ec', 'II', 'null',
    '"; DROP TABLE', '\\', '\n\t', 'фаза',
]
SCALARS = [None, True, False, 0, 1, -1, 1.5, 1e308]
KEYS = ['phase', 'searchTitle', 'sort', 'type', 'benefitWeight', 'distance',
        'distanceUnits', 'therapy_id', '', 'x' * 100, '\x00', 'фаза']


def a_value(rand, depth=0):
    roll = rand()
    if depth < 3 and roll < 0.15:
        return [a_value(rand, depth + 1) for _ in range(int(rand() * 4))]
    if depth < 3 and roll < 0.25:
        return {KEYS[int(rand() * len(KEYS))]: a_value(rand, depth + 1)}
    if roll < 0.6:
        return NASTY_STRINGS[int(rand() * len(NASTY_STRINGS))]
    return SCALARS[int(rand() * len(SCALARS))]


def a_body(rand):
    roll = rand()
    if roll < 0.08:
        return json.dumps(a_value(rand))          # not an object at the top
    if roll < 0.12:
        return '{"preferences": {' + 'x' * int(rand() * 50)   # truncated
    payload = {
        KEYS[int(rand() * len(KEYS))]: a_value(rand)
        for _ in range(int(rand() * 8))
    }
    body = {'preferences': payload}
    if rand() < 0.3:
        body['weights_wizard_offered'] = a_value(rand)
    return json.dumps(body)


def client_for(sub):
    identity, _ = Identity.objects.get_or_create(issuer='urn:local', sub=sub)
    token, _ = Token.objects.get_or_create(user=identity)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return api


def nested(depth):
    body = '"leaf"'
    for _ in range(depth):
        body = '[' + body + ']'
    return '{"preferences": {"a": ' + body + '}}'


@pytest.mark.django_db
@pytest.mark.parametrize('seed', [20260927, 11, 4711, 900001])
def test_no_generated_body_crashes_the_preferences_route(seed):
    rand = lcg(seed)
    client = client_for('fuzz')

    for i in range(120):
        body = a_body(rand)
        response = client.post(PREFS, body, content_type='application/json')
        where = f'seed={seed} i={i} body={body[:200]!r}'

        # Never a 5xx, and never a 401 — a suite that authenticates wrongly
        # would pass this file while testing nothing.
        assert response.status_code < 500, where
        assert response.status_code != 401, where
        if response.status_code >= 400:
            assert not TrialSearchPreferences.objects.exists(), where
        else:
            TrialSearchPreferences.objects.all().delete()


@pytest.mark.django_db
@pytest.mark.parametrize('seed', [20260927, 4711])
def test_no_generated_body_crashes_the_erasure_route(seed, settings):
    # The route the last round found a 500 on, and the one the previous
    # version of this harness did not touch at all.
    settings.SERVICE_AUTH_TOKEN = 'svc-secret'
    rand = lcg(seed)
    service = APIClient()
    service.credentials(HTTP_AUTHORIZATION='Bearer svc-secret')

    for i in range(120):
        body = a_body(rand)
        response = service.post(FORGET, body, content_type='application/json')
        where = f'seed={seed} i={i} body={body[:200]!r}'

        assert response.status_code < 500, where
        # Same guard as the other route: a wrong credential would make every
        # case a 401 and the whole loop vacuous.
        assert response.status_code != 401, where

    # And one body shaped like the real call, so this route is exercised past
    # its 400s at least once — every generated body is refused, so without
    # this the loop never reaches `forget_identity` at all.
    good = service.post(
        FORGET, {'issuer': 'urn:local', 'sub': 'nobody-here'}, format='json',
    )
    assert good.status_code == 200
    assert good.data == {'found': False, 'removed': {}}


@pytest.mark.django_db
@pytest.mark.parametrize('depth', [3, 5000, 9960, 50000])
def test_nesting_around_the_parsers_ceiling_is_refused_not_fatal(depth):
    # `json.loads` recurses, and its ceiling sits near ten thousand levels —
    # about twenty kilobytes of body, far under Django's 2.5 MB limit. Kept
    # as explicit depths because a generator will not reliably reach them.
    client = client_for('fuzz')

    response = client.post(PREFS, nested(depth), content_type='application/json')

    assert response.status_code < 500, depth
