"""Provenance for the negated rows, captured at the raw boundary (#5667).

`uvalue: null` cannot distinguish "the patient answered unknown" from "the
positive field was never in the context": the adapter pops the positive key
and the in-memory builder strips nulls, and after those two steps both cases
have the same shape. The negated row therefore carries the canonical answer
with an explicit `available` flag, recorded before either step runs.

The row is navigation and evidence only — the client decides whether to open
an editor from it. A row without provenance carries no `negative_source`, and
the client's fail-closed default is to offer none.
"""
import pytest

from tests.factories import TrialFactory

NEGATED_ROWS = {
    'noHivRequired', 'noHepatitisBRequired', 'noHepatitisCRequired',
}


def stalled_ok(stamped):
    """Only the three negated rows may be stamped — nothing else."""
    return stamped and stamped <= NEGATED_ROWS and 'noHivRequired' in stamped
from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.promop_adapter import (
    build_patient_info_from_promop_row,
    capture_negated_sources,
)
from trials.services.patient_info.resolve import _build_in_memory
from trials.services.trial_details.trial_attributes import TrialAttributes


class TestCapture:
    def test_an_explicit_null_is_available_and_keeps_its_null_value(self):
        entry = capture_negated_sources({'hiv_status': None})['hiv_status']

        assert entry == {
            'upatientField': 'hiv_status',
            'type': 'boolean',
            'available': True,
            'value': None,
        }

    def test_a_missing_key_is_unavailable_and_carries_no_value_key(self):
        entry = capture_negated_sources({})['hiv_status']

        assert entry == {
            'upatientField': 'hiv_status',
            'type': 'boolean',
            'available': False,
        }
        assert 'value' not in entry, (
            'an absent key must not look like an explicit null; that is the '
            'difference the row exists to preserve'
        )

    def test_a_negated_key_alone_does_not_prove_the_positive_was_present(self):
        entry = capture_negated_sources({'no_hiv_status': True})['hiv_status']

        assert entry['available'] is False

    def test_the_value_is_the_raw_answer_not_the_inverted_display(self):
        # The row shows the inverse; provenance records what the patient said.
        entry = capture_negated_sources({'hiv_status': False})['hiv_status']

        assert entry['value'] is False


@pytest.mark.django_db
class TestTheAdapterCapturesBeforeThePop:
    def test_an_explicit_null_survives_normalization(self):
        pi = build_patient_info_from_promop_row({
            'disease': 'multiple myeloma', 'hiv_status': None,
        })

        assert pi._negated_sources['hiv_status']['available'] is True
        assert pi._negated_sources['hiv_status']['value'] is None

    def test_a_missing_field_stays_unavailable(self):
        pi = build_patient_info_from_promop_row({'disease': 'multiple myeloma'})

        assert pi._negated_sources['hiv_status']['available'] is False

    def test_the_inline_spelling_is_understood(self):
        # The federated host posts camelCase; capture happens after the
        # snake_case pass so the same key is found.
        pi = _build_in_memory(
            {'disease': 'multiple myeloma', 'hivStatus': True}, strict=True,
        )

        assert pi._negated_sources['hiv_status'] == {
            'upatientField': 'hiv_status',
            'type': 'boolean',
            'available': True,
            'value': True,
        }


@pytest.mark.django_db
class TestTheRowCarriesIt:
    def _row(self, patient_info):
        trial = TrialFactory(disease='multiple myeloma', no_hiv_required=True)
        rows = TrialAttributes(trial, patient_info).details()
        return next(
            row for row in rows.values()
            if row.get('upatientField') == 'no_hiv_status'
        )

    def test_a_negated_row_names_the_canonical_field_and_its_availability(self):
        pi = build_patient_info_from_promop_row({
            'disease': 'multiple myeloma', 'hiv_status': None,
        })

        row = self._row(pi)

        assert row['negative_source'] == {
            'upatientField': 'hiv_status',
            'type': 'boolean',
            'available': True,
            'value': None,
        }

    def test_it_is_a_separate_key_not_a_subform(self):
        pi = build_patient_info_from_promop_row({
            'disease': 'multiple myeloma', 'hiv_status': False,
        })

        row = self._row(pi)

        assert 'negative_source' in row
        assert 'subform_details' not in row, (
            'an old client reads `subform_details` as a computed subform and '
            'makes the row read-only; provenance must not ride on that key'
        )

    def test_a_patient_built_without_provenance_carries_no_source(self):
        row = self._row(PatientInfo(disease='multiple myeloma'))

        assert 'negative_source' not in row, (
            'fail closed: a client that finds no source opens no editor'
        )

    def test_only_the_negated_rows_are_stamped(self):
        pi = build_patient_info_from_promop_row({
            'disease': 'multiple myeloma', 'hiv_status': True,
        })
        trial = TrialFactory(disease='multiple myeloma', no_hiv_required=True)
        rows = TrialAttributes(trial, pi).details()

        stamped = {name for name, row in rows.items()
                   if isinstance(row, dict) and 'negative_source' in row}
        assert stalled_ok(stamped), stamped

    @pytest.mark.parametrize('negated,positive,required_attr', [
        ('no_hiv_status', 'hiv_status', 'no_hiv_required'),
        ('no_hepatitis_b_status', 'hepatitis_b_status',
         'no_hepatitis_b_required'),
        ('no_hepatitis_c_status', 'hepatitis_c_status',
         'no_hepatitis_c_required'),
    ])
    def test_every_viral_pair_is_covered(self, negated, positive, required_attr):
        pi = build_patient_info_from_promop_row({
            'disease': 'multiple myeloma', positive: None,
        })
        trial = TrialFactory(
            disease='multiple myeloma', **{required_attr: True},
        )
        row = next(
            row for row in TrialAttributes(trial, pi).details().values()
            if row.get('upatientField') == negated
        )

        assert row['negative_source']['upatientField'] == positive
        assert row['negative_source']['available'] is True
