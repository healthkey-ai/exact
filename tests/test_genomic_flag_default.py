"""Typed genomic matching is the default (#5674 / #5673 / #5672).

The typed envelopes are populated for the gene/variant path; marker envelopes
cover most but not every legacy requirement and fall back per trial. The
legacy symbol/code path remains available with an explicit
``EXACT_OMOP_GENOMICS=0`` for an incident rollback, but it is no longer the
default a silent environment gets.

The setting is read at import time, so the default is tested through the helper
with a controlled environment and a subprocess, not by asserting against
whatever variable this test process happens to carry.
"""
import os
import subprocess
import sys

import pytest

from exact.settings import _env_flag

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize('value,expected', [
    (None, True),      # unset -> the default is ON
    ('', False),       # an explicit empty value keeps the old opt-in spelling off
    ('0', False), ('false', False), ('no', False), ('off', False),
    ('1', True), ('true', True), ('yes', True), ('on', True),
    ('TrUe', True), ('YeS', True),
])
def test_the_flag_parser(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv('EXACT_OMOP_GENOMICS', raising=False)
    else:
        monkeypatch.setenv('EXACT_OMOP_GENOMICS', value)
    assert _env_flag('EXACT_OMOP_GENOMICS', True) is expected


def test_the_shipped_setting_uses_the_helper_and_defaults_on():
    """Read the module the way a deployment does: with the variable removed."""
    env = os.environ.copy()
    env.pop('EXACT_OMOP_GENOMICS', None)
    env.setdefault('SECRET_KEY', 'test-secret-key')
    env.setdefault('DEBUG', 'true')
    code = (
        "import os;"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE','exact.test_settings');"
        "import django; django.setup();"
        "from django.conf import settings;"
        "print('on' if settings.EXACT_OMOP_GENOMICS else 'off')"
    )
    output = subprocess.check_output(
        [sys.executable, '-c', code], cwd=ROOT, env=env, text=True,
    ).strip()
    assert output == 'on'
