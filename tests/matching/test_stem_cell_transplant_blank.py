"""An empty SCT history is unanswered, not "no transplant" (#5435).

CB's `PatientInfoAttributes.is_attr_blank` gained this rule in #5435: `[]` and
`''` mean the patient was not asked, while `['None']` is an explicit "no SCT"
answer. The packaged copy only special-cased the other JSON/list attributes, so
the SQL orchestrator applied the SCT filter with an empty value and the matcher
read it as answered. This pins the shared primitive before the queryset drain.
"""
import pytest

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes


def _attributes(**fields):
    return PatientInfoAttributes(PatientInfo(disease='multiple myeloma', **fields))


@pytest.mark.parametrize('value, blank', [
    ([], True),
    ('', True),
    (None, True),
    # An explicit no-SCT answer is an answer.
    (['None'], False),
    (['Autologous'], False),
])
def test_stem_cell_transplant_history_blankness(value, blank):
    assert _attributes(stem_cell_transplant_history=value).is_attr_blank(
        'stem_cell_transplant_history') is blank


@pytest.mark.parametrize('attribute', [
    'genetic_mutations', 'supportive_therapies', 'later_therapies',
])
@pytest.mark.parametrize('value', [[], ''])
def test_the_other_list_valued_attributes_keep_the_same_empty_rule(attribute, value):
    assert _attributes(**{attribute: value}).is_attr_blank(attribute) is True
