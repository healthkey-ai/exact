"""No JSON body, of any shape, may produce a 5xx.

The property, and the reason it is a property rather than three more cases:
every fix in this app's first two review rounds was "here is an input you did
not think of", and the depth cap that was supposed to close the last one did
not — it ran after the parser that was crashing. A rule stated over generated
bodies catches the next spelling; a list of examples catches the last one.

Depths are chosen around Python's recursion ceiling, which `json.loads` hits
at roughly ten thousand levels from a body of about twenty kilobytes — well
inside Django's 2.5 MB limit, and therefore reachable by anyone with a token
at the throttle's three hundred requests a minute.
"""
import json

import pytest
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from accounts.models import Identity
from user_state.models import TrialSearchPreferences

PREFS = '/user-state/trial-search-preferences/'


@pytest.fixture
def client(db):
    identity, _ = Identity.objects.get_or_create(issuer='urn:local', sub='fuzz')
    token, _ = Token.objects.get_or_create(user=identity)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
    return api


def nested(depth):
    body = '"leaf"'
    for _ in range(depth):
        body = '[' + body + ']'
    return '{"preferences": {"a": ' + body + '}}'


BODIES = [
    ('empty object', '{}'),
    ('no preferences key', '{"weights_wizard_offered": true}'),
    ('null preferences', '{"preferences": null}'),
    ('a list', '{"preferences": ["phase"]}'),
    ('a number', '{"preferences": 7}'),
    ('a string', '{"preferences": "phase"}'),
    ('truncated', '{"preferences": {'),
    ('not json at all', 'phase=II'),
    ('empty body', ''),
    ('a bare array', '[1, 2, 3]'),
    ('unicode keys', json.dumps({'preferences': {'фаза': 'II', '🧬': 'x'}})),
    ('very wide', json.dumps({'preferences': {f'k{i}': i for i in range(5000)}})),
    ('nested 3', nested(3)),
    ('nested 5000', nested(5000)),
    # Above Python's recursion ceiling: the case the depth cap never saw,
    # because the parser died before the serializer ran.
    ('nested 9960', nested(9960)),
    ('nested 50000', nested(50000)),
]


@pytest.mark.django_db
@pytest.mark.parametrize('name,body', BODIES, ids=[n for n, _ in BODIES])
def test_no_body_produces_a_server_error(client, name, body):
    response = client.post(PREFS, body, content_type='application/json')

    assert response.status_code < 500, f'{name} -> {response.status_code}'
    # And a refused body leaves nothing behind, whichever way it was refused.
    if response.status_code >= 400:
        assert not TrialSearchPreferences.objects.exists(), name
