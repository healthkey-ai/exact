"""Guards the deploy-staging rollout order: migrate, then create the revision.

`gcloud run deploy --no-traffic` withholds requests but still starts the new
revision and waits for it to become ready. The trials copy is migrated only by
the migrate job's `migrate_trials_db` pass (the service's own `migrate` covers
`default`), and from #226 on the entrypoint exits when the image models a
trials column that is not there yet. A revision created before that pass then
fails the deploy step, and the pass is never reached.

Source-level, like tests/test_deploy_render_workflow.py: the failure is an
ordering mistake in the file, so reading the file is the check. Regexes rather
than PyYAML, which is not a dependency of this project.
"""
import re
from pathlib import Path

DEPLOY_STAGING = (
    Path(__file__).resolve().parents[1] / '.github' / 'workflows' / 'deploy-staging.yml'
)


def _code(text):
    """The file without comment lines, with shell line continuations joined."""
    lines = [line for line in text.split('\n') if not line.lstrip().startswith('#')]
    return re.sub(r'\\\n\s*', ' ', '\n'.join(lines))


def _steps():
    """(name, body) for each step of the job, in file order, comments dropped."""
    text = _code(DEPLOY_STAGING.read_text())
    steps = text[text.index('\n    steps:\n'):]
    starts = list(re.finditer(r'^      - (?:name|uses):\s*(.+?)\s*$', steps, re.MULTILINE))
    return [
        (match.group(1), steps[match.start():(starts[i + 1].start() if i + 1 < len(starts) else len(steps))])
        for i, match in enumerate(starts)
    ]


def _indexes(command):
    found = [i for i, (_, body) in enumerate(_steps()) if command in body]
    assert found, f'no step runs {command!r}'
    return found


def _calls(command):
    """Every shell line, across all steps, that runs `command`."""
    return [line for _, body in _steps() for line in body.split('\n') if command in line]


def test_both_migrate_passes_finish_before_the_revision_is_created():
    deploy = min(_indexes('gcloud run deploy'))
    assert max(_indexes('gcloud run jobs execute')) < deploy, (
        'the migrate job must run before `gcloud run deploy`: the new revision '
        'is started even with --no-traffic, and its schema check exits against '
        'trials columns the job has not applied yet'
    )
    assert max(_indexes('migrate_trials_db')) < deploy, (
        'the trials copy is migrated only by the `migrate_trials_db` pass'
    )


def test_every_migrate_pass_is_waited_for():
    """Without --wait, execute returns at once and the deploy races the job."""
    executes = _calls('gcloud run jobs execute')
    assert len(executes) >= 2, executes
    for line in executes:
        assert '--wait' in line, line


def test_nothing_on_the_path_can_be_skipped_or_soft_failed():
    """A skipped or swallowed failure would let the deploy run unmigrated."""
    text = _code(DEPLOY_STAGING.read_text())
    assert not re.search(r'^\s+continue-on-error:', text, re.MULTILINE)
    for command in ('gcloud run jobs execute', 'gcloud run deploy'):
        for i in _indexes(command):
            _, body = _steps()[i]
            assert not re.search(r'^\s+if:', body, re.MULTILINE), command
            assert '|| true' not in body and 'set +e' not in body, command
    for line in _calls('gcloud run deploy'):
        assert '--async' not in line, line


def test_traffic_moves_only_after_the_revision_exists():
    assert max(_indexes('gcloud run deploy')) < min(
        _indexes('gcloud run services update-traffic')
    )


def test_the_migrate_job_runs_the_image_being_deployed():
    """The job keeps its last image; it must be pointed at this build first."""
    updates = [
        line for line in _calls('gcloud run jobs update')
        if '--image "$IMAGE:${{ github.sha }}"' in line
    ]
    assert updates, 'the migrate job is never pointed at this build'
    first_update = min(i for i, (_, body) in enumerate(_steps()) if updates[0] in body)
    assert first_update <= min(_indexes('gcloud run jobs execute'))
    _, body = _steps()[first_update]
    if 'gcloud run jobs execute' in body:
        assert body.index(updates[0]) < body.index('gcloud run jobs execute')
