"""OMOP cutover: CTOMOP adapter therapy mapping respects EXACT_OMOP_THERAPY.

The adapter is the ingest boundary (Phase 3c: EXACT does no patient-side crosswalk).
- Flag OFF (legacy): therapy display text -> EXACT internal code via resolve_therapy_code.
- Flag ON (OMOP): therapy fields come straight from CTOMOP's *_therapy_id concept_ids
  (the consumer supplies concepts); resolve_therapy_code is NOT used; a line with no
  concept_id becomes None (unknown).
"""
from unittest.mock import patch

import pytest
from django.test import override_settings

from trials.services.patient_info.ctomop_adapter import normalize_ctomop_row

pytestmark = pytest.mark.django_db


def _row(**kw):
    base = {'disease': 'multiple myeloma'}
    base.update(kw)
    return base


@override_settings(EXACT_OMOP_THERAPY=False)
def test_flag_off_resolves_display_text_to_internal_code():
    with patch(
        'trials.services.patient_info.ctomop_adapter.resolve_therapy_code',
        return_value='resolved_code',
    ) as m:
        r = normalize_ctomop_row(_row(
            first_line_therapy='RVd (Lenalidomide/Bortezomib/Dexamethasone)',
            first_line_therapy_id=35806260,  # ignored when flag off
        ))
    assert r['first_line_therapy'] == 'resolved_code'
    m.assert_called_with('RVd (Lenalidomide/Bortezomib/Dexamethasone)')


@override_settings(EXACT_OMOP_THERAPY=True)
def test_flag_on_uses_concept_ids_not_resolver():
    with patch(
        'trials.services.patient_info.ctomop_adapter.resolve_therapy_code',
    ) as m:
        r = normalize_ctomop_row(_row(
            first_line_therapy='RVd (Lenalidomide/Bortezomib/Dexamethasone)',
            first_line_therapy_id=35806260,
            second_line_therapy='Daratumumab',
            second_line_therapy_id=35806063,
            later_therapy='Pomalidomide',  # no concept_id -> unknown
        ))
    m.assert_not_called()
    assert r['first_line_therapy'] == '35806260'   # concept_id, as string
    assert r['second_line_therapy'] == '35806063'
    assert r['later_therapy'] is None              # no *_therapy_id present


@override_settings(EXACT_OMOP_THERAPY=True)
def test_flag_on_missing_concept_id_is_none():
    r = normalize_ctomop_row(_row(
        first_line_therapy='Some Free Text',  # text present, but no concept_id
        first_line_therapy_id='0',            # sentinel-empty -> None
    ))
    assert r['first_line_therapy'] is None


@override_settings(EXACT_OMOP_THERAPY=True)
def test_flag_on_remaps_later_therapies_from_concept_ids():
    # later_therapies feeds the same overlap as the scalar lines, so under OMOP it
    # must carry concept_ids (from later_therapy_ids), not raw codes.
    r = normalize_ctomop_row(_row(
        later_therapy_ids=[35806261, 35806262, 0],  # 0 dropped
        later_therapies=[{'therapy': 'pomalidomide'}],  # raw codes ignored under OMOP
    ))
    assert r['later_therapies'] == [{'therapy': '35806261'}, {'therapy': '35806262'}]


@override_settings(EXACT_OMOP_THERAPY=False)
def test_flag_off_leaves_later_therapies_list_untouched():
    lst = [{'therapy': 'pomalidomide'}]
    r = normalize_ctomop_row(_row(later_therapies=list(lst)))
    assert r['later_therapies'] == lst  # legacy: not concept-remapped
