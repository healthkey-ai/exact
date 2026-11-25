"""Under the OMOP profile, therapy-type rows carry concept_ids, not codes.

`TrialAttributes.therapies()` used to hand the legacy category-code option map
to a row whose value is `omop_therapy_types_*` — a concept_id. `formatValue`
matches the whole stored value against the option list, so the row rendered
`35807214` instead of `Anthracycline`.

The trial options are display-only for these rows (`ureadonly=True`, and the
editor reads `uoptions`, which stay `None`), so resolving them through the
release-pinned mirror cannot leak a trial vocabulary into a patient write.
"""
import pytest
from django.test import override_settings

from tests.factories import TrialFactory
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.trial_details.trial_attributes import TrialAttributes


RELEASE_ID = 4
THEC = 35807214          # HemOnc "Anthracycline"
MISSING = 4114692        # no mirror row in this fixture


@pytest.fixture
def mirrored_type(db):
    from vocab_mirror.models import MirrorConcept, MirrorRelease

    MirrorRelease.objects.create(release_id=RELEASE_ID, state=MirrorRelease.ACTIVE)
    MirrorConcept.objects.create(
        release_id=RELEASE_ID, concept_id=THEC, concept_name='Anthracycline',
        domain_id='Drug', vocabulary_id='HemOnc', concept_class_id='Component Class',
        concept_code=str(THEC),
    )


@override_settings(EXACT_OMOP_THERAPY=True)
def test_required_and_excluded_type_rows_show_titles(mirrored_type):
    trial = TrialFactory(
        disease='Multiple Myeloma',
        omop_therapy_types_required=[THEC],
        omop_therapy_types_excluded=[MISSING],
    )

    rows = TrialAttributes(trial, PatientInfo(disease='multiple myeloma')).therapies({})

    assert rows['therapyTypesRequired']['options'] == [
        {'value': str(THEC), 'label': 'Anthracycline'},
    ]
    # An unresolved id stays visible and names itself; it is not dropped and no
    # title is invented for it.
    assert rows['therapyTypesExcluded']['options'] == [
        {'value': str(MISSING), 'label': f'Unknown concept ({MISSING})'},
    ]


@override_settings(EXACT_OMOP_THERAPY=False)
def test_legacy_profile_keeps_the_category_option_map(db):
    trial = TrialFactory(
        disease='Multiple Myeloma',
        therapy_types_required=['chemotherapy_(anthracycline)'],
    )

    rows = TrialAttributes(trial, PatientInfo(disease='multiple myeloma')).therapies({})

    # Legacy values are internal category codes, so their options stay the
    # category vocabulary rather than becoming concept ids.
    assert rows['therapyTypesRequired']['options']
    values = {option['value'] for option in rows['therapyTypesRequired']['options']}
    assert 'chemotherapy_(anthracycline)' in values
    assert str(THEC) not in values
