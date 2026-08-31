"""Unit tests for get_user_therapy_release_id() (promop#394, #286 Gate 1).

The getter is the strict, fail-closed normalizer for the patient's aggregate
therapy-vocab release: only a non-empty, bounded-length ASCII-digit string is a
real release; everything else (absent / None / bool / float / non-digit /
over-length) → None (unknown → the patient-release gate fails closed once
enforced). It must NEVER int()-coerce (bool/float would slip through).
"""
import pytest

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes

pytestmark = pytest.mark.django_db


def _attr(**kw):
    return PatientInfoAttributes(PatientInfo(**kw))


def test_absent_returns_none():
    assert _attr().get_user_therapy_release_id() is None


def test_none_value_returns_none():
    assert _attr(therapy_release_id=None).get_user_therapy_release_id() is None


def test_valid_decimal_string_returned_verbatim():
    assert _attr(therapy_release_id='7').get_user_therapy_release_id() == '7'


def test_multi_digit_release_preserved():
    assert _attr(therapy_release_id='12345').get_user_therapy_release_id() == '12345'


def test_empty_string_returns_none():
    assert _attr(therapy_release_id='').get_user_therapy_release_id() is None


def test_non_digit_string_returns_none():
    assert _attr(therapy_release_id='7a').get_user_therapy_release_id() is None
    assert _attr(therapy_release_id='rel-7').get_user_therapy_release_id() is None


def test_bool_returns_none():
    # int(True) == 1 would be a fail-open; the getter rejects a non-str.
    assert _attr(therapy_release_id=True).get_user_therapy_release_id() is None


def test_int_returns_none():
    # Only a canonical decimal STRING is accepted; a bare int is not the wire shape.
    assert _attr(therapy_release_id=7).get_user_therapy_release_id() is None


def test_float_returns_none():
    # int(7.0)==7 would be a fail-open; a float is not a str → rejected.
    assert _attr(therapy_release_id=7.0).get_user_therapy_release_id() is None


def test_whitespace_padded_returns_none():
    assert _attr(therapy_release_id=' 7').get_user_therapy_release_id() is None
    assert _attr(therapy_release_id='7 ').get_user_therapy_release_id() is None


def test_non_ascii_digit_returns_none():
    # str.isdigit() accepts superscript/Arabic-Indic digits; isascii() rejects them.
    assert _attr(therapy_release_id='²').get_user_therapy_release_id() is None  # ²


def test_over_length_returns_none():
    assert _attr(therapy_release_id='1' * 33).get_user_therapy_release_id() is None
