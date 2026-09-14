"""A subform entry names the patient attribute it IS.

A composite row — TNBC status, CRAB, the ULN pair — carries no edit control,
because EXACT recomputes it from other values. The subform beside it holds
exactly those values, so it is the only route a reader has to change what the
row says. That only works if a client can tell which attribute each entry is.

The name in the payload is camelCase and cannot be turned back reliably
(`p53_ihc` camelises to `p53Ihc`, which a snake-caser returns as `p_53_ihc`),
so the entry carries the canonical name outright — taken from
`SUBFORM_ATTRS_MAPPING`, where it already is.
"""
import pytest

from trials.services.patient_info.normalize import RECOMPUTED_ATTRIBUTES, normalize_patient_info
from trials.services.trial_details.configs import SUBFORM_ATTRS_MAPPING
from trials.services.trial_details.trial_attributes import TrialAttributes
from tests.factories import *


def _groups(patient_info):
    pi = patient_info
    pi.disease = 'Breast Cancer'
    pi.estrogen_receptor_status = 'er_minus'
    pi.progesterone_receptor_status = 'pr_minus'
    normalize_patient_info(pi)
    # A therapy line too, because the therapy groups are the ONLY place a
    # recomputed entry appears: every statically listed subform input — the
    # receptor statuses, the labs, height and weight — is raw data EXACT
    # leaves alone, which is the point of a subform. `get_treatment_attrs`
    # is the exception, and it returns `first_line_therapy` and its siblings,
    # which `normalize.py` derives from the therapy lines. Without a prior
    # therapy set, that group is not built and the flag could be hardcoded
    # false without any assertion noticing.
    pi.prior_therapy = 'More than two lines of therapy'
    pi.first_line_therapy = 'vrd'
    normalize_patient_info(pi)
    trial = TrialFactory(
        disease='Breast Cancer', tnbc_status=True, therapies_required=['vrd'],
    )
    return TrialAttributes(trial, patient_info=pi).get_user_subform_attrs()


@pytest.mark.django_db
class TestSubformEntriesNameTheirField:
    def test_every_entry_carries_a_canonical_name(self, patient_info):
        groups = _groups(patient_info)
        assert groups, 'no subforms built — the fixture cannot exercise this'
        for group, entries in groups.items():
            for entry in entries:
                assert entry.get('upatientField'), (
                    f"{group} has an entry with no canonical name: {entry['name']}"
                )

    def test_the_name_is_the_one_the_mapping_asked_for(self, patient_info):
        # Not derived from `name`: the mapping is the source, which is what
        # keeps the two from drifting.
        groups = _groups(patient_info)
        for group, entries in groups.items():
            wanted = SUBFORM_ATTRS_MAPPING[group]
            if callable(wanted):
                wanted = wanted(patient_info)
            assert [e['upatientField'] for e in entries] == list(wanted)

    def test_an_entry_says_whether_a_write_to_it_survives(self, patient_info):
        # The subform is the writable half of a computed row — except where an
        # entry is itself computed, which is not rare: `creatinine_clearance_rate`
        # sits under CRAB and EXACT derives it.
        for entries in _groups(patient_info).values():
            for entry in entries:
                assert entry['upatientRecomputed'] == (
                    entry['upatientField'] in RECOMPUTED_ATTRIBUTES
                )

    def test_both_answers_are_reachable_from_this_fixture(self, patient_info):
        # Otherwise the assertion above is true of a set that only ever says
        # one thing, and would keep passing if the flag were hardcoded.
        flags = {
            e['upatientRecomputed']
            for entries in _groups(patient_info).values()
            for e in entries
        }
        assert flags == {True, False}
