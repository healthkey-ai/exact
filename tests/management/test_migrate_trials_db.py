"""Unit tests for migrate_trials_db management command."""
from unittest.mock import MagicMock, call, patch

import pytest
from django.db.utils import ProgrammingError

from trials.management.commands.migrate_trials_db import Command


def _make_migration(name):
    m = MagicMock()
    m.name = name
    return m


@pytest.fixture
def cmd():
    c = Command()
    c.stdout = MagicMock()
    c.stdout.write = MagicMock()
    return c


@patch('trials.management.commands.migrate_trials_db.connections')
@patch('trials.management.commands.migrate_trials_db.MigrationExecutor')
@patch('trials.management.commands.migrate_trials_db.call_command')
@patch('django.conf.settings.TRIALS_DB_MIGRATE', True, create=True)
@patch('django.conf.settings.DATABASES', {'trials': {}}, create=True)
def test_applies_clean_migrations(mock_call, mock_executor_cls, mock_conns, cmd):
    """All-clean plan: every migration runs for real."""
    plan = [(_make_migration('0012_omopconcept'), False)]
    mock_executor_cls.return_value.migration_plan.return_value = plan

    cmd.handle()

    mock_call.assert_called_once_with(
        'migrate', 'trials', '0012_omopconcept', database='trials', interactive=False, verbosity=0,
    )


@patch('trials.management.commands.migrate_trials_db.connections')
@patch('trials.management.commands.migrate_trials_db.MigrationExecutor')
@patch('trials.management.commands.migrate_trials_db.call_command')
@patch('django.conf.settings.TRIALS_DB_MIGRATE', True, create=True)
@patch('django.conf.settings.DATABASES', {'trials': {}}, create=True)
def test_fakes_duplicate_table(mock_call, mock_executor_cls, mock_conns, cmd):
    """Migration that raises DuplicateTable is faked instead of crashing."""
    plan = [(_make_migration('0008_highriskmclcriteria'), False)]
    mock_executor_cls.return_value.migration_plan.return_value = plan
    mock_call.side_effect = [
        ProgrammingError('relation "trials_highriskmclcriteria" already exists'),
        None,  # fake call succeeds
    ]

    cmd.handle()

    assert mock_call.call_count == 2
    _, fake_call = mock_call.call_args_list
    assert fake_call == call(
        'migrate', 'trials', '0008_highriskmclcriteria',
        database='trials', fake=True, interactive=False, verbosity=0,
    )


@patch('trials.management.commands.migrate_trials_db.connections')
@patch('trials.management.commands.migrate_trials_db.MigrationExecutor')
@patch('trials.management.commands.migrate_trials_db.call_command')
@patch('django.conf.settings.TRIALS_DB_MIGRATE', True, create=True)
@patch('django.conf.settings.DATABASES', {'trials': {}}, create=True)
def test_reraises_unexpected_error(mock_call, mock_executor_cls, mock_conns, cmd):
    """Non-duplicate ProgrammingErrors propagate as usual."""
    plan = [(_make_migration('0009_something'), False)]
    mock_executor_cls.return_value.migration_plan.return_value = plan
    mock_call.side_effect = ProgrammingError('syntax error at or near "INVALID"')

    with pytest.raises(ProgrammingError, match='syntax error'):
        cmd.handle()


@patch('django.conf.settings.TRIALS_DB_MIGRATE', False, create=True)
def test_disabled_flag_is_noop(cmd):
    """Returns early when TRIALS_DB_MIGRATE is False."""
    cmd.handle()
    cmd.stdout.write.assert_called_once()
    assert 'disabled' in cmd.stdout.write.call_args[0][0]
