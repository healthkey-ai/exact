"""Guards the deploy-render `workflow_run` trigger against a silent no-op.

`on.workflow_run.workflows` matches the triggering workflow's `name:`, not its
filename. The test suite lives in django.yml but is *named* `backend`, so
`workflows: [django]` matched nothing: the workflow never fired, every `if:`
below it was unreachable, and neither GitHub nor CI reported anything. The only
symptom was a deploy that quietly stopped happening.

Source-level, in the spirit of tests/test_settings_security.py — the failure is
a configuration mismatch, so reading the files is the check. Parsed with
regexes rather than PyYAML, which is not a dependency of this project.
"""
import json
import os
import re
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
    """The inline `python3 -c` program in the deploy step containing *marker*.

    Executable form: dedented, with the shell's `\\"` escapes resolved. Lets the
    tests below run the workflow's own logic instead of grepping for it — a
    source match proves a check is present, never that it is right.
    """
    scripts = _run_scripts()
    step = next(s for s in scripts.values() if '-X POST' in s)
    blocks = re.findall(r'python3 -c "\n(.*?)\n *"', step, re.DOTALL)
    matching = [b for b in blocks if marker in b]
    assert len(matching) == 1, \
        f'expected exactly one embedded program containing {marker!r}, got {len(matching)}'
    return textwrap.dedent(matching[0].replace('\\"', '"'))


def _run_embedded(program, stdin, commit_id):
    return subprocess.run(
        [sys.executable, '-c', program],
        input=stdin,
        capture_output=True,
        text=True,
        env={**os.environ, 'COMMIT_ID': commit_id},
    )


def _triggering_workflows():
    """The entries of `on.workflow_run.workflows` in deploy-render.yml.

    Accepts both YAML list forms — flow (`workflows: [backend]`) and block
    (`workflows:` then `  - backend`) — so reformatting the file does not turn
    into a spurious failure here.
    """
    source = DEPLOY_RENDER.read_text()
    flow = re.search(r'^\s*workflows:\s*\[([^\]]*)\]\s*$', source, re.MULTILINE)
    if flow:
        entries = flow.group(1).split(',')
    else:
        block = re.search(
            r'^(\s*)workflows:\s*\n((?:\1\s+-\s*\S.*\n)+)', source, re.MULTILINE
        )
        assert block is not None, \
            'no `workflows:` list (flow or block form) found in deploy-render.yml'
        entries = re.findall(r'-\s*(\S.*?)\s*$', block.group(2), re.MULTILINE)
    return [e.strip().strip('\'"') for e in entries if e.strip()]


class TestWorkflowRunTrigger:
    def test_every_triggering_workflow_name_exists(self):
        declared = set(_declared_workflow_names().values())
        for referenced in _triggering_workflows():
            assert referenced in declared, (
                f'deploy-render.yml triggers on workflow {referenced!r}, but no '
                f'workflow declares that `name:`. Known names: {sorted(declared)}. '
                'workflow_run matches `name:`, not the filename — a mismatch '
                'means the deploy silently never runs.'
            )

    def test_it_triggers_on_the_test_suite(self):
        """Specifically the backend suite, so the deploy stays gated on tests."""
        backend_name = _declared_workflow_names().get('django.yml')
        assert backend_name is not None, 'django.yml has no `name:`'
        assert backend_name in _triggering_workflows(), (
            f'deploy-render.yml must trigger on django.yml (named {backend_name!r}) '
            'or the deploy is no longer gated on the test suite.'
        )

    @pytest.mark.parametrize('guard', [
        r"github\.event\.workflow_run\.conclusion\s*==\s*'success'",
        r"github\.event\.workflow_run\.head_branch\s*==\s*'main'",
    ])
    def test_conclusion_and_branch_are_both_guarded(self, guard):
        """workflow_run fires for every branch and every conclusion, including
        failures — dropping either guard turns this into a deploy-on-red."""
        assert re.search(guard, DEPLOY_RENDER.read_text()), \
            f'deploy-render.yml is missing the {guard!r} guard'


class TestDeployIsPinnedToTheTestedCommit:
    """Gating on a green suite is worthless if the deploy is not the revision
    that turned it green. Render builds the tracked branch's current tip unless
    the request names a commit, so a push landing while the suite ran would
    ship untested — the exact race the `workflow_run` gate is meant to close.
    """

    def test_commit_id_comes_from_the_triggering_run(self):
        source = DEPLOY_RENDER.read_text()
        assert re.search(
            r"COMMIT_ID:\s*\$\{\{\s*github\.event\.workflow_run\.head_sha",
            source,
        ), ('deploy-render.yml must resolve COMMIT_ID from '
            'github.event.workflow_run.head_sha — the revision the suite ran.')

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

    def test_manual_dispatch_is_branch_guarded(self):
        """Unguarded dispatch used to be harmless — the request carried no
        commit, so Render built the tracked branch regardless of the ref. With
        the commit pinned it would ship the dispatched branch's untested tip."""
        source = DEPLOY_RENDER.read_text()
        assert re.search(
            r"github\.event_name\s*==\s*'workflow_dispatch'\s*&&\s*\n?\s*"
            r"github\.ref\s*==\s*'refs/heads/main'",
            source,
        ), 'the workflow_dispatch arm of the `if:` must also require main'

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
        'sha-with-a-"quote"',   # json.dumps escapes; printf would not
        'sha\\with\\backslash',
    ])
    def test_the_request_body_escapes_its_input(self, commit_id):
        program = _embedded_python('json.dumps')
        result = _run_embedded(program, '', commit_id)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {'commitId': commit_id}

    @pytest.mark.parametrize('body,expected_id', [
        ({'id': 'dep-1', 'commit': {'id': SHA}}, 'dep-1'),
        ({'id': 'dep-2'}, 'dep-2'),                      # no commit echoed back
        ({'id': 'dep-3', 'commit': None}, 'dep-3'),
    ])
    def test_an_accepted_deploy_for_this_commit_is_used(self, body, expected_id):
        program = _embedded_python('built')
        result = _run_embedded(program, json.dumps(body), SHA)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected_id

    def test_an_accepted_deploy_for_another_commit_fails_the_run(self):
        """If Render ever ignored `commitId`, the build would be of the branch
        tip and the run would otherwise report success."""
        program = _embedded_python('built')
        result = _run_embedded(
            program, json.dumps({'id': 'dep-x', 'commit': {'id': OTHER_SHA}}), SHA
        )
        assert result.returncode != 0
        assert OTHER_SHA in result.stderr

    def test_an_empty_body_yields_no_deploy_id(self):
        """The 202 case, which hands over to the adopt path."""
        program = _embedded_python('built')
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
