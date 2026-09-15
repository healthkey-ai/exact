"""Keep the patient-DB password out of the psql command line (#403).

Command-line arguments are world-readable: any local process can read
`/proc/<pid>/cmdline`, `ps` shows argv to every user on the host, and argv
lands in shell history and in process-accounting or container-runtime logs.
The credential in question opens the patient database.

libpq reads `PGPASSWORD` from the environment instead, and `/proc/<pid>/environ`
is owner-only on Linux. Upstream does not call `PGPASSWORD` the *best* answer --
the PostgreSQL docs note that some operating systems let other users read a
process environment -- and #403 lists `.pgpass` and `PGSERVICEFILE` alongside
it. `PGPASSWORD` is chosen here because these commands already build a custom
env for `PGSSLMODE`, so it needs no new file to deploy or permission to police;
if a host is ever found where the environment is readable, the same seam moves
to `.pgpass` without touching a call site.

This module is the one place the split happens, because the call sites are
copies of the same helper: fixing one and leaving the others is how a ticket
reads as closed while the credential is still in argv on the other paths.
"""
from __future__ import annotations

import os
import re
from urllib.parse import unquote, urlsplit, urlunsplit

# `password=` as a libpq keyword-conninfo token, e.g.
# "host=h dbname=d user=u password=s3cr3t". Values may be single-quoted.
_KEYWORD_PASSWORD = re.compile(
    r"(?:^|\s)password\s*=\s*(?:'((?:[^'\\]|\\.)*)'|(\S*))"
)
# Query parameters that carry a credential. `sslpassword` is the passphrase for
# the client key and is just as much a secret as `password`.
_PASSWORD_QUERY_KEYS = {"password", "sslpassword"}


class DsnStillCarriesPassword(ValueError):
    """Raised when a credential could not be moved out of the DSN.

    Failing closed matters more than usual here: the whole point of this module
    is that the call sites can then say "the password is not in argv". If an
    unrecognised DSN form were passed through untouched, that sentence would be
    false exactly where it mattered, and nothing would say so.
    """


def psql_dsn_and_env(db_url: str, **extra_env: str) -> tuple[str, dict[str, str]]:
    """Split *db_url* into an argv-safe DSN and an env carrying the password.

    Returns `(dsn, env)` where `dsn` has no credential in it and `env` is a copy
    of `os.environ` with `PGPASSWORD` set when there was one, plus any
    `extra_env` the caller needs (`PGSSLMODE`, typically).

    Handles the three forms psql accepts: a URI with `user:password@`, a URI
    with `?password=`, and keyword conninfo with a `password=` token. A DSN with
    no password is returned unchanged.

    Raises `DsnStillCarriesPassword` if a password survives the rewrite, rather
    than returning something the caller would describe as safe.
    """
    env = {**os.environ, **extra_env}
    if not db_url:
        return db_url, env

    if _is_uri(db_url):
        dsn, password = _strip_uri_password(db_url)
    else:
        # A keyword value may itself contain "://" (options, a comment), so the
        # form is decided by the scheme, not by a substring search.
        dsn, password = _strip_keyword_password(db_url)

    # An empty password is not a password. `postgres://u:@h/db` and
    # `postgres://u@h/db` mean the same thing to libpq, but an *empty*
    # PGPASSWORD does not mean the same as an unset one, so the credential is
    # still stripped from the DSN while the variable is left alone.
    if password:
        env["PGPASSWORD"] = password

    _assert_no_password(dsn)
    return dsn, env


# libpq itself only connects to `postgres://` and `postgresql://`, but this
# codebase hands these URLs to Django through `dj_database_url`, which accepts
# more — `postgis://` is the documented deploy convention for the shared Cloud
# SQL instance (see `exact/settings.py`). Any RFC-3986 scheme, then; a string
# with `://` but no valid scheme (`://u:pw@h`, `my_scheme://…`) is still not
# rewritten, which is why `_assert_no_password` no longer asks this question
# before deciding whether to look. Scheme-matching on the libpq pair left
# every other scheme falling through to the keyword-conninfo branch, where a
# URI-shaped credential contains no `password=` token: the URL came back
# unchanged, password included, and `_assert_no_password` waved it through on
# the same test. Reported clean, sent to psql in argv. libpq would refuse to
# connect, but the credential is in `/proc/<pid>/cmdline` for as long as the
# child lives, which is the whole thing this module prevents. Anything with an
# authority is now treated as a URI.
_URI_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*://", re.IGNORECASE)


def _is_uri(db_url: str) -> bool:
    return _URI_SCHEME.match(db_url.lstrip()) is not None


def _split_authority(db_url: str) -> tuple[str, str, str]:
    """Return `(prefix, authority, tail)` by scanning the raw string.

    `urlsplit` must not be used for this. It ends the netloc at the first `#`
    or `?`, so `postgresql://u:pa#ss@h/db` gives it a netloc of `u:pa` -- no
    `@`, nothing to strip -- and the credentialed URL is handed back untouched.
    libpq does not stop there: verified against psql 17.2, that DSN connects to
    host `h` with the password `pa#ss`. Reading the authority as "everything up
    to the first `/`" matches libpq and keeps `#` and `?` inside the password
    where they belong.
    """
    scheme, sep, rest = db_url.partition("://")
    prefix = f"{scheme}{sep}"
    slash = rest.find("/")
    if slash != -1:
        return prefix, rest[:slash], rest[slash:]

    # No database path -- but `postgresql://user@host?password=secret` is a
    # valid libpq URI, and treating its query as part of the authority left the
    # credential in the returned DSN: nothing downstream looked at a tail that
    # was never produced, so neither the strip nor the `_assert_no_password`
    # backstop saw the password, and it went to psql in argv. Exactly the leak
    # this module exists to prevent.
    #
    # The query is only a terminator *after* the userinfo, for the same reason
    # the authority runs to the first `/`: a password may legitimately contain
    # `?`, and libpq reads it as part of the credential rather than as the start
    # of a parameter list.
    at = rest.rfind("@")
    question = rest.find("?", at + 1)
    if question == -1:
        return prefix, rest, ""
    return prefix, rest[:question], rest[question:]


def _strip_uri_password(db_url: str) -> tuple[str, str | None]:
    """Remove `user:password@` and `?password=` from a URI-form DSN.

    The authority is edited as text rather than rebuilt from `urlsplit`'s
    parsed components. Rebuilding loses information: `.hostname` strips the
    brackets from an IPv6 literal, turning `[2001:db8::1]:5432` into
    `2001:db8::1:5432`, which psql reads as a port of `db8::1:5432`; and
    `.port` raises `ValueError` on the comma-separated multi-host URIs libpq
    supports. Both were working configurations that a component rebuild breaks
    into what looks like a credential problem.
    """
    prefix, authority, tail = _split_authority(db_url)

    password: str | None = None
    # The last `@` in the authority separates userinfo from host, so a `@`
    # inside the password does not truncate the host.
    userinfo, at, hostinfo = authority.rpartition("@")
    if at and ":" in userinfo:
        user, _, raw_password = userinfo.partition(":")
        # `urlsplit`-style components hand back the raw, still percent-encoded
        # substring, while libpq wants the literal password. Without `unquote`
        # a password containing @ : / ? or a space authenticates as its encoded
        # form and fails, and a literal '%40' is sent as '@' -- a silent
        # authentication break that looks like a bad credential rather than a
        # bad fix. The username stays encoded, because it goes back into a URI
        # and libpq decodes it itself.
        password = unquote(raw_password)
        authority = f"{user}@{hostinfo}" if user else hostinfo

    path, question, query = tail.partition("?")
    if question:
        query, query_password = _strip_query_password(query)
        if query_password is not None:
            # libpq parses the userinfo first and then the query, and a later
            # setting of the same keyword replaces the earlier one — so with
            # `postgresql://u:old@h/db?password=new` it connects as `new`.
            # Keeping `old` here stripped both and then authenticated with the
            # one libpq would have discarded: a working DSN turned into a
            # password failure by the fix meant to leave it working.
            password = query_password
        tail = f"{path}?{query}" if query else path

    return f"{prefix}{authority}{tail}", password


def _strip_query_password(query: str) -> tuple[str, str | None]:
    """Drop every `password=` parameter, decoding percent-encoded keys.

    libpq percent-decodes parameter *names* as well as values, so `%70assword=`
    is `password=`. Matching the literal text would let that through while this
    module reported success.
    """
    kept: list[str] = []
    password: str | None = None
    for pair in query.split("&"):
        if not pair:
            continue
        raw_key, _, raw_value = pair.partition("=")
        if unquote(raw_key).lower() in _PASSWORD_QUERY_KEYS:
            if password is None:
                password = unquote(raw_value)
            continue
        kept.append(pair)
    return "&".join(kept), password


def _strip_keyword_password(db_url: str) -> tuple[str, str | None]:
    """Remove the `password=` token from libpq keyword conninfo."""
    match = _KEYWORD_PASSWORD.search(db_url)
    if not match:
        return db_url, None

    quoted, bare = match.group(1), match.group(2)
    password = quoted.replace("\\'", "'").replace("\\\\", "\\") if quoted is not None else bare
    stripped = (db_url[: match.start()] + " " + db_url[match.end():]).strip()
    return re.sub(r"\s+", " ", stripped), password


def _assert_no_password(dsn: str) -> None:
    """Refuse to hand back a DSN that still carries a credential.

    Uses the same raw-authority scan as the rewrite, not `urlsplit`. An
    assertion that re-derives the authority differently from the code it checks
    would agree with it exactly where both are wrong -- which is how the `#`
    case passed silently.
    """
    # The URI scan runs on anything containing `://`, not only on what
    # `_is_uri` accepted for rewriting. Gating the *assertion* on the same
    # predicate as the rewrite means a string the rewrite declined to touch is
    # also never checked: `my_scheme://u:pw@h/db` came back unchanged, password
    # and all, and was reported clean. A backstop that trusts the same judgement
    # as the code it backs is not one.
    if "://" in dsn:
        _, authority, tail = _split_authority(dsn)
        userinfo, at, _ = authority.rpartition("@")
        if at and ":" in userinfo:
            raise DsnStillCarriesPassword("authority still contains a password")
        _, question, query = tail.partition("?")
        if question:
            for pair in query.split("&"):
                key, _, _value = pair.partition("=")
                if unquote(key).lower() in _PASSWORD_QUERY_KEYS:
                    raise DsnStillCarriesPassword(
                        f"query string still contains {unquote(key)}="
                    )

    # Checked for every shape, not just non-URIs: a keyword conninfo whose
    # value contains `://` is not a URI, and reaching the scan above by that
    # accident must not exempt it from this one.
    if _KEYWORD_PASSWORD.search(dsn):
        raise DsnStillCarriesPassword("conninfo still contains password=")
