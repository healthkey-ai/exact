import pytest
from django.test.client import Client

from trials.services.patient_info.patient_info import PatientInfo

from trials.services.loaders.load_bc_options import LoadBcOptions
from trials.services.loaders.load_concomitant_medications import LoadConcomitantMedications
from trials.services.loaders.load_ethnicity_options import LoadEthnicityOptions
from trials.services.loaders.load_genetic_mutations import LoadGeneticMutations
from trials.services.loaders.load_markers import LoadMarkers
from trials.services.loaders.load_mcl_options import LoadMclOptions
from trials.services.loaders.load_supportive_therapies import LoadSupportiveTherapies
from trials.services.loaders.load_therapies import LoadTherapies
from trials.services.loaders.load_toxicity_grade_options import LoadToxicityGradeOptions


@pytest.fixture(scope="session", autouse=True)
def django_db_setup(django_db_setup, django_db_blocker):
    """Seed static reference data once per test session."""
    with django_db_blocker.unblock():
        LoadConcomitantMedications().load_all()
        LoadEthnicityOptions().load_all()
        LoadMarkers().load_all()
        LoadTherapies().load_all()
        LoadSupportiveTherapies().load_all()
        LoadToxicityGradeOptions().load_all()
        LoadGeneticMutations().load_all(skip_genes_origins=False)
        LoadBcOptions().load_all(skip_hrd=False, skip_hr=False, skip_histologic_types=False)
        LoadMclOptions().load_all()


@pytest.fixture
def patient_info():
    return PatientInfo(disease='multiple myeloma')


@pytest.fixture
def api_client():
    return Client(content_type='application/json')


def patient_info_payload(**overrides):
    """Build a minimal patient_info JSON dict for stateless API calls."""
    base = {
        'disease': 'multiple myeloma',
        'gender': '',
        'patientAge': None,
    }
    base.update(overrides)
    return base
