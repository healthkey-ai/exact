"""eligible_for_min_max_value sane_range guard (ported from CB, cb#4559/#4561).

A stored threshold outside the sane band is treated as "no constraint" (self-heals
once re-normalized) instead of comparing on a wrong scale and wrongly excluding the
patient. Default sane_range=None is an exact no-op.
"""
import pytest

from trials.models import Trial
from trials.querysets.trial import TrialQuerySet
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.configs import USER_TO_TRIAL_ATTRS_MAPPING
from tests.factories import TrialFactory

_MIN, _MAX = 'white_blood_cell_count_min', 'white_blood_cell_count_max'


@pytest.mark.django_db
def test_orchestrator_forwards_sane_range_from_config(monkeypatch):
    """QR4a/L1 regression: `filter_by_patient_info` must forward the config's
    `sane_range` into `eligible_for_min_max_value`. The helper has accepted the
    kwarg since #282, but the orchestrator previously dropped it — so a stored
    out-of-band threshold would wrongly exclude the patient, and queryset search
    would disagree with the detailed matcher / explain. CB's retained orchestrator
    forwards it; this pins the package to match. (No package config carries a
    sane_range value yet — ported in QR4b — so we inject one and prove the wiring.)
    """
    monkeypatch.setitem(
        USER_TO_TRIAL_ATTRS_MAPPING['white_blood_cell_count'], 'sane_range', (0, 1_000_000)
    )
    TrialFactory(white_blood_cell_count_min=5000)
    pi = PatientInfo(white_blood_cell_count=3000, white_blood_cell_count_units='cells/L')

    seen = []
    orig = TrialQuerySet.eligible_for_min_max_value

    def spy(self, attr_min_name, attr_max_name, value, **kwargs):
        seen.append((attr_min_name, kwargs.get('sane_range')))
        return orig(self, attr_min_name, attr_max_name, value, **kwargs)

    monkeypatch.setattr(TrialQuerySet, 'eligible_for_min_max_value', spy)
    Trial.objects.filter_by_patient_info(pi)

    wbc_calls = [sr for (name, sr) in seen if name == _MIN]
    assert wbc_calls, 'white_blood_cell_count min_max branch never called eligible_for_min_max_value'
    assert (0, 1_000_000) in wbc_calls, (
        f'orchestrator dropped sane_range; forwarded values: {wbc_calls}'
    )


@pytest.mark.django_db
def test_out_of_band_min_threshold_is_no_constraint():
    t_out = TrialFactory(white_blood_cell_count_min=2_000_000_000)  # not yet in canonical units
    t_in = TrialFactory(white_blood_cell_count_min=5000)            # in-band
    value = 3000  # below the in-band min -> would exclude t_in
    qs = Trial.objects.filter(pk__in=[t_out.pk, t_in.pk]).eligible_for_min_max_value(
        _MIN, _MAX, value, sane_range=(0, 1_000_000))
    ids = set(qs.values_list('pk', flat=True))
    assert t_out.pk in ids       # out-of-band threshold -> guard fires -> kept
    assert t_in.pk not in ids    # in-band, value < min -> genuinely excluded


@pytest.mark.django_db
def test_no_sane_range_excludes_out_of_band_threshold():
    t_out = TrialFactory(white_blood_cell_count_min=2_000_000_000)
    qs = Trial.objects.filter(pk=t_out.pk).eligible_for_min_max_value(_MIN, _MAX, 3000)  # no guard
    assert t_out.pk not in set(qs.values_list('pk', flat=True))  # excluded on the wrong scale


@pytest.mark.django_db
def test_in_band_threshold_matches_normally():
    t_ok = TrialFactory(white_blood_cell_count_min=1000)  # value >= min -> eligible
    qs = Trial.objects.filter(pk=t_ok.pk).eligible_for_min_max_value(
        _MIN, _MAX, 3000, sane_range=(0, 1_000_000))
    assert t_ok.pk in set(qs.values_list('pk', flat=True))
