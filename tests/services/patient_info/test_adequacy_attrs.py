"""
Computed adequacy attributes (port from CB #4390 hepatic, #4392 haematological).

Mirror CB's thresholds:
- hepatic: bilirubin total <= 1.5 ULN, AST <= 2.5 ULN, ALT <= 2.5 ULN.
- haematological: ANC >= 1500/uL, platelet >= 100 (10^9/L), hemoglobin >= 9 g/dL.
Missing inputs resolve to False (NO) for both.
"""
import pytest

from trials.services.patient_info.patient_info import PatientInfo
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
