"""`PatientInfoSerializer` — what it must NOT copy out of the instance.

`PatientInfo` is a plain Python object and the serializer takes `vars()`
wholesale, so a `cached_property` that has been read leaves its result sitting
in the instance dict beside the patient's data. `attributes_service` caches a
service object and `geolocation` caches the patient itself; either one turns
the response into a 500 at render time, and only once something has touched
it — which is why the graph endpoint's 400 paths passed while its success path
did not.
"""
import pytest
from rest_framework.renderers import JSONRenderer

from trials.api.patient_info_serializers import PatientInfoSerializer
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.normalize import normalize_patient_info


def _patient():
    pi = PatientInfo(disease='multiple myeloma', patient_age=61)
    normalize_patient_info(pi)
    return pi


@pytest.mark.django_db
def test_the_output_is_json_serializable_after_the_caches_are_warm():
    pi = _patient()
    # What the matcher does on any real request.
    assert pi.attributes_service is not None
    assert pi.geolocation is not None

    data = PatientInfoSerializer(pi).data
    # The renderer DRF actually uses. `json.dumps(..., default=str)` was the
    # first version of this line and it cannot fail — `default=` stringifies
    # anything, including the service object whose presence is the bug.
    JSONRenderer().render(data)
    assert 'attributes_service' not in data
    assert 'geolocation' not in data


@pytest.mark.django_db
def test_the_patient_data_still_comes_through():
    """The exclusion is by mechanism, not a denylist of names — so it has to be
    shown that it excludes the caches and nothing else."""
    pi = _patient()
    assert pi.attributes_service is not None

    data = PatientInfoSerializer(pi).data
    assert data['disease'] == 'multiple myeloma'
    assert data['patient_age'] == 61
    assert 'preExistingConditionCategories' in data


@pytest.mark.django_db
def test_the_output_does_not_depend_on_what_has_been_read():
    """The exclusion drops derived data too — `mutation_genes` and its
    neighbours are `cached_property` as well, and they used to appear in the
    response whenever something had warmed them.

    That is the trade, and it is the right way round: a field that is present
    only when an unrelated code path happened to touch it is worse than one
    that is consistently absent. A client cannot depend on the first, and can
    at least see that the second is missing.
    """
    cold = PatientInfoSerializer(_patient()).data

    warm_patient = _patient()
    assert warm_patient.attributes_service is not None
    assert warm_patient.geolocation is not None
    warm = PatientInfoSerializer(warm_patient).data

    assert set(cold) == set(warm)
