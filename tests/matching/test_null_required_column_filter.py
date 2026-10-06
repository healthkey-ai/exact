"""A NULL `*_required` column is no constraint — to the filter too (#383 follow-up).

cb#4832 / #383 gave the potential count and the matcher one reading of a NULL
jsonb list ("no constraint", same as `[]`), and left the hard filter with the
other: `Q(required__has_any_keys=values) | Q(required__exact=[])` matches neither
for NULL, so the trial was dropped from the list while the matcher called the
same patient eligible. The therapy list columns are `null=True`, so this is
reachable from data alone.
"""
import pytest

from trials.models import (
    TherapyComponentCategory, TherapyComponentCategoryConnection,
    TherapyComponentConnection, Trial,
)
from trials.services.matching import status_equivalence as se
from trials.services.patient_info.patient_info import PatientInfo
from tests.factories import TherapyComponentFactory, TherapyFactory, TrialFactory

NULLABLE_REQUIRED = [
    'therapies_required',
    'therapy_components_required',
    'therapy_types_required',
]


@pytest.fixture
def resolvable_therapy(db):
    """A regimen that expands all the way to a drug-class type.

    The category matters: without it `derive_component_and_type_values` yields
    `therapy_types == []`, the `elif therapy_types:` guard skips
    `eligible_for_therapy_types` entirely, and the `therapy_types_required`
    parametrization below asserts a column the filter never looks at.
    """
    therapy = TherapyFactory()
    component = TherapyComponentFactory()
    TherapyComponentConnection.objects.create(therapy=therapy, component=component)
    # get_or_create: the seeded test vocab already carries the real categories.
    category, _ = TherapyComponentCategory.objects.get_or_create(
        code='chemotherapy_(alkylating_agent)',
        defaults={'title': 'Chemotherapy (alkylating agent)'})
    TherapyComponentCategoryConnection.objects.create(category=category, component=component)
    return therapy


@pytest.mark.django_db
@pytest.mark.parametrize('null_column', NULLABLE_REQUIRED)
def test_a_null_required_column_does_not_drop_the_trial(null_column, resolvable_therapy):
    trial = TrialFactory(disease='multiple myeloma')
    Trial.objects.filter(id=trial.id).update(**{null_column: None})
    trial.refresh_from_db()
    patient = PatientInfo(disease='multiple myeloma', patient_age=65,
                          first_line_therapy=resolvable_therapy.code)
    base = Trial.objects.filter(id=trial.id)

    assert se.sql_status_map(base, patient) == {trial.id: 'eligible'}
    assert se.compare(base, patient) == []


@pytest.mark.django_db
@pytest.mark.parametrize('null_column', NULLABLE_REQUIRED)
def test_an_empty_required_column_is_unchanged(null_column, resolvable_therapy):
    """Guard: `[]` already meant no constraint and must keep meaning it."""
    trial = TrialFactory(disease='multiple myeloma', **{null_column: []})
    patient = PatientInfo(disease='multiple myeloma', patient_age=65,
                          first_line_therapy=resolvable_therapy.code)
    base = Trial.objects.filter(id=trial.id)

    assert se.sql_status_map(base, patient) == {trial.id: 'eligible'}


@pytest.mark.django_db
def test_a_populated_required_column_still_excludes(resolvable_therapy):
    """Guard against over-firing: a real requirement the patient cannot meet still
    drops the trial."""
    trial = TrialFactory(disease='multiple myeloma', therapies_required=['krd'])
    patient = PatientInfo(disease='multiple myeloma', patient_age=65,
                          first_line_therapy=resolvable_therapy.code)
    base = Trial.objects.filter(id=trial.id)

    assert se.sql_status_map(base, patient) == {trial.id: 'not_eligible'}
    assert se.compare(base, patient) == []


@pytest.mark.django_db
@pytest.mark.parametrize('null_column', NULLABLE_REQUIRED)
def test_a_treatment_naive_patient_keeps_a_null_required_trial(null_column):
    """The `has_no_prior_therapy` branch of the same filter carried the same bug.

    A patient who has had no therapy at all is a coherent, common state, and the
    branch kept only trials whose three `*_required` columns are `[]` — missing
    NULL exactly as the sibling filter did, so the trial vanished from the list
    while the matcher (which coalesces) called the patient eligible.
    """
    trial = TrialFactory(disease='multiple myeloma')
    Trial.objects.filter(id=trial.id).update(**{null_column: None})
    trial.refresh_from_db()
    patient = PatientInfo(disease='multiple myeloma', patient_age=65,
                          prior_therapy='None')
    base = Trial.objects.filter(id=trial.id)

    assert se.sql_status_map(base, patient) == {trial.id: 'eligible'}
    assert se.compare(base, patient) == []


@pytest.mark.django_db
def test_a_treatment_naive_patient_still_loses_a_required_trial():
    """Guard against over-firing on the same branch: a real requirement still
    excludes a patient who has had no therapy."""
    trial = TrialFactory(disease='multiple myeloma', therapies_required=['krd'])
    patient = PatientInfo(disease='multiple myeloma', patient_age=65,
                          prior_therapy='None')
    base = Trial.objects.filter(id=trial.id)

    assert se.sql_status_map(base, patient) == {trial.id: 'not_eligible'}
