"""Under the OMOP profile, regimen and component rows carry concept_ids, not codes.

`TrialAttributes.therapies()` switched the row's `value` to the `omop_*` column
(so the matcher and the reader see the same fact) but kept the legacy code→label
option map. `formatValue` matches the stored value against `options`, found
nothing, and the detail rendered `4090378, 44507848` under "Therapies" — the
same defect #672 fixed for therapy *types* only.

The row is display-only (`ureadonly=True`; the editor reads `uoptions`, which
stay `None`), so resolving ids to titles here cannot leak a trial vocabulary into
a patient write.

The trial's own vocabulary is the first source, because it holds both the id the
backfill wrote and the title the legacy map used — no mirror release is required
to cover the concept. The pinned mirror stays the fallback, and an id neither
source knows remains visible as "Unknown concept (<id>)" rather than being
dropped or given an invented title.

`trials_*` models live on a second database that pytest-django does not wrap in
the per-test transaction, so a vocabulary row created here is deleted again in
teardown: otherwise every later test in the session sees an extra therapy in its
option map.
"""
import pytest
from django.test import override_settings

from tests.factories import TrialFactory
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.trial_details.trial_attributes import TrialAttributes


CLINICAL_TRIALS = 4090378
RITUXIMAB = 44507848
#: No vocabulary row and no mirror row in this fixture.
UNKNOWN = 4114692


def _rows(trial, disease='mantle cell lymphoma'):
    return TrialAttributes(trial, PatientInfo(disease=disease)).therapies({})


@pytest.fixture
def clinical_trials_therapy(db):
    """A regimen the backfill mapped to `CLINICAL_TRIALS`."""
    from trials.models import Therapy

    therapy, created = Therapy.objects.get_or_create(
        code='test_clinical_trials',
        defaults={'title': 'Test Clinical Trials', 'omop_concept_id': CLINICAL_TRIALS},
    )
    yield 'test_clinical_trials'
    # Only the row this fixture made: the shared trials database is not rolled
    # back by pytest-django, and deleting a catalog row would be a real loss.
    if created:
        therapy.delete()


@pytest.fixture
def rituximab_component(db):
    from trials.models import TherapyComponent

    component, created = TherapyComponent.objects.get_or_create(
        code='test_rituximab',
        defaults={'title': 'Test Rituximab', 'omop_concept_id': RITUXIMAB},
    )
    yield 'test_rituximab'
    if created:
        component.delete()


@override_settings(EXACT_OMOP_THERAPY=True)
@pytest.mark.django_db
def test_required_regimen_row_shows_the_vocabulary_title(clinical_trials_therapy):
    trial = TrialFactory(
        disease='Mantle Cell Lymphoma',
        omop_therapies_required=[CLINICAL_TRIALS, UNKNOWN],
    )

    row = _rows(trial)['therapiesRequired']

    assert row['options'] == [
        {'value': str(CLINICAL_TRIALS), 'label': 'Test Clinical Trials'},
        {'value': str(UNKNOWN), 'label': f'Unknown concept ({UNKNOWN})'},
    ]
    # Order follows the stored value, so the reader sees the criteria in the
    # order the trial states them.
    assert row['value'] == [CLINICAL_TRIALS, UNKNOWN]
    # A display map, not an editor: the write path reads `uoptions`.
    assert row['ureadonly'] is True
    assert row['uoptions'] is None


@override_settings(EXACT_OMOP_THERAPY=True)
@pytest.mark.django_db
def test_excluded_regimen_row_and_component_row_resolve_in_their_own_vocabulary(
        clinical_trials_therapy, rituximab_component):
    trial = TrialFactory(
        disease='Mantle Cell Lymphoma',
        omop_therapies_excluded=[CLINICAL_TRIALS],
        omop_therapy_components_required=[RITUXIMAB],
    )

    rows = _rows(trial)

    assert rows['therapiesExcluded']['options'] == [
        {'value': str(CLINICAL_TRIALS), 'label': 'Test Clinical Trials'},
    ]
    assert rows['therapyComponentsRequired']['options'] == [
        {'value': str(RITUXIMAB), 'label': 'Test Rituximab'},
    ]


@override_settings(EXACT_OMOP_THERAPY=True)
@pytest.mark.django_db
def test_the_trial_vocabulary_wins_over_the_mirror(clinical_trials_therapy):
    """Both sources know the id; the trial's own reviewed title is the answer."""
    from vocab_mirror.models import MirrorConcept, MirrorRelease

    MirrorRelease.objects.create(release_id=4, state=MirrorRelease.ACTIVE)
    MirrorConcept.objects.create(
        release_id=4, concept_id=CLINICAL_TRIALS, concept_name='Clinical Trials (OMOP)',
        domain_id='Drug', vocabulary_id='HemOnc', concept_class_id='Regimen',
        concept_code=str(CLINICAL_TRIALS),
    )
    trial = TrialFactory(
        disease='Mantle Cell Lymphoma',
        omop_therapies_required=[CLINICAL_TRIALS],
    )

    assert _rows(trial)['therapiesRequired']['options'] == [
        {'value': str(CLINICAL_TRIALS), 'label': 'Test Clinical Trials'},
    ]


@override_settings(EXACT_OMOP_THERAPY=True)
@pytest.mark.django_db
def test_an_id_the_vocabulary_lost_is_labelled_by_the_pinned_mirror():
    """A concept the vocabulary row no longer carries still renders a title."""
    from vocab_mirror.models import MirrorConcept, MirrorRelease

    MirrorRelease.objects.create(release_id=4, state=MirrorRelease.ACTIVE)
    MirrorConcept.objects.create(
        release_id=4, concept_id=UNKNOWN, concept_name='Ibrutinib',
        domain_id='Drug', vocabulary_id='HemOnc', concept_class_id='Ingredient',
        concept_code=str(UNKNOWN),
    )
    trial = TrialFactory(
        disease='Mantle Cell Lymphoma',
        omop_therapies_required=[UNKNOWN],
    )

    assert _rows(trial)['therapiesRequired']['options'] == [
        {'value': str(UNKNOWN), 'label': 'Ibrutinib'},
    ]


@override_settings(EXACT_OMOP_THERAPY=False)
@pytest.mark.django_db
def test_legacy_profile_keeps_the_code_option_map(clinical_trials_therapy):
    trial = TrialFactory(
        disease='Multiple Myeloma',
        therapies_required=['clinical_trials'],
    )

    row = _rows(trial, disease='multiple myeloma')['therapiesRequired']

    # The row keeps the legacy code, and the option map stays in code space so
    # the reader can resolve it — the OMOP ids are the matcher's, not the page's.
    assert row['value'] == ['clinical_trials']
    assert {'value': 'clinical_trials', 'label': 'Clinical Trials'} in row['options']
    assert not any(
        str(option['label']).startswith('Unknown concept') for option in row['options'])
