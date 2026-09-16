"""Guards the deploy-render workflow: manual-only, test-gated, commit-pinned.

It used to trigger on `workflow_run`, which GitHub reads from the default
branch's copy of the file — dormant while `main` was the default, and an
automatic deploy of every green `main` push once `dev` became the default.
Staging deploys are manual (#329), so this one is too, and the green-suite gate
that `workflow_run` provided is now an explicit first step.

Source-level, in the spirit of tests/test_settings_security.py — the failure is
a configuration mismatch, so reading the files is the check. Parsed with
regexes rather than PyYAML, which is not a dependency of this project.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SHA = 'a' * 40
OTHER_SHA = 'b' * 40

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / '.github' / 'workflows'
DEPLOY_RENDER = WORKFLOWS_DIR / 'deploy-render.yml'


def _workflow_files():
    """Both extensions: GitHub accepts .yaml, and missing one would make the
    name lookup below report a false mismatch."""
    return sorted(
        p for ext in ('*.yml', '*.yaml') for p in WORKFLOWS_DIR.glob(ext)
    )


def _declared_workflow_names():
    """Map every workflow file to its top-level `name:`."""
    names = {}
    for path in _workflow_files():
        match = re.search(r'^name:\s*(.+?)\s*$', path.read_text(), re.MULTILINE)
        if match:
            names[path.name] = match.group(1).strip('\'"')
    return names


def _run_scripts():
    """Map each step's `name:` to its shell script, for both `run:` forms.

    Hand-rolled rather than PyYAML, which is not a dependency of this project.
    A block scalar ends where indentation returns to the key's level — scanning
    lines by indent, not a regex lookahead for the next `key:`, because shell
    lines like `-H "Content-Type: application/json"` look exactly like one.
    """
    lines = DEPLOY_RENDER.read_text().split('\n')
    scripts = {}
    name = None
    i = 0
    while i < len(lines):
        named = re.match(r'\s*- name:\s*(\S.*?)\s*$', lines[i])
        if named:
            name = named.group(1).strip('\'"')
        run = re.match(r'(\s*)run:\s*(\|[-+]?|>[-+]?)?\s*(.*?)\s*$', lines[i])
        if run and name:
            indent, block, inline = run.group(1), run.group(2), run.group(3)
            if not block:
                scripts[name] = inline
                i += 1
                continue
            body, i = [], i + 1
            while i < len(lines) and (
                not lines[i].strip()
                or len(lines[i]) - len(lines[i].lstrip()) > len(indent)
            ):
                body.append(lines[i])
                i += 1
            scripts[name] = '\n'.join(body)
            continue
        i += 1
    assert scripts, 'no run: scripts found in deploy-render.yml'
    # Keyed on the step name, so a step added without one would map its script
    # onto the previous step's key and silently drop that script from the map —
    # quietly narrowing the `${{`-injection guard below.
    declared = len(re.findall(r'^\s*run:', DEPLOY_RENDER.read_text(), re.MULTILINE))
    assert len(scripts) == declared, (
        f'found {declared} `run:` keys but only {len(scripts)} named steps — '
        'every step needs a `name:` for these tests to cover it'
    )
    return scripts


def _without_comments(script):
    """Drop comment-only lines, so a check cannot be satisfied by prose."""
    return '\n'.join(
        line for line in script.split('\n') if not line.lstrip().startswith('#')
    )


def _deploy_post_request():
    """The comment-free script of the step that POSTs the deploy."""
    for name, script in _run_scripts().items():
        stripped = _without_comments(script)
        if '-X POST' in stripped and '/deploys' in stripped:
            return stripped
    raise AssertionError('no step POSTs to the Render deploys endpoint')


def _embedded_python(marker):
    """The inline `python3 -c` program, across all steps, containing *marker*.

    Executable form: dedented, with the shell's `\\"` escapes resolved. Lets the
    tests below run the workflow's own logic instead of grepping for it — a
    source match proves a check is present, never that it is right.
    """
    blocks = [
        block
        for script in _run_scripts().values()
        for block in re.findall(r'python3 -c "\n(.*?)\n *"', script, re.DOTALL)
    ]
    matching = [b for b in blocks if marker in b]
    assert len(matching) == 1, (
        f'expected exactly one embedded program containing {marker!r}, '
        f'got {len(matching)} (of {len(blocks)} programs)'
    )
    return textwrap.dedent(matching[0].replace('\\"', '"'))


def _run_embedded(program, stdin, commit_id):
    # The deploy job runs no setup-python, so the workflow's `python3` is the
    # system one. Prefer that over the interpreter running pytest, which may be
    # newer and accept syntax the runner would reject.
    interpreter = shutil.which('python3') or sys.executable
    return subprocess.run(
        [interpreter, '-c', program],
        input=stdin,
        capture_output=True,
        text=True,
        env={**os.environ, 'COMMIT_ID': commit_id},
    )


def _triggers():
    """The keys directly under `on:` in deploy-render.yml, in order.

    Line-based rather than a regex over the block: the block ends at the next
    unindented line, and its keys are the lines indented by exactly two spaces.
    """
    lines = DEPLOY_RENDER.read_text().split('\n')
    assert 'on:' in lines, 'no block-form `on:` found in deploy-render.yml'
    keys = []
    for line in lines[lines.index('on:') + 1:]:
        if line and not line[0].isspace():
            break
        key = re.match(r'  (\w+):', line)
        if key:
            keys.append(key.group(1))
    return keys


class TestManualDispatchOnly:
    def test_the_only_trigger_is_workflow_dispatch(self):
        """Not `workflow_run`, not `push`: either would deploy on its own. A
        `workflow_run` trigger also switches on silently when the default
        branch changes, since GitHub reads it from the default branch's copy."""
        assert _triggers() == ['workflow_dispatch']

    def test_manual_dispatch_is_branch_guarded(self):
        """The deploy is pinned to the dispatched commit, so an unguarded
        dispatch would ship any branch's untested tip. Checked by the first
        step, which fails loudly, rather than a job `if:` that shows "skipped"."""
        name, script = next(iter(_run_scripts().items()))
        assert re.search(
            r'\[\s*"\$REF"\s*!=\s*"refs/heads/main"\s*\]', _without_comments(script)
        ), f'the first step ({name!r}) must refuse any ref but main'
        assert 'exit 1' in _without_comments(script)
        assert re.search(
            r"REF:\s*\$\{\{\s*github\.ref\s*\}\}", DEPLOY_RENDER.read_text()
        ), 'the branch check must read github.ref'


class TestDeployIsGatedOnTheTestSuite:
    """The gate `workflow_run` used to provide: no green backend run for the
    dispatched commit, no deploy."""

    def _gate(self):
        scripts = {n: _without_comments(s) for n, s in _run_scripts().items()}
        names = list(scripts)
        gate = next(
            (i for i, n in enumerate(names) if 'gh run list' in scripts[n]), None
        )
        assert gate is not None, 'no step runs the backend gate'
        deploy = next(
            (i for i, n in enumerate(names) if '-X POST' in scripts[n]), None
        )
        assert deploy is not None, 'no step POSTs the deploy'
        assert gate < deploy, (
            f'the backend gate ({names[gate]!r}) must run before the deploy '
            'request, so nothing is deployed before it passes'
        )
        return scripts[names[gate]]

    def test_the_gate_checks_the_backend_suite(self):
        backend_file = 'django.yml'
        assert backend_file in _declared_workflow_names(), \
            'django.yml (the backend suite) no longer exists'
        assert re.search(r'--workflow\s+' + re.escape(backend_file), self._gate())

    @pytest.mark.parametrize('flag', [
        r'--commit\s+"\$COMMIT_ID"',
        r'--status\s+success',
        # A pull_request run — a fork's included — must never satisfy it.
        r'--event\s+push',
    ])
    def test_the_gate_requires_a_successful_push_run_of_this_commit(self, flag):
        assert re.search(flag, self._gate()), f'the gate is missing {flag!r}'

    def test_the_gate_fails_when_nothing_matches(self):
        assert re.search(r'-lt\s+1\s*\]', self._gate())
        assert 'exit 1' in self._gate()

    def test_the_token_can_read_actions(self):
        lines = DEPLOY_RENDER.read_text().split('\n')
        assert 'permissions:' in lines, 'deploy-render.yml sets no permissions'
        block = []
        for line in lines[lines.index('permissions:') + 1:]:
            if line and not line[0].isspace():
                break
            block.append(line.strip())
        assert 'actions: read' in block, \
            '`gh run list` needs `actions: read` on the job token'


class TestDeployIsPinnedToTheTestedCommit:
    """Gating on a green suite is worthless if the deploy is not the revision
    that turned it green. Render builds the tracked branch's current tip unless
    the request names a commit, so a push landing after the dispatch would
    ship untested — past the gate that checked the dispatched commit.
    """

    def test_every_step_uses_the_dispatched_commit(self):
        """The gate and the deploy must name the same revision."""
        source = DEPLOY_RENDER.read_text()
        values = re.findall(r'COMMIT_ID:\s*(\$\{\{.*?\}\})', source)
        assert len(values) == 3, f'expected COMMIT_ID in 3 steps, got {values}'
        assert set(values) == {'${{ github.sha }}'}, values

    def test_the_deploy_request_pins_the_commit(self):
        """Asserted on the request line, not on the file.

        A bare `'commitId' in source` is satisfied by the comment that explains
        the pinning, so dropping the `-d` argument entirely — which restores
        "build the tracked branch's tip" — would leave this green.
        """
        post = _deploy_post_request()
        assert re.search(r'-d\s+\S', post), (
            'the deploy request carries no body; Render then builds the tracked '
            "branch's tip rather than the tested commit."
        )
        assert 'COMMIT_ID' in post, \
            'the deploy request body must pin the commit from COMMIT_ID'
        assert not re.search(r"""-d\s*["']\{\}["']""", post), (
            "posting an empty body deploys the tracked branch's tip, not the "
            'tested commit.'
        )

    def test_the_commit_is_passed_through_the_environment(self):
        """Not interpolated into the shell: `${{ }}` inside `run:` is textual
        substitution, which is how workflow injections happen. Checked against
        every `run:` form, block scalar or single line."""
        for name, script in _run_scripts().items():
            assert '${{' not in script, (
                f'the {name!r} step interpolates a GitHub expression directly '
                'into its run: script; pass it via env: instead.'
            )

    def test_the_accepted_deploy_is_checked_against_the_commit(self):
        """Both response paths verify, not just the adopted one: an API that
        silently ignored `commitId` would otherwise build the branch tip and
        the run would call it a success."""
        post = _deploy_post_request()
        assert re.search(r"built\s*!=\s*os\.environ\['COMMIT_ID'\]", post), \
            "the 201 response's commit must be checked against COMMIT_ID"

    def test_an_adopted_deploy_is_checked_against_the_commit(self):
        """The 202/empty-body path adopts "the most recent deploy". Unchecked,
        that can be another revision, or a build already `live` — which the
        wait step would pass on its first poll, reporting success for a deploy
        this run never made."""
        source = DEPLOY_RENDER.read_text()
        assert re.search(r"queued\s*!=\s*os\.environ\['COMMIT_ID'\]", source), \
            'the adopted deploy must be verified against COMMIT_ID'
        assert re.search(r"deploy\.get\('status'\)\s*not in\s*IN_FLIGHT", source), (
            'an adopted deploy must be required to be in flight — a finished '
            'one (live, or failed) means nothing was queued for this run'
        )


class TestTheWorkflowsOwnLogic:
    """Runs the workflow's embedded programs, rather than grepping for them.

    The source-level tests above can only prove a check is present. They stay
    green if its sense is inverted — `built is not None` to `built is None`, say
    — so the behaviour is pinned here by feeding each program the API responses
    it has to survive.
    """

    def test_the_request_body_is_well_formed_json_naming_the_commit(self):
        program = _embedded_python('json.dumps')
        result = _run_embedded(program, '', SHA)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {'commitId': SHA}

    @pytest.mark.parametrize('commit_id', [
        SHA,
        'sha-with-a-"quote"',   # json.dumps escapes; a %-format would not
        'sha\\with\\backslash',
    ])
    def test_the_request_body_escapes_its_input(self, commit_id):
        # Located by `commitId`, which is the field being built rather than the
        # function building it — so this fails on unescaped output, not merely
        # because someone stopped calling json.dumps.
        program = _embedded_python('commitId')
        result = _run_embedded(program, '', commit_id)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {'commitId': commit_id}

    @pytest.mark.parametrize('body,expected_id', [
        ({'id': 'dep-1', 'commit': {'id': SHA}}, 'dep-1'),
        # A response that names no commit is tolerated *here* only because the
        # wait step refuses to call such a deploy a success — see
        # TestTheDeployIsConfirmedNotJustRequested. Failing closed at this point
        # instead would make every deploy fail against an API that simply does
        # not echo the field.
        ({'id': 'dep-2'}, 'dep-2'),
        ({'id': 'dep-3', 'commit': None}, 'dep-3'),
    ])
    def test_an_accepted_deploy_for_this_commit_is_used(self, body, expected_id):
        program = _embedded_python('raw =')
        result = _run_embedded(program, json.dumps(body), SHA)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected_id

    def test_an_accepted_deploy_for_another_commit_fails_the_run(self):
        """If Render ever ignored `commitId`, the build would be of the branch
        tip and the run would otherwise report success."""
        program = _embedded_python('raw =')
        result = _run_embedded(
            program, json.dumps({'id': 'dep-x', 'commit': {'id': OTHER_SHA}}), SHA
        )
        assert result.returncode != 0
        assert OTHER_SHA in result.stderr

    def test_an_empty_body_yields_no_deploy_id(self):
        """The 202 case, which hands over to the adopt path."""
        program = _embedded_python('raw =')
        result = _run_embedded(program, '', SHA)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == ''

    @pytest.mark.parametrize('status', [
        'created', 'queued', 'build_in_progress',
        'pre_deploy_in_progress', 'update_in_progress',
    ])
    def test_an_in_flight_deploy_for_this_commit_is_adopted(self, status):
        program = _embedded_python('IN_FLIGHT')
        body = [{'deploy': {'id': 'dep-9', 'status': status, 'commit': {'id': SHA}}}]
        result = _run_embedded(program, json.dumps(body), SHA)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == 'dep-9'

    @pytest.mark.parametrize('deploy,reason', [
        ({'id': 'd', 'status': 'build_in_progress', 'commit': {'id': OTHER_SHA}},
         'another commit'),
        ({'id': 'd', 'status': 'build_in_progress'},
         'no commit at all'),
        ({'id': 'd', 'status': 'live', 'commit': {'id': SHA}},
         'already finished — nothing was queued for this run'),
        ({'id': 'd', 'status': 'build_failed', 'commit': {'id': SHA}},
         'failed earlier — its failure is not this run\'s'),
        ({'id': 'd', 'status': 'canceled', 'commit': {'id': SHA}},
         'canceled'),
    ])
    def test_an_unsuitable_deploy_is_not_adopted(self, deploy, reason):
        program = _embedded_python('IN_FLIGHT')
        result = _run_embedded(program, json.dumps([{'deploy': deploy}]), SHA)
        assert result.returncode != 0, \
            f'a deploy that is {reason} must not be adopted'
        assert not result.stdout.strip()


class TestTheDeployIsConfirmedNotJustRequested:
    """Pinning `commitId` on the request only states an intention.

    The POST response is checked when it happens to echo a commit, so on its own
    the pin fails open: an API that stopped echoing the field, or silently
    dropped `commitId` from the request, would look identical to success. The
    deploy detail endpoint reports the commit actually built, so the wait step
    is where the pin is confirmed — and a deploy that reaches `live` without
    ever naming a commit has to fail.
    """

    @pytest.fixture
    def verdict(self):
        program = _embedded_python('FAILED')

        def check(deploy, commit_id=SHA):
            body = '' if deploy is None else json.dumps(deploy)
            result = _run_embedded(program, body, commit_id)
            assert result.returncode == 0, result.stderr
            return result.stdout.strip()

        return check

    def test_live_at_the_tested_commit_succeeds(self, verdict):
        assert verdict({'status': 'live', 'commit': {'id': SHA}}) == 'live'

    def test_live_without_a_commit_is_not_a_success(self, verdict):
        """The fail-open case: nothing here confirms `commitId` was honoured."""
        assert verdict({'status': 'live'}) == 'unverified'
        assert verdict({'status': 'live', 'commit': None}) == 'unverified'

    def test_a_different_commit_fails_even_while_still_building(self, verdict):
        """Caught as early as the API admits it, not after 30 minutes."""
        assert verdict(
            {'status': 'build_in_progress', 'commit': {'id': OTHER_SHA}}
        ) == f'mismatch {OTHER_SHA}'

    def test_a_different_commit_outranks_a_live_status(self, verdict):
        assert verdict({'status': 'live', 'commit': {'id': OTHER_SHA}}) \
            == f'mismatch {OTHER_SHA}'

    @pytest.mark.parametrize('status', [
        'build_failed', 'update_failed', 'pre_deploy_failed',
        'canceled', 'deactivated',
    ])
    def test_terminal_failures_are_reported(self, verdict, status):
        assert verdict({'status': status, 'commit': {'id': SHA}}) == f'failed {status}'

    @pytest.mark.parametrize('status', [
        'created', 'queued', 'build_in_progress', 'update_in_progress',
    ])
    def test_in_flight_statuses_keep_polling(self, verdict, status):
        assert verdict({'status': status, 'commit': {'id': SHA}}) == f'waiting {status}'

    def test_an_unparseable_or_empty_answer_costs_one_poll(self, verdict):
        """A transient API hiccup must not fail the deploy."""
        assert verdict(None) == 'pending'
        assert verdict({}) == 'pending'

    def test_the_wait_step_receives_the_commit(self):
        """It cannot confirm anything without COMMIT_ID in its own env block."""
        scripts = _run_scripts()
        name = next(n for n, s in scripts.items() if 'FAILED' in s)
        source = DEPLOY_RENDER.read_text()
        step = source.split(f'- name: {name}', 1)[1].split('run:', 1)[0]
        assert re.search(
            r"COMMIT_ID:\s*\$\{\{\s*github\.sha\s*\}\}", step
        ), f'the {name!r} step must receive COMMIT_ID via env:'
