"""The patient-DB password must never reach a psql command line (#403).

Command-line arguments are world-readable: any local process can read
`/proc/<pid>/cmdline`, `ps` shows argv to every user on the host, and it lands
in shell history and process-accounting logs. The credential here opens the
patient database.

The last class is the one that matters most. The commands are copies of the
same helper, so a fix applied to one and not the others closes the ticket while
the other callers keep the defect. That test scans every management command --
discovered from the directory, not from a hardcoded list, so a NEW command
that shells out to psql is guarded the day it is written -- and fails if any of
them puts a non-literal into a psql argv.

What it does not catch, stated so the guard is not read as wider than it is:
`shell=True` with an interpolated command string, and an argv list built in a
variable before the call. Both are visible in review; neither is what the
copies did. Everything it DOES catch is enumerated in
`TestTheGuardActuallyGuards`, which drives the guard itself rather than
describing it.
"""
import ast
import os
import subprocess
from pathlib import Path

import pytest
from django.conf import settings

from trials.services.psql_dsn import psql_dsn_and_env

DSN_WITH_PASSWORD = 'postgresql://reader:s3cr3t@db.example.invalid:5432/patients'

COMMANDS = {
    'compare_trials.py',
    'fetch_exact_for_patients.py',
    'search_trials_for_patients.py',
}


# psql takes a conninfo string as ANY positional argument, not only the first,
# and as the value of -d/--dbname. Everything else after a flag belongs to that
# flag -- `-c <sql>` is a query, legitimately a variable.
_VALUE_FLAGS = {
    '-c', '--command', '-f', '--file', '-v', '--set', '--variable',
    '-o', '--output', '-L', '--log-file', '-F', '--field-separator',
    '-R', '--record-separator', '-P', '--pset', '-U', '--username',
    '-h', '--host', '-p', '--port',
}
_DSN_FLAGS = {'-d', '--dbname'}
_SUBPROCESS_FUNCS = {'run', 'call', 'check_call', 'check_output', 'Popen'}


def _is_subprocess_call(node):
    """`subprocess.run`, an aliased module, and the bare imported forms."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr in _SUBPROCESS_FUNCS
    if isinstance(func, ast.Name):
        return func.id in _SUBPROCESS_FUNCS
    return False


def _dsn_positions(argv):
    """Every element psql could read as a connection string.

    An earlier version marked only argv[0] and the value of -d/--dbname, so
    moving a single flag ahead of the DSN -- `['psql', '--no-psqlrc', db_url]`,
    which is the shape one of the real call sites already has -- defeated it
    entirely.
    """
    positions = []
    expect_dsn_next = False
    for element in argv:
        is_flag = (
            isinstance(element, ast.Constant)
            and isinstance(element.value, str)
            and element.value.startswith('-')
        )
        if expect_dsn_next:
            positions.append(element)
            expect_dsn_next = False
            if not is_flag:
                continue
        if is_flag:
            if element.value in _DSN_FLAGS:
                expect_dsn_next = True
            elif element.value in _VALUE_FLAGS:
                skip_next = True
                expect_dsn_next = False
                # the flag's value is not a DSN; drop it
                positions.append(None)
            continue
        positions.append(element)
    # `None` marks "value of a non-DSN flag" -- drop it and the element after.
    cleaned, skip = [], False
    for item in positions:
        if item is None:
            skip = True
            continue
        if skip:
            skip = False
            continue
        cleaned.append(item)
    return cleaned


def psql_argv_offenders(source: str) -> list[str]:
    """Every non-literal reaching a psql DSN position in *source*.

    One implementation, called by the guard AND by the test that pins the
    guard's reach. A second copy would drift: loosening the guard alone was
    measured to leave the coverage test green.
    """
    offenders = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not _is_subprocess_call(node):
            continue
        if not node.args or not isinstance(node.args[0], (ast.List, ast.Tuple)):
            continue
        argv = node.args[0].elts
        if not argv:
            continue
        first = argv[0]
        if not (
            isinstance(first, ast.Constant)
            and isinstance(first.value, str)
            and Path(first.value).name == 'psql'
        ):
            continue
        for element in _dsn_positions(argv[1:]):
            if isinstance(element, ast.Constant):
                continue
            if isinstance(element, ast.Name) and element.id == 'dsn':
                continue
            offenders.append(f'{node.lineno}: {ast.dump(element)[:70]}')
    return offenders


class TestPsqlDsnAndEnv:
    def test_password_is_moved_out_of_the_dsn(self):
        dsn, env = psql_dsn_and_env(DSN_WITH_PASSWORD)

        assert 's3cr3t' not in dsn
        assert env['PGPASSWORD'] == 's3cr3t'

    def test_everything_else_survives_the_round_trip(self):
        """A DSN that loses its host or database is a fix that breaks the tool."""
        dsn, _ = psql_dsn_and_env(DSN_WITH_PASSWORD)

        assert dsn == 'postgresql://reader@db.example.invalid:5432/patients'

    def test_extra_env_is_passed_through(self):
        _, env = psql_dsn_and_env(DSN_WITH_PASSWORD, PGSSLMODE='require')

        assert env['PGSSLMODE'] == 'require'

    def test_a_url_without_a_password_is_untouched(self):
        url = 'postgresql://reader@db.example.invalid:5432/patients'

        dsn, env = psql_dsn_and_env(url)

        assert dsn == url
        assert 'PGPASSWORD' not in env

    def test_the_parent_environment_is_inherited_not_replaced(self):
        """psql needs PATH, PGSSLROOTCERT and friends from the parent."""
        _, env = psql_dsn_and_env(DSN_WITH_PASSWORD)

        assert env.get('PATH') == os.environ.get('PATH')

    def test_os_environ_is_not_mutated(self):
        before = dict(os.environ)

        psql_dsn_and_env(DSN_WITH_PASSWORD, PGSSLMODE='require')

        assert dict(os.environ) == before, 'the helper must return a copy'

    @pytest.mark.parametrize(
        'url',
        [
            # `urlsplit` ends the netloc at the first `#` or `?`, so these used
            # to come back untouched with the credential still in them --
            # while `_assert_no_password`, which re-derived the netloc the same
            # way, agreed that all was well. libpq reads both as passwords:
            # verified against psql 17.2.
            'postgresql://u:pa#ss@h/db',
            'postgresql://u:pa?ss@h/db',
            # libpq percent-decodes parameter NAMES too.
            'postgresql://u@h/db?%70assword=s3cr3t',
            # The client-key passphrase is a credential as much as `password`.
            'postgresql://u@h/db?sslpassword=s3cr3t',
        ],
    )
    def test_credentials_libpq_accepts_do_not_survive(self, url):
        dsn, env = psql_dsn_and_env(url)

        assert 's3cr3t' not in dsn and 'pa#ss' not in dsn and 'pa?ss' not in dsn
        assert env['PGPASSWORD']

    def test_an_at_sign_in_the_path_does_not_truncate_the_host(self):
        dsn, env = psql_dsn_and_env('postgresql://reader:s3cr3t@h:5432/pat@ients')

        assert dsn == 'postgresql://reader@h:5432/pat@ients'
        assert env['PGPASSWORD'] == 's3cr3t'

    def test_keyword_conninfo_whose_value_contains_a_scheme(self):
        """`host=h options='a://b'` is conninfo, not a URI."""
        dsn, env = psql_dsn_and_env("host=h dbname=d user=u password='a://b'")

        assert 'a://b' not in dsn
        assert env['PGPASSWORD'] == 'a://b'

    def test_an_ipv6_literal_keeps_its_brackets(self):
        """Rebuilding from urlsplit components dropped them, and psql then read
        the port as `db8::1:5432`."""
        dsn, _ = psql_dsn_and_env('postgresql://r:s@[2001:db8::1]:5432/pat')

        assert dsn == 'postgresql://r@[2001:db8::1]:5432/pat'

    def test_a_multi_host_uri_does_not_raise(self):
        """`urlsplit().port` raises ValueError on these; libpq accepts them."""
        url = 'postgresql://postgres@127.0.0.1:5432,127.0.0.1:5433/db'

        assert psql_dsn_and_env(url)[0] == url

    @pytest.mark.parametrize('password', ['p@ss:word/with?chars', 'sim ple', '%40'])
    def test_awkward_passwords_still_leave_the_dsn(self, password):
        from urllib.parse import quote

        url = f'postgresql://reader:{quote(password, safe="")}@host.invalid:5432/db'

        dsn, env = psql_dsn_and_env(url)

        assert password not in dsn
        assert quote(password, safe='') not in dsn
        assert env['PGPASSWORD'] == password


class TestNoCommandPassesAUrlToPsql:
    """The regression guard for every command at once.

    Reading the sources rather than calling the commands is deliberate: these
    commands need a live patient database, so a behavioural test would not run
    in CI, and the defect is visible in the call itself.
    """

    @staticmethod
    def _command_files():
        directory = Path(settings.BASE_DIR) / 'trials' / 'management' / 'commands'
        return sorted(p for p in directory.glob('*.py') if p.name != '__init__.py')

    def test_the_directory_scan_finds_the_known_callers(self):
        """Otherwise an empty glob would make every test below vacuous."""
        names = {p.name for p in self._command_files()}

        assert COMMANDS <= names, f'expected {COMMANDS} among {sorted(names)}'

    def test_no_command_puts_a_non_literal_into_a_psql_dsn_position(self):
        offenders = []
        for path in self._command_files():
            for offender in psql_argv_offenders(path.read_text()):
                offenders.append(f'{path.name}:{offender}')

        assert not offenders, (
            'a non-literal reaches a psql connection-string position, where any '
            'local process can read it: ' + '; '.join(offenders)
        )

    @pytest.mark.parametrize('name', sorted(COMMANDS))
    def test_the_command_calls_the_shared_helper(self, name):
        """A substring check would pass on a stale import; assert the call."""
        path = Path(settings.BASE_DIR) / 'trials' / 'management' / 'commands' / name
        tree = ast.parse(path.read_text())

        called = any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == 'psql_dsn_and_env'
            for node in ast.walk(tree)
        )

        assert called, f'{name} shells out to psql but never calls psql_dsn_and_env'


class TestTheGuardActuallyGuards:
    """The guard's reach, asserted rather than described.

    These call `psql_argv_offenders` -- the same function the guard above uses,
    not a copy of it. An earlier version re-implemented the discriminating
    lines here, and loosening the real guard left this class green: a test
    whose whole purpose was to pin coverage noticed nothing.
    """

    CAUGHT = [
        "subprocess.run(['psql', db_url, '-t', '-c', sql])",
        "subprocess.run(['psql', '-d', db_url, '-c', sql])",
        "subprocess.run(['psql', '--no-psqlrc', db_url, '-c', sql])",
        "subprocess.run(['psql', '-tAX', db_url, '-c', sql])",
        "subprocess.run(['psql', '-v', 'ON_ERROR_STOP=1', db_url, '-c', sql])",
        "subprocess.run(['psql', conn, '-c', sql])",
        "subprocess.run(['psql', f'{db_url}', '-c', sql])",
        "subprocess.run(['psql', self.db_url, '-c', sql])",
        "subprocess.Popen(['psql', db_url, '-c', sql])",
        "subprocess.check_call(['psql', db_url, '-c', sql])",
        "run(['psql', db_url, '-c', sql])",
        "sp.run(['psql', db_url, '-c', sql])",
        "subprocess.run(('psql', db_url, '-c', sql))",
        "subprocess.run(['/usr/bin/psql', db_url, '-c', sql])",
        "subprocess.check_output(['psql', db_url])",
    ]
    ALLOWED = [
        "subprocess.run(['psql', dsn, '-t', '--no-psqlrc', '-c', sql])",
        "subprocess.run(['psql', dsn, '-c', sql])",
        "subprocess.run(['psql', '-d', dsn, '-c', sql])",
        "subprocess.run(['psql', '--no-psqlrc', dsn, '-v', 'ON_ERROR_STOP=1'])",
    ]

    @pytest.mark.parametrize('source', CAUGHT)
    def test_a_reintroduced_defect_is_caught(self, source):
        assert psql_argv_offenders(source), f'guard missed: {source}'

    @pytest.mark.parametrize('source', ALLOWED)
    def test_the_fixed_shape_is_not_flagged(self, source):
        assert not psql_argv_offenders(source), f'false positive on: {source}'


class TestTheShellTwinAgreesWithPython:
    """docker/psql_dsn.sh does the same job for the container init scripts.

    It is the path that runs unattended on every cold start, and it had no
    coverage at all. Two implementations of one security rule drift; these
    tests are what notices.
    """

    @staticmethod
    def _split(url):
        script = Path(settings.BASE_DIR) / 'docker' / 'psql_dsn.sh'
        result = subprocess.run(
            [
                'bash', '-c',
                f'source {script}; psql_dsn_split "$1" || exit 3; '
                'printf "%s\\n%s" "$PSQL_DSN" "${PGPASSWORD-}"',
                '_', url,
            ],
            capture_output=True, text=True,
        )
        return result.returncode, result.stdout.split('\n')

    @pytest.mark.parametrize(
        'url',
        [
            'postgresql://reader:s3cr3t@db.example.invalid:5432/patients',
            'postgresql://reader:p%40ss%20word@h:5432/db',
            'postgresql://r:s@[2001:db8::1]:5432/pat',
            'postgresql://reader@h:5432/patients',
            'postgres://u:@h/db',
        ],
    )
    def test_the_shell_matches_python(self, url):
        code, (dsn, password) = self._split(url)
        expected_dsn, expected_env = psql_dsn_and_env(url)

        assert code == 0
        assert dsn == expected_dsn
        assert password == expected_env.get('PGPASSWORD', '')

    @pytest.mark.parametrize(
        'password,expected',
        [
            # `printf '%b' "${1//%/\\x}"` mangled all three: the backslash was
            # swallowed, %0A became a literal newline via the wrong route, and
            # `\c` truncated the credential outright with no error.
            ('back%5Cslash', 'back\\slash'),
            ('50%25off', '50%off'),
            ('pass%5Cctail', 'pass\\ctail'),
        ],
    )
    def test_awkward_passwords_survive_the_shell_decode(self, password, expected):
        code, (_dsn, decoded) = self._split(f'postgresql://u:{password}@h/db')

        assert code == 0
        assert decoded == expected

    @pytest.mark.parametrize(
        'url',
        [
            'postgresql://reader@h/db?password=leaky',
            'host=h dbname=d password=leaky',
        ],
    )
    def test_the_shell_refuses_what_it_cannot_clean(self, url):
        """Fail closed. A silent pass would make the init scripts' own comment
        false exactly where it mattered."""
        code, _ = self._split(url)

        assert code == 3

    def test_a_dsn_without_a_password_is_left_alone(self):
        """An earlier version split on the last `@` of the whole remainder, so
        a `@` in the path destroyed the host and exported a bogus password."""
        url = 'postgresql://db.example:5432/pat@ients'

        code, (dsn, password) = self._split(url)

        assert code == 0
        assert dsn == url
        assert password == ''
