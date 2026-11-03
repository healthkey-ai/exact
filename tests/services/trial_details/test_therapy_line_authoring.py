"""The line a patient's own history claims is authorable from the subform.

`upatientRecomputed` answers "can this value be replaced behind the reader?".
For the therapy-line fields the answer depends on the patient: normalization
clears a line's fields when `prior_therapy` says the patient is not in that
line. Offering a pencil under "no prior therapy" would promise an edit the
payload then hides, and offering none to a patient in 3L+ would hide a line
they are already on and can now author (PROMOP writes it as an Episode).
"""
import pytest

from trials.services.patient_info.normalize import (
    LINE_FIELDS_BY_PRIOR_THERAPY, normalize_patient_info, therapy_line_is_authorable,
)
from trials.services.trial_details.trial_attributes import TrialAttributes
from tests.factories import TrialFactory

pytestmark = pytest.mark.django_db

FIRST = {'first_line_therapy', 'first_line_date', 'first_line_outcome'}
SECOND = {'second_line_therapy', 'second_line_date', 'second_line_outcome'}
LATER = {'later_therapy', 'later_date', 'later_outcome'}


def _entries(patient_info, prior_therapy):
    patient_info.prior_therapy = prior_therapy
    normalize_patient_info(patient_info)
    groups = TrialAttributes(TrialFactory(), patient_info).get_user_subform_attrs()
    return {entry['upatientField']: entry
            for group in groups.values() for entry in group}


@pytest.mark.parametrize('prior_therapy,offered', [
    # The group already lists only the lines a patient claims
    # (`get_treatment_attrs`), so the flag has to agree with it rather than
    # with the condition text, which is written for every patient.
    ('One line', FIRST),
    ('Two lines', FIRST | SECOND),
    ('More than two lines of therapy', FIRST | SECOND | LATER),
])
def test_the_line_the_patient_claims_is_not_recomputed(patient_info, prior_therapy, offered):
    entries = _entries(patient_info, prior_therapy)

    assert offered <= set(entries)
    for field in offered:
        assert entries[field]['upatientRecomputed'] is False, field
        assert entries[field]['upatientRecomputedWhen'] is None, field


def test_no_prior_therapy_offers_no_line_at_all(patient_info):
    entries = _entries(patient_info, 'None')

    assert not (FIRST | SECOND | LATER) & set(entries)


def test_blank_prior_therapy_lists_the_lines_but_keeps_them_recomputed(patient_info):
    """The group builder's fallback lists every line, but normalization clears
    them: the control must not be offered for a value the payload discards."""
    entries = _entries(patient_info, '')

    for field in FIRST | SECOND | LATER:
        assert entries[field]['upatientRecomputed'] is True, field
        assert entries[field]['upatientRecomputedWhen'] is not None, field


def test_only_the_authored_parts_are_released(patient_info):
    """The rest of the line's columns stay derivation's: concept ids, the
    per-line list, end dates. A pencil on those would write nothing."""
    entries = _entries(patient_info, 'More than two lines of therapy')

    for field in ('later_therapies', 'later_start_date', 'later_end_date',
                  'first_line_component_ids'):
        if field in entries:
            assert entries[field]['upatientRecomputed'] is True, field


def test_the_rule_releases_only_the_claimed_line_and_its_authored_parts():
    assert set(LINE_FIELDS_BY_PRIOR_THERAPY) == {'first_line_', 'second_line_', 'later_'}
    assert therapy_line_is_authorable('later_therapy', 'More than two lines of therapy')
    assert not therapy_line_is_authorable('later_therapy', 'Two lines')
    assert not therapy_line_is_authorable('later_therapy_ids', 'More than two lines of therapy')
    assert not therapy_line_is_authorable('later_start_date', 'More than two lines of therapy')
    assert not therapy_line_is_authorable('first_line_component_ids', 'One line')
    assert not therapy_line_is_authorable('weight', 'More than two lines of therapy')
