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

    PSQL_DSN="$dsn"
    [ -n "$dsn" ] || return 0

    # `tr` rather than ${dsn,,}: the latter needs bash 4+, and keeping this
    # portable is what makes the function testable outside the container.
    local lowered
    lowered="$(printf '%s' "$dsn" | tr '[:upper:]' '[:lower:]')"
    case "$lowered" in
        postgres://*|postgresql://*) ;;
        *)
            # Keyword conninfo. Not handled here -- refuse if it carries one.
            if [[ "$dsn" =~ (^|[[:space:]])password[[:space:]]*= ]]; then
                _psql_dsn_refuse "keyword conninfo carries password=" || return 1
            fi
            return 0
            ;;
    esac

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

    # Same check the Python twin makes, on the value actually about to be used.
    if [[ "$PSQL_DSN" =~ ^[^:]+://[^/]*:[^/]*@ ]]; then
        _psql_dsn_refuse "authority still contains a password" || return 1
    fi
    if [[ "$PSQL_DSN" =~ [?\&](password|sslpassword)= ]]; then
        _psql_dsn_refuse "query string still contains a password" || return 1
    fi
    return 0
}
