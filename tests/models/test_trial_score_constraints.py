import pytest

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from trials.constants import TRIAL_SCORE_MAX
from trials.models import Trial
from tests.factories import TrialFactory


SCORE_FIELDS = ['benefit_score', 'patient_burden_score', 'risk_score']


class TestTrialScoreConstraints:
    @pytest.mark.django_db
    def test_in_range_scores_persist(self):
        t = TrialFactory(benefit_score=0, patient_burden_score=TRIAL_SCORE_MAX, risk_score=10)
        t.refresh_from_db()
        assert (t.benefit_score, t.patient_burden_score, t.risk_score) == (0, TRIAL_SCORE_MAX, 10)

    @pytest.mark.django_db
    def test_null_scores_allowed(self):
        t = TrialFactory(benefit_score=None, patient_burden_score=None, risk_score=None)
        assert t.pk is not None

    @pytest.mark.django_db
    @pytest.mark.parametrize('field', SCORE_FIELDS)
    def test_above_max_rejected_by_db_constraint(self, field):
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                TrialFactory(**{field: TRIAL_SCORE_MAX + 1})

    @pytest.mark.parametrize('field', SCORE_FIELDS)
    def test_above_max_rejected_by_field_validator(self, field):
        field_obj = Trial._meta.get_field(field)
        field_obj.run_validators(TRIAL_SCORE_MAX)  # boundary value is valid
        with pytest.raises(ValidationError):
            field_obj.run_validators(TRIAL_SCORE_MAX + 1)
