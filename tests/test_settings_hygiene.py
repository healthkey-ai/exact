"""Test settings must not inherit the developer's PROMOP credentials (#448).

`exact.settings` reads every PROMOP_* value from the environment and from
BASE_DIR/.env — the file `.env.example` tells a developer to write. Without
neutralization those values reach any test that builds a client without passing
every field explicitly, and an assertion about what EXACT sends quietly becomes
an assertion about whose machine ran it. Measured before this was fixed: three
failures in the vocab-client tests and nine in the patient client's, on a
machine with a working local promop.

`exact.test_settings` blanks them. This test exists because that list is
hand-maintained: a new PROMOP_* setting added to `exact.settings` would
otherwise be silently un-neutralized, and nothing would fail until someone's
shell made it fail.
"""
import exact.settings as prod_settings
import exact.test_settings as test_settings


def _promop_names(module):
    return {name for name in dir(module) if name.startswith('PROMOP_')}


def test_every_promop_setting_is_neutralized_for_tests():
    missing = _promop_names(prod_settings) - _promop_names(test_settings)
    assert not missing, (
        f'PROMOP settings not neutralized in exact/test_settings.py: '
        f'{sorted(missing)}. Add them there (blank), or a developer with these '
        f'configured locally will get different test results from CI.'
    )


def test_neutralized_values_are_blank():
    """Blank, not a copy of the production default: a pinned value silently
    stops tracking `exact.settings` the moment that default changes."""
    non_blank = {
        name: getattr(test_settings, name)
        for name in _promop_names(test_settings)
        if getattr(test_settings, name) != ''
    }
    assert not non_blank, f'expected blank PROMOP test settings, got {non_blank}'
