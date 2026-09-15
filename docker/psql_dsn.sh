#!/usr/bin/env bash
# Move a password out of a libpq DSN and into PGPASSWORD (#403).
#
# Sourced by the init scripts, which shell out to psql with a connection string
# carrying the patient- or trials-DB password. Command-line arguments are
# world-readable: any local process can read /proc/<pid>/cmdline, and `ps` shows
# argv to every user on the host. These scripts run unattended in the deployed
# container, so this is the exposure that matters most -- more than the
# hand-run analysis commands fixed alongside them.
#
# Usage:
#   source "$(dirname "${BASH_SOURCE[0]}")/psql_dsn.sh"
#   psql_dsn_split "$SOME_DATABASE_URL"    # sets PSQL_DSN, exports PGPASSWORD
#   psql "$PSQL_DSN" ...
#
# It assigns rather than echoes on purpose. A `dsn="$(fn "$url")"` form runs the
# function in a command-substitution subshell, so `export PGPASSWORD` there
# never reaches the caller: the DSN would come back stripped and the password
# would be gone entirely, turning a credential leak into an auth failure.
#
# Coverage, stated so the callers' comments do not claim more: this handles the
# URI form `scheme://user:password@host/db`. `?password=` in the query and
# keyword conninfo (`host=h password=s`) are NOT handled -- psql_dsn_split
# refuses them loudly rather than passing a credentialed string through as if
# it were clean. The Python twin (trials/services/psql_dsn.py) handles all
# three; this one covers what the deployment actually uses and fails closed on
# the rest.

# Percent-decode via python3 rather than `printf '%b' "${1//%/\x}"`.
# That expansion is not a percent decoder: it also interprets backslash escapes
# already present in the password, so `new\nline` becomes a real newline,
# `back\slash` loses its backslash, and `\c` truncates the credential outright
# with no error. A bare `%` not followed by two hex digits makes printf write to
# stderr. python3 is already in this image and is what the Python twin uses, so
# the two implementations decode identically by construction.
_psql_dsn_percent_decode() {
    python3 -c 'import sys,urllib.parse;sys.stdout.write(urllib.parse.unquote(sys.argv[1]))' "$1"
}

# Fail closed: a DSN that still carries a credential must stop the script, not
# be handed to psql while the caller believes it was cleaned.
_psql_dsn_refuse() {
    echo "[psql_dsn] ERROR: $1" >&2
    echo "[psql_dsn] refusing to run psql with a credential in argv (#403)." >&2
    return 1
}

psql_dsn_split() {
    local dsn="$1"
    local scheme rest authority tail userinfo hostinfo user password
    local _psql_dsn_is_uri=0

    PSQL_DSN="$dsn"
    [ -n "$dsn" ] || return 0

    # `tr` rather than ${dsn,,}: the latter needs bash 4+, and keeping this
    # portable is what makes the function testable outside the container.
    local lowered
    lowered="$(printf '%s' "$dsn" | tr '[:upper:]' '[:lower:]')"
    # Any scheme with an authority, not just the two libpq connects to:
    # `postgis://` is this deployment's convention, and treating it as keyword
    # conninfo let a URI-shaped credential through untouched.
    #
    # ANCHORED, and matching the Python twin's `[a-z][a-z0-9+.-]*://` exactly.
    # An unanchored `*://*` glob reads `host=h user=u password=p://w` -- a
    # keyword conninfo whose *value* happens to contain `://` -- as a URI:
    # `${dsn%%://*}` then swallows the left half, no `@` is found, the DSN is
    # rebuilt identical to the input, and both end guards miss it because
    # neither pattern matches. Returned clean, password and all, straight into
    # psql's argv. Fail-open in the function whose whole job is to fail closed.
    #
    # Leading whitespace is trimmed before the test, because the Python twin
    # lstrips and an anchored pattern that does not is a different predicate on
    # the same input: one space in front of a DSN sent it down the keyword
    # branch, whose only check looks for a `password=` token that a URI
    # userinfo does not contain -- so `  postgresql://u:s3cr3t@h/db` came back
    # untouched and was reported clean. Measured against real psql: the
    # credential then reached argv AND the container log, via the probe's own
    # error handler printing the DSN psql rejected.
    local trimmed
    trimmed="${lowered#"${lowered%%[![:space:]]*}"}"
    if [[ "$trimmed" =~ ^[a-z][a-z0-9+.-]*:// ]]; then
        _psql_dsn_is_uri=1
    else
        _psql_dsn_is_uri=0
        # Keyword conninfo. Not handled here -- refuse if it carries one.
        # Case-insensitive: libpq's own lookup is strcmp, so `PassWord=` is not
        # a credential it would accept -- but it is still a secret, and argv
        # exposure on a connection libpq refuses is exactly the harm this
        # module exists to prevent.
        if [[ "$lowered" =~ (^|[[:space:]])(password|sslpassword)[[:space:]]*= ]]; then
            _psql_dsn_refuse "keyword conninfo carries password=" || return 1
        fi
    fi

    if [ "$_psql_dsn_is_uri" = "1" ]; then
    scheme="${dsn%%://*}"
    rest="${dsn#*://}"

    # The authority ends at the first '/'. Splitting on the whole remainder
    # would let a '@' in the PATH decide the split: for
    # postgresql://reader:s@h:5432/pat@ients the host became "ients" and the
    # password became garbage -- and a DSN with no password at all was rewritten
    # into an unusable one with a bogus PGPASSWORD exported.
    if [[ "$rest" == */* ]]; then
        authority="${rest%%/*}"
        tail="/${rest#*/}"
    else
        authority="$rest"
        tail=""
    fi

    # The last '@' of the authority separates userinfo from host, so a '@'
    # inside the password does not truncate the host.
    if [[ "$authority" == *"@"* ]]; then
        userinfo="${authority%@*}"
        hostinfo="${authority##*@}"
        if [[ "$userinfo" == *":"* ]]; then
            user="${userinfo%%:*}"
            password="${userinfo#*:}"
            if [ -n "$password" ]; then
                PGPASSWORD="$(_psql_dsn_percent_decode "$password")"
                export PGPASSWORD
            fi
            if [ -n "$user" ]; then
                authority="$user@$hostinfo"
            else
                authority="$hostinfo"
            fi
        fi
    fi

    PSQL_DSN="$scheme://$authority$tail"
    fi

    # Guards, for EVERY shape -- not only the ones the rewrite above handled.
    # They used to live inside the URI branch, so a string the classifier
    # declined (`my_scheme://u:pw@h/db`, `://u:pw@h`) was returned unexamined:
    # narrowing the classifier without an unconditional backstop moves the
    # fail-open rather than closing it. The Python twin ungated its own
    # `_assert_no_password` for the same reason.
    #
    # Same check the Python twin makes, on the value actually about to be used.
    # URI-shaped enough to have an authority: what the rewrite accepts, plus
    # the near-misses it declines (invalid scheme, or none) -- but NOT keyword
    # conninfo, where a `://` inside a value is not an authority. libpq decides
    # the same way, by what comes first; so does the Python twin's
    # `_looks_like_uri`. Without this, `host=h options='-c a://u:p@h'` -- a DSN
    # carrying no credential at all -- was refused, and a refusal here aborts
    # container start.
    local guard_target guard_head
    guard_target="$(printf '%s' "$PSQL_DSN" | tr '[:upper:]' '[:lower:]')"
    guard_target="${guard_target#"${guard_target%%[![:space:]]*}"}"
    guard_head="${guard_target%%=*}"
    if [[ "$guard_target" != *=* || "$guard_head" == *://* ]]; then
        # `[^:]*`, not `[^:]+`: an empty scheme (`://u:pw@h/db`) is not a DSN
        # the rewrite touches, and requiring a character before `://` let
        # precisely that shape past the guard meant to catch what it skipped.
        if [[ "$guard_target" =~ ^[^:]*://[^/]*:[^/]*@ ]]; then
            _psql_dsn_refuse "authority still contains a password" || return 1
        fi
    fi
    # Case-insensitively, and over percent-encoded spellings: libpq decodes
    # parameter *names*, so `%70assword=` is `password=`, and matching the
    # literal lowercase text let both that and `?Password=` through with the
    # credential still in the DSN — the exact exposure this refuses. The
    # Python twin strips both (it decodes the key), so a literal match here
    # also left the two disagreeing about the same input.
    #
    # Decoding in bash is the part worth not attempting: the obvious
    # `printf '%b' "${k//%/\\x}"` also interprets backslash escapes, so it
    # mangles keys it should pass through. Since this function refuses rather
    # than strips, it can be blunt instead: any `%` in a parameter *name* is
    # itself grounds to refuse, which covers every encoding of `password`
    # without decoding anything.
    local query_lower
    query_lower="$(printf '%s' "$PSQL_DSN" | tr '[:upper:]' '[:lower:]')"
    if [[ "$query_lower" =~ [?\&](password|sslpassword)= ]]; then
        _psql_dsn_refuse "query string still contains a password" || return 1
    fi
    if [[ "$query_lower" =~ [?\&][^=\&]*%[^=\&]*= ]]; then
        _psql_dsn_refuse "query string has a percent-encoded parameter name" || return 1
    fi
    return 0
}
