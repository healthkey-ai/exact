"""
Tests for script-level robustness of `search_trials_for_patients`.

Guards three regressions that would cause `scripts/trials4patients.sh` to fail
silently or with a confusing downstream error:

- patient DB fetch failure → CommandError (non-zero exit, shell stops)
- trials DB reference-lookup failure → CommandError before the per-patient loop
- non-JSON psql stdout lines → logged and skipped, not crash the script
"""
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError


# ---------------------------------------------------------------------------
# Fixture: shared --source-db-url avoids the "no URL" branch in handle()
# ---------------------------------------------------------------------------

_SOURCE_DB_URL = 'postgresql://test:test@host:5432/patients'


@pytest.fixture
def mock_lookup_ok():
    """_build_code_lookup returns immediately — no real DB hit."""
    with patch('trials.management.commands.search_trials_for_patients._build_code_lookup', return_value={'_therapy': {}}):
        yield


# ---------------------------------------------------------------------------
# Fix 1 — _fetch_via_db returning None must raise CommandError
# ---------------------------------------------------------------------------

class TestFetchFailureExits:
    def test_psql_nonzero_returncode_raises_command_error(self, mock_lookup_ok):
        with patch('shutil.which', return_value='/usr/bin/psql'), \
             patch('subprocess.run', return_value=CompletedProcess([], 1, '', 'connection refused')):
            with pytest.raises(CommandError, match='Patient DB fetch failed'):
                call_command('search_trials_for_patients', f'--source-db-url={_SOURCE_DB_URL}')

    def test_psql_subprocess_exception_raises_command_error(self, mock_lookup_ok):
        with patch('shutil.which', return_value='/usr/bin/psql'), \
             patch('subprocess.run', side_effect=OSError('psql binary missing')):
            with pytest.raises(CommandError, match='Patient DB fetch failed'):
                call_command('search_trials_for_patients', f'--source-db-url={_SOURCE_DB_URL}')

    def test_psql_not_in_path_raises_command_error(self, mock_lookup_ok):
        with patch('shutil.which', return_value=None):
            with pytest.raises(CommandError, match='Patient DB fetch failed'):
                call_command('search_trials_for_patients', f'--source-db-url={_SOURCE_DB_URL}')


# ---------------------------------------------------------------------------
# Fix 3 — _build_code_lookup failure must raise CommandError before the loop
# ---------------------------------------------------------------------------

class TestReferenceLookupFailureExits:
    def test_lookup_db_error_raises_command_error_with_context(self):
        with patch(
            'trials.management.commands.search_trials_for_patients._build_code_lookup',
            side_effect=Exception('could not connect to trials DB'),
        ):
            with pytest.raises(CommandError, match='Trials DB reference lookup failed'):
                call_command('search_trials_for_patients', f'--source-db-url={_SOURCE_DB_URL}')

    def test_dry_run_skips_lookup_so_trials_db_outage_does_not_block_inspection(self):
        # --dry-run only inspects the raw row from CTOMOP; it must not trip the
        # trials DB lookup, so a sysadmin can debug patient data without trials DB.
        stdout = '{"person_id": 1, "disease": "multiple myeloma"}\n'
        with patch(
            'trials.management.commands.search_trials_for_patients._build_code_lookup',
            side_effect=Exception('trials DB offline'),
        ), patch('shutil.which', return_value='/usr/bin/psql'), \
             patch('subprocess.run', return_value=CompletedProcess([], 0, stdout, '')):
            # No exception expected — dry-run prints and returns
            call_command(
                'search_trials_for_patients',
                f'--source-db-url={_SOURCE_DB_URL}',
                '--dry-run',
            )


# ---------------------------------------------------------------------------
# Fix 2 — bad psql stdout lines are skipped with a warning, not crash
# ---------------------------------------------------------------------------

class TestPsqlStdoutToleratesNonJson:
    def test_non_json_lines_skipped_and_valid_json_returned(self, mock_lookup_ok, capsys):
        stdout = (
            'NOTICE: connection encrypted via libssl\n'
            '\n'
            '{"person_id": 1, "disease": null}\n'
            'garbage <not json> line\n'
            '{"person_id": 2, "disease": null}\n'
        )
        with patch('shutil.which', return_value='/usr/bin/psql'), \
             patch('subprocess.run', return_value=CompletedProcess([], 0, stdout, '')):
            # Both rows have disease=None so per-patient loop skips them; the
            # command should complete cleanly without exception.
            call_command('search_trials_for_patients', f'--source-db-url={_SOURCE_DB_URL}')

        captured = capsys.readouterr()
        # Warning surfaces to stderr — operator sees the skipped lines
        assert 'Skipped 2 non-JSON line' in captured.err
