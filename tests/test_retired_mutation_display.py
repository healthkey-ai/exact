"""#5297: retired mutation values stay visible on the payload EXACT renders."""
import pytest

from trials.services.genetic_mutation_catalog import (
    V422DEL_CODE,
    V422DEL_TITLE,
    label_for_stored_code,
)
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.promop_adapter import normalize_promop_row
from trials.services.trial_details.trial_attributes import TrialAttributes
from tests.factories import TrialFactory

RETIRED_CODE = 'c.472g_t_(r158l)'
RETIRED_LABEL = 'c.472G>T (R158L)'


def test_corrected_v422del_label_normalizes_to_the_stored_code():
    row = normalize_promop_row({
        'genetic_mutations': [{'gene': 'ESR1', 'mutation': V422DEL_TITLE}],
    })

    assert row['genetic_mutations'][0]['variant'] == V422DEL_CODE


def test_lowercase_corrected_v422del_label_uses_the_stored_code():
    row = normalize_promop_row({
        'genetic_mutations': [
            {'gene': 'ESR1', 'mutation': 'c.1265_1267del (v422del)'},
        ],
    })

    assert row['genetic_mutations'][0]['variant'] == V422DEL_CODE


def test_ordinary_variants_normalize_exactly_as_before():
    row = normalize_promop_row({
        'genetic_mutations': [
            {'gene': 'TP53', 'mutation': 'c.524G>A (R175H)'},
            {'gene': 'PIK3CA', 'variant': 'c.3140A>G (p.H1047R)'},
        ],
    })

    assert [m['variant'] for m in row['genetic_mutations']] == [
        'c.524g_a_(r175h)',
        'c.3140a_g_(p.h1047r)',
    ]


def test_stored_retired_code_has_a_label():
    assert label_for_stored_code(RETIRED_CODE) == RETIRED_LABEL


@pytest.mark.django_db
def test_retired_variant_keeps_its_label_in_the_user_value():
    trial = TrialFactory()
    attrs = TrialAttributes(trial, PatientInfo(disease='breast cancer'))

    assert attrs.get_uvalue(
        'ugeneticMutations', [RETIRED_CODE],
    ) == [RETIRED_LABEL]


@pytest.mark.django_db
def test_retired_variant_is_added_only_to_the_row_that_holds_it():
    trial = TrialFactory()
    patient = PatientInfo(
        disease='breast cancer',
        genetic_mutations=[
            {'gene': 'tp53', 'variant': RETIRED_CODE,
             'origin': None, 'interpretation': 'vus'},
        ],
    )
    attrs = TrialAttributes(trial, patient)

    rows = attrs.get_genetic_mutations()
    options = rows[0]['variant']['options']
    assert {'value': RETIRED_CODE, 'label': RETIRED_LABEL} in options
    # It is not offered in the global list for the gene.
    assert {'value': RETIRED_CODE, 'label': RETIRED_LABEL} not in (
        attrs.all_options['geneticMutationAllVariants']['options']['tp53']
    )


@pytest.mark.django_db
def test_trial_requirement_keeps_a_label_for_a_retired_code():
    trial = TrialFactory(mutation_variants_required=[RETIRED_CODE])
    attrs = TrialAttributes(trial, PatientInfo(disease='breast cancer'))

    options = attrs.get_options('mutationVariantsRequired')

    assert {'value': RETIRED_CODE, 'label': RETIRED_LABEL} in options
