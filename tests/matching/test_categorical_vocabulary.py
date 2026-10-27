"""The categorical crosswalk must cover EXACT's own option lists (#5553).

The mappings live in the packaged surface as static tables so the package keeps
no `trials.*` import. This test is the other side of that trade-off: it reads
the option loaders EXACT actually ships and fails when a code is added, renamed
or removed without updating the crosswalk.
"""
import pytest

from exact_matching.patient_record import (
    histologic_type_code,
    menopausal_status_code,
    tnm_stage_code,
    tumor_grade_code,
)
from trials.models import DistantMetastasisStage, HistologicType, NodesStage, TumorStage
from trials.services.loaders.load_tnm_options import LoadTnmOptions
from trials.services.value_options import ValueOptions


@pytest.mark.django_db
def test_every_tnm_option_round_trips_from_its_code_and_its_title():
    LoadTnmOptions().load_all()

    for field, model in (
        ('tumor_stage', TumorStage),
        ('nodes_stage', NodesStage),
        ('distant_metastasis_stage', DistantMetastasisStage),
    ):
        rows = list(model.objects.values_list('code', 'title'))
        assert rows, f'{model.__name__} did not seed'

        for code, title in rows:
            # A CB-authored record stores the code; an imported FHIR fact stores
            # the loader's display title.
            assert tnm_stage_code(field, code) == code, (field, code)
            assert tnm_stage_code(field, title) == code, (field, title)


def test_every_menopausal_option_round_trips():
    options = ValueOptions().menopausal_status

    for code, title in options.items():
        if not code:
            continue
        assert menopausal_status_code(code) == code
        assert menopausal_status_code(title) == code, title


def test_every_fl_grade_option_round_trips():
    options = ValueOptions().tumor_grades

    for code, title in options.items():
        if not code:
            continue
        assert tumor_grade_code(code) == code
        assert tumor_grade_code(title) == code, title


@pytest.mark.django_db
def test_every_bc_histology_option_round_trips():
    rows = list(HistologicType.objects.values_list('code', 'title'))
    assert rows, 'HistologicType did not seed'

    for code, title in rows:
        assert histologic_type_code(code) == code
        assert histologic_type_code(title) == code, title
