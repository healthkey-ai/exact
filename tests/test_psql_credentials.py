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

from trials.services.psql_dsn import DsnStillCarriesPassword, psql_dsn_and_env

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
        ],
    )
    def test_credentials_libpq_accepts_do_not_survive(self, url):
        dsn, env = psql_dsn_and_env(url)

        assert 's3cr3t' not in dsn and 'pa#ss' not in dsn and 'pa?ss' not in dsn
        assert env['PGPASSWORD']

    def test_an_ssl_key_passphrase_is_refused_not_repurposed(self):
        """`sslpassword` is a credential too, but it is not the database
        user's password and libpq has no environment variable for it — checked
        against libpq 17, which offers PGPASSWORD and fifteen PGSSL* names,
        none of them PGSSLPASSWORD. Handing it back as PGPASSWORD lost the key
        passphrase and authenticated with it: a certificate-authenticated
        connection broken twice over, which is worse than the argv exposure
        being fixed. With nowhere to relocate it, the only honest answer is to
        refuse — loudly, and saying why."""
        with pytest.raises(DsnStillCarriesPassword) as exc:
            psql_dsn_and_env('postgresql://u@h/db?sslpassword=s3cr3t')

        assert 'sslpassword' in str(exc.value)
        assert 'no environment variable' in str(exc.value)

    def test_the_last_password_parameter_wins(self):
        """libpq applies a repeated keyword's later value, so
        `?password=old&password=new` connects as `new`. Keeping the first
        stripped both and used the one libpq would have discarded."""
        dsn, env = psql_dsn_and_env('postgresql://u@h/db?password=old&password=new')

        assert env['PGPASSWORD'] == 'new'
        assert 'old' not in dsn and 'new' not in dsn

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


class TestWhatCountsAsAUri:
    """Which strings take the URI path decides whether a credential is
    rewritten or waved through, and the two implementations have to agree on
    it. `postgis://` is this deployment's documented DSN scheme and must be
    rewritten; a keyword conninfo whose *value* merely contains `://` must not
    be, or the URI parse rebuilds it unchanged and both end guards miss it."""

    @pytest.mark.parametrize('url, expected_dsn', [
        ('postgis://u:pw@h:5432/db', 'postgis://u@h:5432/db'),
        ('postgresql://u:pw@h/db', 'postgresql://u@h/db'),
        ('postgres://u:pw@h/db', 'postgres://u@h/db'),
    ])
    def test_any_real_scheme_is_rewritten(self, url, expected_dsn):
        dsn, env = psql_dsn_and_env(url)

        assert dsn == expected_dsn
        assert env['PGPASSWORD'] == 'pw'

    def test_a_conninfo_value_containing_a_scheme_is_not_a_uri(self):
        """`password=p://w` is a keyword conninfo, not a URI. Reading it as one
        leaves it rebuilt identical to the input — credential included — and
        reported clean."""
        dsn, env = psql_dsn_and_env('host=h user=u password=p://w')

        assert dsn == 'host=h user=u'
        assert env['PGPASSWORD'] == 'p://w'

    @pytest.mark.parametrize('url', [
        'my_scheme://u:pw@h/db',   # `_` is not a scheme character
        '://u:pw@h/db',            # no scheme at all
    ])
    def test_a_shape_the_rewrite_declines_is_still_refused(self, url):
        """The backstop must not ask the same question as the rewrite: a string
        it declines to touch would otherwise be returned untouched *and*
        reported clean — the silent pass this module says cannot happen."""
        with pytest.raises(DsnStillCarriesPassword):
            psql_dsn_and_env(url)

    @pytest.mark.parametrize('url, expect_refusal', [
        ('postgis://u:pw@h/db', False),
        ('host=h user=u password=p://w', True),
        ('host=h dbname=d', False),
    ])
    def test_the_shell_twin_classifies_them_the_same_way(self, url, expect_refusal):
        helper = Path(settings.BASE_DIR) / 'docker' / 'psql_dsn.sh'
        result = subprocess.run(
            ['bash', '-c',
             f'source {helper}; psql_dsn_split {url!r} && echo "DSN=$PSQL_DSN"'],
            capture_output=True, text=True, timeout=30,
        )

        if expect_refusal:
            assert result.returncode != 0, result.stdout
        else:
            assert result.returncode == 0, result.stderr
        # Either way, what it hands back must not carry the credential.
        assert 'pw@' not in result.stdout
        assert 'password=p://w' not in result.stdout


class TestBothPasswordLocationsAtOnce:
    """libpq parses the userinfo first and then the query, and a later setting
    of a keyword replaces the earlier one — so a DSN carrying both connects
    with the *query* password. Taking the authority one stripped both and then
    authenticated with the value libpq would have discarded: a working DSN
    turned into a password failure by the fix meant to keep it working."""

    def test_the_query_password_wins(self):
        dsn, env = psql_dsn_and_env('postgresql://u:old@h/db?password=new')

        assert env['PGPASSWORD'] == 'new'
        assert 'old' not in dsn and 'new' not in dsn
        assert dsn == 'postgresql://u@h/db'


class TestSpellingsOfTheQueryPassword:
    """libpq percent-decodes parameter *names*, so `%70assword=` is
    `password=`. Matching the literal lowercase text reports success on a DSN
    whose credential is still in it."""

    @pytest.mark.parametrize('url', [
        'postgresql://u@h/db?password=s3cr3t',
        'postgresql://u@h/db?Password=s3cr3t',
        'postgresql://u@h/db?PASSWORD=s3cr3t',
        'postgresql://u@h/db?%70assword=s3cr3t',
        'postgresql://u@h/db?sslmode=require&Password=s3cr3t',
    ])
    def test_python_strips_every_spelling(self, url):
        dsn, env = psql_dsn_and_env(url)

        assert 's3cr3t' not in dsn
        assert env['PGPASSWORD'] == 's3cr3t'

    @pytest.mark.parametrize('url', [
        'postgresql://u@h/db?password=s3cr3t',
        'postgresql://u@h/db?Password=s3cr3t',
        'postgresql://u@h/db?%70assword=s3cr3t',
        'postgresql://u@h/db?sslmode=require&Password=s3cr3t',
    ])
    def test_the_shell_twin_refuses_every_spelling(self, url):
        """The shell twin refuses where Python strips — but it has to refuse
        the *same set*, or the container init keeps a credential the migrated
        management commands would have removed."""
        helper = Path(settings.BASE_DIR) / 'docker' / 'psql_dsn.sh'
        result = subprocess.run(
            ['bash', '-c',
             f'source {helper}; psql_dsn_split {url!r} && echo "DSN=$PSQL_DSN"'],
            capture_output=True, text=True, timeout=30,
        )

        assert result.returncode != 0, result.stdout
        assert 's3cr3t' not in result.stdout

    def test_the_shell_twin_still_accepts_an_ordinary_dsn(self):
        """The blunt rule must not refuse DSNs that carry no credential in the
        query — a refusal aborts container start."""
        helper = Path(settings.BASE_DIR) / 'docker' / 'psql_dsn.sh'
        result = subprocess.run(
            ['bash', '-c',
             f'source {helper}; '
             "psql_dsn_split 'postgresql://u:pw@h/db?sslmode=require' && "
             'echo "DSN=$PSQL_DSN PG=$PGPASSWORD"'],
            capture_output=True, text=True, timeout=30,
        )

        assert result.returncode == 0, result.stderr
        assert 'DSN=postgresql://u@h/db?sslmode=require' in result.stdout
        assert 'PG=pw' in result.stdout


class TestAUriWithNoDatabasePath:
    """`postgresql://user@host?password=secret` is a valid libpq URI, and the
    authority split ended at the first `/` — which a pathless URI has none of,
    so the query went into the authority, the strip never saw it, and neither
    did the `_assert_no_password` backstop. The credential reached psql in argv:
    the one outcome this module exists to prevent, on the shape most likely to
    appear in a hand-written DSN."""

    @pytest.mark.parametrize('url, expected_dsn', [
        ('postgresql://user@host?password=secret',
         'postgresql://user@host'),
        ('postgresql://user@host:5432?password=secret&sslmode=require',
         'postgresql://user@host:5432?sslmode=require'),
    ])
    def test_a_pathless_uri_still_loses_its_password(self, url, expected_dsn):
        dsn, env = psql_dsn_and_env(url)

        assert 'secret' not in dsn
        assert env['PGPASSWORD'] == 'secret'
        # The rest of the DSN has to survive: dropping `sslmode` here would turn
        # a credential fix into a silent downgrade of the connection.
        assert dsn == expected_dsn

    def test_a_question_mark_inside_the_password_is_not_a_query(self):
        """The query only terminates the authority *after* the userinfo — libpq
        reads `?` before the `@` as part of the password, and so must we, or a
        working credential is truncated into an authentication failure that
        looks like a bad password."""
        dsn, env = psql_dsn_and_env('postgresql://user:pa?ss@host/db')

        assert env['PGPASSWORD'] == 'pa?ss'
        assert dsn == 'postgresql://user@host/db'

    def test_a_pathless_uri_without_a_password_is_untouched(self):
        url = 'postgresql://user@host:5432?sslmode=require'
        dsn, env = psql_dsn_and_env(url)

        assert dsn == url
        assert 'PGPASSWORD' not in env


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

    # Every shape where the two have drifted, plus the ones that must keep
    # working. Because bash refuses where Python strips, the only invariant
    # statable across both is the one that matters: what comes back must not
    # carry a credential, and a DSN carrying none must not be refused.
    SECRET = 's3cr3t'
    CREDENTIALED = [
        'postgresql://u:s3cr3t@h/db',
        '  postgresql://u:s3cr3t@h/db',      # lstrip: Python did, bash did not
        '\tpostgresql://u:s3cr3t@h/db',
        'POSTGRESQL://u:s3cr3t@h/db',
        'postgis://u:s3cr3t@h/db',
        'my_scheme://u:s3cr3t@h/db',         # rewrite declines it; backstop must not
        '://u:s3cr3t@h/db',                  # no scheme at all
        'postgresql://u@h/db?password=s3cr3t',
        ' postgresql://u@h/db?password=s3cr3t',
        'postgresql://u@h/db?Password=s3cr3t',
        'postgresql://u@h/db?%70assword=s3cr3t',
        'postgresql://u@h/db?sslpassword=s3cr3t',
        'host=h password=s3cr3t',
        'host=h PassWord=s3cr3t',            # libpq rejects it; argv still shows it
        'host=h sslpassword=s3cr3t',
        '\npostgresql://u:s3cr3t@h/db',      # a newline hid this from the check
        'postgresql://u@h/db?password%20=s3cr3t',   # libpq rejects; argv shows it
        'postgresql://u@h/db?PASSWORD=s3cr3t',
    ]
    CREDENTIAL_FREE = [
        'postgresql://u@h/db',
        'postgresql://u@h:5432/db?sslmode=require',
        'postgis://u@h/db',
        'postgresql:///db?host=/var/run/postgresql',
        'host=h dbname=d user=u',
        "host=h options='-c a://u:p@h'",     # `://` in a value is not an authority
        'host=h application_name=svc://alice@registry',
        'postgresql://u@h/db?passfile=%2Fsecrets%2F.pgpass',
        # An empty password is not a password — libpq parses no credential out
        # of these, and refusing them aborts container start on the realistic
        # `?password=${SECRET}` with SECRET unset.
        'postgresql://u@h/db?password=',
        'host=h password=',
        "host=h options='-c ?password=x'",   # `?` in a value is not a query
        'postgresql://u@h/dbname password=s3cr3t',  # libpq: that is the dbname
    ]

    @pytest.mark.parametrize('url', CREDENTIALED)
    def test_neither_implementation_hands_back_the_secret(self, url):
        """Refused or stripped, never returned. The one property both halves
        must share, on the inputs where they drifted apart."""
        code, out = self._split(url)
        # A refusal prints nothing, so there is no DSN to inspect — that is the
        # pass condition for the bash half, which refuses where Python strips.
        if code != 3:
            # Every line of the DSN, not just out[0]: the helper prints the
            # DSN and then the password, so a DSN containing a newline hides
            # everything after its first line — including, for
            # `\npostgresql://u:s3cr3t@h/db`, the credential itself. The last
            # line is PGPASSWORD, where the secret is SUPPOSED to be.
            returned_dsn = '\n'.join(out[:-1])
            assert self.SECRET not in returned_dsn, (
                f'bash returned the credential: {returned_dsn!r}'
            )

        try:
            py_dsn, _env = psql_dsn_and_env(url)
        except DsnStillCarriesPassword:
            return
        assert self.SECRET not in py_dsn, f'python returned the credential: {py_dsn!r}'

    @pytest.mark.parametrize('url', CREDENTIAL_FREE)
    def test_neither_implementation_refuses_a_clean_dsn(self, url):
        """A false refusal aborts container start — worse than what it guards.

        And the DSN has to come back intact. Asserting only "not refused" let a
        stub that returns an empty string for every input pass every case in
        this class: nothing checked that the connection string survived.
        """
        code, out = self._split(url)
        assert code != 3, f'bash refused a credential-free DSN: {url!r}'

        _py_dsn, py_env = psql_dsn_and_env(url)  # must not raise

        # bash rewrites nothing when there is nothing to strip, so its output
        # is the input. This is what a stub cannot satisfy — asserting only
        # "not refused" let one that returns an empty string pass every case.
        assert '\n'.join(out[:-1]) == url, f'bash altered a clean DSN: {out!r}'
        # Python may tidy an empty `password=` out of the query; what it must
        # not do is find a credential where libpq sees none.
        assert 'PGPASSWORD' not in py_env

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
