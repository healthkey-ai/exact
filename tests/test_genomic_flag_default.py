"""Typed genomic matching is the default (#5674 / #5673 / #5672).

The typed envelopes are populated and the shadow comparisons are clean for the
mutation path; the marker slice has one reviewed NOTCH1/NOTCH2 broadening. The
legacy symbol/code path remains available with an explicit
``EXACT_OMOP_GENOMICS=0`` for an incident rollback, but it is no longer the
default a silent environment gets.
"""
from django.conf import settings

from exact_matching.omop.genomics import omop_genomics_enabled


def test_typed_genomics_is_on_by_default():
    assert settings.EXACT_OMOP_GENOMICS is True
    assert omop_genomics_enabled() is True
