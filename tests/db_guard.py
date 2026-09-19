"""Which database a write-gated suite is allowed to touch (test support).

``HARBOR_TEST_DATABASE_URL`` has been documented as pointing at a *disposable*
database since MVP 1, but nothing enforced it. The gap was not theoretical: during
MVP 5 Stage 3 it was pointed at the development database, and the write-gated
suites put six validation runs into the very database the dashboard reads — rows
that then appeared in the UI as if a human had created them. Worse, several of
these suites run ``alembic upgrade``/``downgrade``, so the same mistake can move
the development schema backwards.

So the rule is enforced here instead of in a docstring: a suite that writes may
only run when its URL addresses a database other than the development one. The
failure is a **skip with an actionable reason** rather than an error, because the
correct response really is "point this somewhere else" — and because a skip is
visible in the summary instead of being buried in a traceback.

Suites that only *read* are deliberately not guarded: reading the development
database is how the SQL the dashboard depends on gets verified, and blocking that
would remove real coverage. Only add the guard to a suite that inserts, updates,
deletes or migrates.

The escape hatch exists for the deliberate case (a fresh throwaway development
database, say) and is a separate variable on purpose: a leak has to be an explicit
choice, never a default.
"""

from __future__ import annotations

import os

from sqlalchemy.engine import make_url

#: The database the application (and therefore the dashboard) reads.
DEV_DATABASE_URL_ENV = "DATABASE_URL"

#: The database a write-gated suite may use.
TEST_DATABASE_URL_ENV = "HARBOR_TEST_DATABASE_URL"

#: Set to ``1`` to allow writes against the development database on purpose.
OVERRIDE_ENV = "HARBOR_ALLOW_DEV_DATABASE_WRITES"

_DEFAULT_PORTS = {"postgresql": 5432, "postgres": 5432}

#: Spellings of "this machine" collapsed into one host. Two names for loopback can
#: address different servers only in contrived setups, while missing the match
#: costs a polluted dashboard; the asymmetry is why the guard errs towards
#: blocking and why the override exists.
_LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]"}


def _target(url: str) -> tuple[str | None, int | None, str | None] | None:
    """Return ``(host, port, database)`` for a URL, or ``None`` when unparseable.

    The password is never part of the identity that is compared, so the guard
    cannot leak a credential into a skip message.
    """
    try:
        parsed = make_url(url)
    except Exception:  # noqa: BLE001 - any parse failure means "cannot compare"
        return None
    host = parsed.host
    if host is not None and host.lower() in _LOOPBACK:
        host = "loopback"
    port = parsed.port
    if port is None:
        port = _DEFAULT_PORTS.get(parsed.get_backend_name() or "")
    return host, port, parsed.database


def disposable_database_url() -> str | None:
    """The URL a write-gated suite may use, or ``None`` when it must not run."""
    url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not url:
        return None
    if is_development_database(url):
        return None
    return url


def is_development_database(url: str) -> bool:
    """Whether ``url`` addresses the same database as ``DATABASE_URL``.

    Compares host, port and database name rather than the raw strings: the two
    variables routinely spell the same target differently (driver suffix, an
    explicit default port, a localhost alias), and a string comparison would
    declare them different and let the write through.
    """
    dev_url = os.environ.get(DEV_DATABASE_URL_ENV)
    if not dev_url or os.environ.get(OVERRIDE_ENV) == "1":
        return False
    target = _target(url)
    return target is not None and target == _target(dev_url)


def describe_target(url: str) -> str:
    """Render a URL as ``host:port/database`` for a message, never with secrets.

    Deliberately shows the host as the caller spelled it (``localhost``, not the
    normalised ``loopback``): the message has to match what the reader typed to be
    recognisable, even though the comparison uses the normalised form.
    """
    try:
        parsed = make_url(url)
    except Exception:  # noqa: BLE001 - only used for a human-readable message
        return "an unparseable URL"
    host = parsed.host or "?"
    port = parsed.port if parsed.port is not None else "?"
    return f"{host}:{port}/{parsed.database or '?'}"


def skip_reason(component: str) -> str | None:
    """Why ``component`` must not run, or ``None`` when it may.

    Args:
        component: What is being gated, named for the reader (e.g. ``"the
            migration suite"``), so the skip line says which suite was held back.
    """
    url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not url:
        return f"Set {TEST_DATABASE_URL_ENV} to run {component}."
    if is_development_database(url):
        return (
            f"{TEST_DATABASE_URL_ENV} = {describe_target(url)} is the development "
            f"database; {component} writes, so point it at a disposable one "
            f"(createdb harbor_test) or set {OVERRIDE_ENV}=1 on purpose."
        )
    return None
