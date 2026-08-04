"""by_titles matches the NCT / study_id, not only title text (cb#3547).

Regression guard for the study_id clause dropped during the E1 extraction and
re-added so CB can drain its by_titles override (QR3).
"""
import pytest

from trials.models import Trial
from tests.factories import TrialFactory


@pytest.mark.django_db
def test_by_titles_matches_study_id():
    t = TrialFactory(study_id='NCT01234567', brief_title='Some Myeloma Trial')
    other = TrialFactory(study_id='NCT99999999', brief_title='Unrelated')
    ids = set(Trial.objects.filter(pk__in=[t.pk, other.pk]).by_titles('NCT01234567').values_list('pk', flat=True))
    assert t.pk in ids and other.pk not in ids


@pytest.mark.django_db
def test_by_titles_still_matches_title_text():
    t = TrialFactory(study_id='NCT00000001', brief_title='Bortezomib Study')
    ids = set(Trial.objects.filter(pk=t.pk).by_titles('Bortezomib').values_list('pk', flat=True))
    assert t.pk in ids
