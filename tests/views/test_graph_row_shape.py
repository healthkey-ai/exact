"""What `/trials-graph/` hands its client for each eligibility row.

The endpoint rebuilds every row from a whitelist rather than passing the
detail row through, so a key added upstream reaches this consumer only if
it is named here — and the one that matters is the patient attribute's
snake_case name (EXACT #421): without it the client is left deriving
the name by hand — and `p53_ihc` camelizes to `p53Ihc`, which a standard
snake-caser turns back into `p_53_ihc`. PROMOP answers a name derived
wrongly with 200 and no change.
"""
from trials.api.graph_view import _normalize_item_for_ui


class TestGraphRowShape:
    def test_it_carries_the_patient_record_field(self):
        row = _normalize_item_for_ui({
            'name': 'meetsCRAB',
            'ufield': 'meetsCRAB',
            'upatientField': 'meets_crab',
            'label': 'Meets CRAB',
            'value': True,
            'uvalue': None,
            'ureadonly': True,
        })
        assert row['patientField'] == 'meetsCRAB'
        assert row['patientFieldCanonical'] == 'meets_crab'
        # And the subform signal, for the same reason: this endpoint
        # rebuilds the row, so without naming it the client here is handed
        # an address to write to and nothing about how the value is edited.
        assert row['patientFieldHasSubform'] is True

    def test_it_carries_the_subform_signal_both_ways(self):
        # Only the True case was asserted, so `True` as a literal passed —
        # and that is the worse failure: it would mark every field as
        # behind a subform and hide every ordinary control.
        editable = _normalize_item_for_ui({
            'name': 'hemoglobinLevelMin', 'ufield': 'hemoglobinLevel',
            'upatientField': 'hemoglobin_level', 'label': 'Hb',
            'value': 5, 'uvalue': None, 'ureadonly': False,
        })
        assert editable['patientFieldHasSubform'] is False

    def test_an_unsaid_subform_signal_stays_unsaid(self):
        # `bool(...)` would turn "this builder did not say" into "editable",
        # which is the wrong direction to fail in.
        row = _normalize_item_for_ui({'name': 'x', 'ufield': 'f', 'label': 'X'})
        assert row['patientFieldHasSubform'] is None

    def test_it_does_not_invent_one_when_upstream_sent_nothing(self):
        # An older upstream, or a row built by a path that has not been
        # stamped: the key must be absent-as-None, not derived here.
        row = _normalize_item_for_ui({'name': 'x', 'ufield': 'someField', 'label': 'X'})
        assert row['patientFieldCanonical'] is None

    def test_it_does_not_claim_a_subform_where_there_is_no_field(self):
        """`therapies()` hardcodes `ureadonly: True` on every therapy row and
        puts the TRIAL attribute in `ufield`, so the canonical name comes back
        null while the subform flag says true.

        A client reading that flag to decide whether to offer a subform
        affordance offers one for a row with no patient field to write —
        which is the silent no-op the canonical name exists to prevent,
        reached through the key beside it.
        """
        row = _normalize_item_for_ui({
            'name': 'therapiesRequired',
            'ufield': 'therapiesRequired',
            'upatientField': None,
            'label': 'Therapies',
            'value': ['vrd'],
            'uvalue': None,
            'ureadonly': True,
        })
        assert row['patientFieldCanonical'] is None
        assert row['patientFieldHasSubform'] is None
