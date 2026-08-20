"""#4832: a NULL jsonb list column means "no list constraint", same as '[]'.

The SQL potential count compared jsonb columns with `col = '[]'::jsonb`, which is
NULL (not TRUE) when the column is NULL, so the ELSE (potential) branch fired and
over-counted the trial as potential — while `potential_attrs_for_trial` (skips
NULL) and the matcher (empty list = matched) both treated it as no constraint.
This was the whole `potential->eligible` divergence class (concomitant_medications
with a NULL excluded list).
"""
import pytest

from trials.models import Trial
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.matching import status_equivalence as se
from tests.factories import TrialFactory


@pytest.mark.django_db
def test_null_jsonb_excluded_not_counted_potential():
    """A trial with a NULL concomitant excluded list is not potential for a
    blank patient — SQL count agrees with the matcher (no divergence)."""
    trial = TrialFactory(
        disease='multiple myeloma',
        concomitant_medications_excluded=None,  # NULL, not '[]'
    )
    pi = PatientInfo(disease='multiple myeloma', patient_age=65)
    base = Trial.objects.filter(id=trial.id)

    annotated = base.with_potential_attrs_count(pi).values('potential_attrs_count').first()
    assert annotated['potential_attrs_count'] == 0, (
        f"NULL jsonb list column inflated potential_attrs_count to "
        f"{annotated['potential_attrs_count']}"
    )
    assert se.compare(base, pi) == []


@pytest.mark.django_db
def test_nonempty_jsonb_excluded_still_counted_potential():
    """Guard: a genuinely non-empty excluded list on a blank patient is still
    potential — the NULL fix must not swallow real list constraints."""
    trial = TrialFactory(
        disease='multiple myeloma',
        concomitant_medications_excluded=['some_drug_code'],
    )
    pi = PatientInfo(disease='multiple myeloma', patient_age=65)
    base = Trial.objects.filter(id=trial.id)

    annotated = base.with_potential_attrs_count(pi).values('potential_attrs_count').first()
    assert annotated['potential_attrs_count'] >= 1, (
        "a real excluded list should still make a blank patient potential"
    )


@pytest.mark.django_db
def test_null_therapy_lists_reconcile():
    """NULL therapy columns (null=True) reconcile: SQL counts them empty and the
    matcher (null-guarded) returns matched, so no divergence and no crash."""
    trial = TrialFactory(
        disease='multiple myeloma',
        therapies_required=None, therapies_excluded=None,
        therapy_types_required=None, therapy_types_excluded=None,
        therapy_components_required=None, therapy_components_excluded=None,
    )
    pi = PatientInfo(disease='multiple myeloma', patient_age=65)
    base = Trial.objects.filter(id=trial.id)

    assert base.with_potential_attrs_count(pi).values(
        'potential_attrs_count').first()['potential_attrs_count'] == 0
    assert se.compare(base, pi) == []


@pytest.mark.django_db
def test_multicol_jsonb_mixed_required_set_excluded_null():
    """Multi-column jsonb attr with one column set and one NULL: the AND-of-OR
    grouping (the parenthesization fix) must keep it potential — a real required
    list still constrains even when the sibling excluded column is NULL."""
    trial = TrialFactory(
        disease='multiple myeloma',
        therapies_required=['some_therapy_code'], therapies_excluded=None,
        therapy_types_required=None, therapy_types_excluded=None,
        therapy_components_required=None, therapy_components_excluded=None,
    )
    pi = PatientInfo(disease='multiple myeloma', patient_age=65)
    base = Trial.objects.filter(id=trial.id)

    # required list is set -> the CASE must NOT collapse to empty -> potential.
    # This pins the parenthesization: with the OR groups AND-joined, a broken
    # (unparenthesized) build would let a NULL sibling column satisfy the whole
    # CASE and wrongly zero the count. A real required list must keep count >= 1.
    assert base.with_potential_attrs_count(pi).values(
        'potential_attrs_count').first()['potential_attrs_count'] >= 1
    # (No compare()==[] here: a synthetic therapy code isn't resolvable by the
    # matcher's therapy taxonomy, so the matcher verdict for a fake code is not a
    # reliable reconcile oracle — this test pins the SQL count/parens only.)


@pytest.mark.django_db
def test_pre_existing_excluded_null_reconcile():
    """The other real null=True divergence class: NULL pre_existing excluded list."""
    trial = TrialFactory(disease='multiple myeloma', pre_existing_conditions_excluded=None)
    pi = PatientInfo(disease='multiple myeloma', patient_age=65)
    base = Trial.objects.filter(id=trial.id)

    assert base.with_potential_attrs_count(pi).values(
        'potential_attrs_count').first()['potential_attrs_count'] == 0
    assert se.compare(base, pi) == []
