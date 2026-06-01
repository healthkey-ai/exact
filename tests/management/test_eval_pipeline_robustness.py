"""
Regression tests for #47 — the eval pipeline must fail loudly (non-zero exit)
instead of silently exiting 0 when the patient DB fetch fails or every patient
errors. A silent exit-0 previously left trials4patients.sh proceeding to a
missing-output FileNotFoundError downstream.
"""
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

_CMD = 'trials.management.commands.search_trials_for_patients.Command'


@pytest.mark.django_db
class TestEvalPipelineFailsLoudly:
    def test_db_fetch_failure_raises(self):
        # _fetch_via_db returns None on any fetch failure (bad URL, psql missing,
        # non-zero psql exit). handle() must turn that into a non-zero exit.
        with patch(f'{_CMD}._fetch_via_db', return_value=None):
            with pytest.raises(CommandError, match='Patient DB fetch failed'):
                call_command('search_trials_for_patients', person_ids='1')

    def test_all_patients_failing_raises(self):
        # Rows fetched, but every per-patient match raises (e.g. trials DB down).
        # A run that produced zero results must exit non-zero, not write an empty
        # output file and report success.
        rows = [{'person_id': 1, 'disease': 'multiple myeloma'}]
        with patch(f'{_CMD}._fetch_via_db', return_value=rows), \
                patch(f'{_CMD}._search_trials_direct', side_effect=RuntimeError('boom')):
            with pytest.raises(CommandError, match='no results produced'):
                call_command('search_trials_for_patients', person_ids='1')
