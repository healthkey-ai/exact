"""
Computed adequacy attributes (port from CB #4390 hepatic, #4392 haematological).

Mirror CB's thresholds:
- hepatic: bilirubin total <= 1.5 ULN, AST <= 2.5 ULN, ALT <= 2.5 ULN.
- haematological: ANC >= 1500/uL, platelet >= 100 (10^9/L), hemoglobin >= 9 g/dL.
Missing inputs resolve to False (NO) for both.
"""
import pytest

from trials.services.patient_info.patient_info import PatientInfo
from trials.services.patient_info.resolve import _build_in_memory, _coerce_numerics
from trials.services.patient_info.patient_info_attributes import PatientInfoAttributes

pytestmark = pytest.mark.django_db


class TestHepaticAdequacyStatus:
    # ULN norms for caucasian_or_european / M: AST=40, ALT=45, BiliT=1.2.
    _common = dict(ethnicity='caucasian_or_european', gender='M')

    def _status(self, **kwargs):
        pi = PatientInfo(**{**self._common, **kwargs})
        return PatientInfoAttributes(pi).hepatic_adequacy_status

    def test_all_within_thresholds_is_true(self):
        # AST 80/40=2.0, ALT 90/45=2.0, bili 1.2/1.2=1.0
        assert self._status(liver_enzyme_levels_ast=80, liver_enzyme_levels_alt=90,
                            serum_bilirubin_level_total='1.2') is True

    def test_exactly_at_thresholds_is_true(self):
        # AST 100/40=2.5, ALT 112/45=2.49, bili 1.8/1.2=1.5
        assert self._status(liver_enzyme_levels_ast=100, liver_enzyme_levels_alt=112,
                            serum_bilirubin_level_total='1.8') is True

    def test_ast_above_threshold_is_false(self):
        assert self._status(liver_enzyme_levels_ast=120, liver_enzyme_levels_alt=90,
                            serum_bilirubin_level_total='1.2') is False

    def test_alt_above_threshold_is_false(self):
        assert self._status(liver_enzyme_levels_ast=80, liver_enzyme_levels_alt=150,
                            serum_bilirubin_level_total='1.2') is False

    def test_bilirubin_above_threshold_is_false(self):
        assert self._status(liver_enzyme_levels_ast=80, liver_enzyme_levels_alt=90,
                            serum_bilirubin_level_total='2.4') is False

    def test_missing_lab_value_is_false(self):
        assert self._status(liver_enzyme_levels_ast=None, liver_enzyme_levels_alt=90,
                            serum_bilirubin_level_total='1.2') is False

    def test_missing_ethnicity_gender_is_false(self):
        # ULN not computable without the demographic norms.
        pi = PatientInfo(liver_enzyme_levels_ast=80, liver_enzyme_levels_alt=90,
                         serum_bilirubin_level_total='1.2', ethnicity='', gender='')
        assert PatientInfoAttributes(pi).hepatic_adequacy_status is False


class TestHaematologicalAdequacyStatus:
    def _status(self, **kwargs):
        return PatientInfoAttributes(PatientInfo(**kwargs)).haematological_adequacy_status

    def test_all_at_thresholds_is_true(self):
        assert self._status(absolute_neutrophile_count=1500, platelet_count=100,
                            hemoglobin_level=9) is True

    def test_above_thresholds_is_true(self):
        assert self._status(absolute_neutrophile_count=3000, platelet_count=250,
                            hemoglobin_level=13) is True

    def test_low_anc_is_false(self):
        assert self._status(absolute_neutrophile_count=1499, platelet_count=100,
                            hemoglobin_level=9) is False

    def test_low_platelet_is_false(self):
        assert self._status(absolute_neutrophile_count=1500, platelet_count=99,
                            hemoglobin_level=9) is False

    def test_low_hemoglobin_is_false(self):
        assert self._status(absolute_neutrophile_count=1500, platelet_count=100,
                            hemoglobin_level=8.9) is False

    def test_missing_value_is_false(self):
        assert self._status(absolute_neutrophile_count=1500, platelet_count=None,
                            hemoglobin_level=9) is False


class TestRenalAdequacyDecidesOnAValue(object):
    """`renal_adequacy_status` used to be answered before the eGFR it reads (#502).

    `normalize` copies this status out and derives
    `estimated_glomerular_filtration_rate` one line BELOW that. So for every
    patient whose eGFR is calculated rather than stated, the field was still
    empty at the moment the status was decided, the derivation took its
    "inputs missing" branch and returned False.

    False here is not one unmet criterion among many: `match_score_and_status()`
    short-circuits the whole trial to `not_eligible` and the SQL prefilter drops
    it. A patient with healthy kidneys was removed from trials, indistinguishably
    from one at half the threshold.

    The fix computes the value where it is USED (`_screening_egfr`) rather than
    reordering two lines in `normalize`, so the ordering cannot come apart again.
    """

    # serum creatinine 0.8 / M / 20y derives an eGFR of 129.93; 3.0 / 68y
    # derives 21.94. Both are checked below rather than assumed.
    PANEL = dict(disease='multiple myeloma', gender='M', patient_age=20,
                 weight=70, serum_creatinine_level=0.8)
    IMPAIRED = dict(disease='multiple myeloma', gender='M', patient_age=68,
                    weight=70, serum_creatinine_level=3.0)

    def test_a_derived_egfr_decides_the_screen(self):
        """The defect itself. Fails on the base branch."""
        pi = _build_in_memory(dict(self.PANEL, creatinine_clearance_rate=110))
        assert pi.estimated_glomerular_filtration_rate == 129.93, (
            'fixture assumes the eGFR is DERIVED, which is the whole point'
        )
        assert pi.renal_adequacy_status is True

    def test_a_derived_impairment_still_reads_as_inadequate(self):
        """Regression guard only.

        Stated plainly because it matters when reading this file: this one
        PASSES on the base branch, where the same False came out of the
        "inputs missing" branch instead. It pins the answer, not the fix.
        """
        pi = _build_in_memory(dict(self.IMPAIRED, creatinine_clearance_rate=110))
        assert pi.estimated_glomerular_filtration_rate == 21.94
        assert pi.renal_adequacy_status is False

    def test_the_worse_of_two_disagreeing_readings_decides(self):
        """A caller echoing back an old derived eGFR must not outrank fresh labs.

        EXACT publishes its own derived eGFR, so a client that reads a record
        and sends it back carries EXACT's arithmetic on the wire — the same
        hazard `_tp53_contradiction` documents. Here the stale 129.93 arrives
        beside a creatinine of 3.0 that implies 21.94.

        The base branch published the RECALCULATED 21.94 in the field and
        `renal_adequacy_status: True` beside it, because the status was read
        before the recalculation landed. Screening on the worse reading removes
        that contradiction.
        """
        pi = _build_in_memory(dict(self.IMPAIRED,
                                   estimated_glomerular_filtration_rate=129.93,
                                   creatinine_clearance_rate=110))
        assert pi.estimated_glomerular_filtration_rate == 21.94
        assert pi.renal_adequacy_status is False

    def test_a_supplied_reading_is_not_published_differently_than_before(self):
        """The published field is deliberately left exactly as the base branch.

        Every numeric criterion (`estimated_glomerular_filtration_rate_min/max`)
        reads that field, so changing what lands in it would move trials for
        reasons this issue is not about. Only the screen moved.
        """
        pi = _build_in_memory(dict(self.PANEL,
                                   estimated_glomerular_filtration_rate=40,
                                   creatinine_clearance_rate=110))
        assert pi.estimated_glomerular_filtration_rate == 129.93
        assert pi.renal_adequacy_status is False

    @pytest.mark.parametrize('payload,which', [
        ({'estimated_glomerular_filtration_rate': 0,
          'creatinine_clearance_rate': 110}, 'eGFR'),
        ({'estimated_glomerular_filtration_rate': 90,
          'creatinine_clearance_rate': 0}, 'clearance'),
    ])
    def test_zero_is_a_reading_not_an_absence(self, payload, which):
        """Truthiness skipped the check for the worst value either can carry.

        `if egfr and egfr < 60` is False when egfr is 0, so the branch never
        fired and — the other measure being adequate — the answer fell through
        to True: a trial offered to a patient with effectively no renal
        function. Both halves were already live on the base branch.
        """
        assert _build_in_memory(dict(payload)).renal_adequacy_status is False, (
            'a {} of 0 was read as no reading at all'.format(which)
        )

    @pytest.mark.parametrize('field', [
        'creatinine_clearance_rate',
        'estimated_glomerular_filtration_rate',
    ])
    def test_an_empty_string_is_an_absent_reading_not_a_crash(self, field):
        """`_coerce_numerics` passed '' through, leaving a str in a numeric column.

        The first comparison against a threshold then raises TypeError from
        inside request resolution — the search fails and EVERY trial is hidden.

        The two halves are not equal evidence, so: the CLEARANCE case fails on
        the base branch, where `meets_crab_r_renal_insufficiency` compares it
        and raises. The eGFR case PASSES there, because truthiness skipped the
        comparison — it fails only once that check becomes `is not None`. It is
        a guard on this commit's own change, not a demonstration of a defect
        that predates it.
        """
        payload = dict(self.PANEL, creatinine_clearance_rate=110)
        payload[field] = ''

        pi = _build_in_memory(payload)     # the assertion is that this returns

        assert not isinstance(getattr(pi, field), str), (
            'an empty reading stayed a str in a numeric column'
        )
        assert pi.renal_adequacy_status in (True, False)

    def test_a_decimal_string_reading_survives_and_screens(self):
        """`int("40.5")` raises, and the reading was being discarded as None.

        This module's docstring says CB sends decimal strings ("10.20"), so
        dropping one throws away a measurement the caller stated — and for eGFR
        an absent supplied value hands the screen to the derived 129.93 instead.

        Screened on the coerced record DIRECTLY rather than through
        `_build_in_memory`: `normalize` republishes the derived value into the
        field (deliberately unchanged here), so an attributes object built after
        it no longer sees what the caller sent. Asserting through the full path
        would pass on the base branch too — False is reachable there by the
        `egfr is None` branch — and prove nothing.
        """
        data = dict(self.PANEL, estimated_glomerular_filtration_rate='40.5',
                    creatinine_clearance_rate=110)
        _coerce_numerics(data, PatientInfo)

        assert data['estimated_glomerular_filtration_rate'] == 40.5, (
            'a stated reading was discarded or truncated instead of coerced'
        )

        attrs = PatientInfoAttributes(PatientInfo(**data))
        assert attrs._screening_egfr == 40.5, 'the derived value screened instead'
        assert attrs.renal_adequacy_status is False


class TestNumericCoercionIsSharedByEveryField(object):
    """`_coerce_numerics` serves all 54 numeric columns, not just the eGFR (#510).

    The eGFR motivated the change; these pin what it must NOT do to the rest.
    """

    def test_a_decimal_reading_is_not_truncated_to_an_integer_column(self):
        """Truncating would make "990.9" and 990.9 mean different things.

        The free light chain pair is the sharp case: floored to 990/9 the ratio
        is 110, against a true 99.59 — across the SLiM threshold of 100. That
        republishes a SMOLDERING patient as active myeloma, which drives
        `disease_progression_active_required` in the matcher: offered
        active-disease trials, rejected from the smoldering ones they belong in.

        `int()` alone (the base branch) discards both readings instead, which is
        a different defect and is why the fallback exists at all.
        """
        data = {'kappa_flc': '990.9', 'lambda_flc': '9.95'}
        _coerce_numerics(data, PatientInfo)

        assert (data['kappa_flc'], data['lambda_flc']) == (990.9, 9.95)

        quoted = _build_in_memory({'disease': 'multiple myeloma',
                                   'kappa_flc': '990.9', 'lambda_flc': '9.95'})
        unquoted = _build_in_memory({'disease': 'multiple myeloma',
                                     'kappa_flc': 990.9, 'lambda_flc': 9.95})
        assert quoted.meets_slim == unquoted.meets_slim, (
            'quoting a number changed whether the patient meets SLiM'
        )
        assert quoted.progression == unquoted.progression

    @pytest.mark.parametrize('field', [
        'hemoglobin_level',
        'creatinine_clearance_rate',
        'serum_calcium_level',
    ])
    def test_an_empty_reading_never_reaches_a_comparison(self, field):
        """'' in a numeric column raises at the first threshold comparison.

        `creatinine_clearance_rate` and `hemoglobin_level` both 500 on the base
        branch — via `meets_crab_r_renal_insufficiency` and `meets_crab_a_anemia`
        — which fails the whole search and hides every trial. The fix is in the
        shared coercion, so it is not a renal-only repair and is not tested as
        one.
        """
        pi = _build_in_memory({'disease': 'multiple myeloma', field: ''})

        assert getattr(pi, field) is None

    @pytest.mark.parametrize('value', [[], {}, b'40'])
    def test_a_non_numeric_reading_does_not_raise_out_of_normalization(self, value):
        """`_coerce_numerics` only touches strings, so these arrive intact.

        The truthiness checks absorbed the falsy ones by accident; `is not None`
        does not, and the comparison raises inside `normalize_patient_info` —
        400 on the inline path, 500 on the person_id path, every trial hidden
        either way. Same harm as the empty string, different door.

        Only the eGFR field is asserted. The same value in
        `creatinine_clearance_rate` raises earlier and independently, at the
        unguarded comparison in `meets_crab_r_renal_insufficiency` — that one
        raises on the base branch too, so it is pre-existing and not this
        commit's to fix (noted in #509).
        """
        pi = _build_in_memory({'disease': 'multiple myeloma',
                               'estimated_glomerular_filtration_rate': value,
                               'creatinine_clearance_rate': 110})

        assert pi.renal_adequacy_status in (True, False)

    @pytest.mark.parametrize('value', ['inf', 'Infinity', '-inf'])
    def test_an_infinite_reading_is_not_a_measurement(self, value):
        """`int("inf")` refused it; `float("inf")` does not.

        Infinity clears every ceiling criterion outright and reads as adequate
        renal function. The decimal fallback is what makes it parseable at all,
        so rejecting it belongs in the same place.
        """
        data = {'estimated_glomerular_filtration_rate': value}
        _coerce_numerics(data, PatientInfo)

        assert data['estimated_glomerular_filtration_rate'] is None

        pi = _build_in_memory({'disease': 'multiple myeloma',
                               'estimated_glomerular_filtration_rate': value,
                               'creatinine_clearance_rate': 110})
        assert pi.renal_adequacy_status is False

    def test_a_nan_creatinine_is_not_an_adequate_kidney(self):
        """`Decimal('nan')` is a valid Decimal, so it survives coercion.

        `EgfrCalculator` propagates it and every comparison against NaN is
        False, so the screen fell through to "adequate" for a patient whose
        creatinine is not a number at all.
        """
        pi = _build_in_memory({'disease': 'multiple myeloma',
                               'serum_creatinine_level': 'nan',
                               'gender': 'M', 'patient_age': 20,
                               'creatinine_clearance_rate': 110})

        assert pi.renal_adequacy_status is False

    def test_a_non_numeric_clearance_does_not_raise_at_the_screen(self):
        """The clearance side of the same guard.

        Asserted on the attributes object directly: through `_build_in_memory`
        this value raises earlier, in `meets_crab_r_renal_insufficiency`, which
        raises on the base branch too and is not this commit's to fix. The screen
        itself must still answer.
        """
        attrs = PatientInfoAttributes(PatientInfo(creatinine_clearance_rate=[],
                                                  estimated_glomerular_filtration_rate=90))

        assert attrs.renal_adequacy_status is False
