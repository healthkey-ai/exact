"""The container init scripts must never read a FAILED probe as "database is empty".

`docker/init_patients_db.sh` and `init_trials_db.sh` DROP the public schema
before restoring a snapshot, so every probe that authorises that step has to
distinguish "the table is not there" from "I could not find out". #397 made the
first two probes fail closed and left the third — the one that checks whether
the table resolves through `search_path` outside `public` — discarding both its
exit status and its stderr. A transient failure there produced an empty string,
which read as "nothing outside public" and fell through to the DROP.

These tests run the real scripts with a stubbed `psql`, so they assert the
behaviour rather than the shape of the source.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = {
    'patients': (REPO_ROOT / 'docker' / 'init_patients_db.sh', 'PATIENT_DATABASE_URL',
                 'PATIENT_DATABASE_INIT_FROM_BACKUP'),
    'trials': (REPO_ROOT / 'docker' / 'init_trials_db.sh', 'TRIALS_DATABASE_URL',
               'TRIALS_DATABASE_INIT_FROM_BACKUP'),
}

# The stub answers the reachability probe and the qualified-table probe (as
# "absent", which is what sends the script down the search_path branch), then
# fails the unqualified probe the way a dropped connection would.
_PSQL_STUB = """#!/usr/bin/env bash
for arg in "$@"; do
    case "$arg" in
        "SELECT 1") exit 0 ;;
        *"to_regclass('public."*) echo ""; exit 0 ;;
        *"to_regclass('"*)
            echo "psql: error: connection to server was lost" >&2
            exit 2 ;;
    esac
done
exit 0
"""


def _modern_bash():
    """The scripts use bash 4 syntax (`${VAR,,}`); macOS ships bash 3.2 as
    /bin/bash. Find an interpreter that can actually run them, or skip — a
    green result from a shell that cannot parse the script proves nothing."""
    for candidate in ('/opt/homebrew/bin/bash', '/usr/local/bin/bash', shutil.which('bash')):
        if not candidate or not os.access(candidate, os.X_OK):
            continue
        out = subprocess.run([candidate, '--version'], capture_output=True, text=True)
        first = out.stdout.splitlines()[0] if out.stdout else ''
        version = first.split('version ')[-1].split('.')[0] if 'version ' in first else '0'
        if version.isdigit() and int(version) >= 4:
            return candidate
    return None


def _run(script, url_var, enable_var, stub_dir, bash):
    env = {
        **os.environ,
        'PATH': f"{stub_dir}:{os.environ['PATH']}",
        url_var: 'postgresql://user@localhost/db',
        enable_var: '1',
    }
    return subprocess.run([bash, str(script)], env=env, capture_output=True, text=True,
                          timeout=60)


@pytest.mark.parametrize('which', sorted(SCRIPTS))
def test_a_failed_search_path_probe_refuses_the_restore(which, tmp_path):
    script, url_var, enable_var = SCRIPTS[which]
    bash = _modern_bash()
    if bash is None:                          # pragma: no cover - CI has bash 5
        pytest.skip('no bash >= 4 available to run the script')

    stub = tmp_path / 'psql'
    stub.write_text(_PSQL_STUB)
    stub.chmod(0o755)

    result = _run(script, url_var, enable_var, str(tmp_path), bash)
    combined = result.stdout + result.stderr

    assert result.returncode != 0, (
        f'a failed probe authorised the restore:\n{combined}'
    )
    assert 'refusing to restore' in combined, combined
    # The decisive assertion: the destructive step must never have been reached.
    assert 'DROP SCHEMA' not in combined
    assert 'Recreating public schema' not in combined
