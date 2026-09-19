"""The disposable-database guard itself (test support, MVP 5 Stage 3).

The guard exists because pointing ``HARBOR_TEST_DATABASE_URL`` at the development
database wrote six validation runs into the database the dashboard reads. A guard
that is only exercised by accident is worth nothing, so its comparison rule is
pinned here: two spellings of the same target must be recognised as the same
database, and the skip message must never carry a password.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from db_guard import (
    DEV_DATABASE_URL_ENV,
    OVERRIDE_ENV,
    TEST_DATABASE_URL_ENV,
    describe_target,
    disposable_database_url,
    is_development_database,
    skip_reason,
)

DEV_URL = "postgresql+psycopg://harbor:hunter2@localhost:5433/harbor"
DISPOSABLE_URL = "postgresql://harbor:hunter2@localhost:5433/harbor_test"


def env(**values: str) -> dict[str, str]:
    """Only the variables the guard reads, so a stray shell value cannot leak in.

    ``DATABASE_URL`` defaults to the development URL: a test that expects the guard
    to fire has to be able to omit it, which is what makes the unset case worth
    spelling out separately.
    """
    return {
        DEV_DATABASE_URL_ENV: DEV_URL,
        TEST_DATABASE_URL_ENV: "",
        OVERRIDE_ENV: "",
    } | values


class IsDevelopmentDatabaseTests(unittest.TestCase):
    def test_a_credential_or_driver_difference_does_not_hide_the_same_target(self) -> None:
        for spelling in (
            "postgresql://harbor:different-password@localhost:5433/harbor",
            "postgresql+psycopg2://harbor@localhost:5433/harbor",
            "postgres://harbor@localhost:5433/harbor",
        ):
            with self.subTest(spelling=spelling), patch.dict("os.environ", env(), clear=True):
                self.assertTrue(is_development_database(spelling))

    def test_a_loopback_alias_is_the_same_server(self) -> None:
        # A false positive is recoverable — the override exists — while a false
        # negative pollutes the dashboard, so loopback spellings count as one host.
        # IPv6 has to be bracketed to be a valid URL, and parses to a bare `::1`.
        for spelled in (
            DEV_URL.replace("localhost", "127.0.0.1"),
            DEV_URL.replace("localhost", "[::1]"),
        ):
            with self.subTest(spelled=spelled), patch.dict("os.environ", env(), clear=True):
                self.assertTrue(is_development_database(spelled))

    def test_a_default_port_matches_an_explicit_default_port(self) -> None:
        with patch.dict(
            "os.environ",
            env(**{DEV_DATABASE_URL_ENV: "postgresql://harbor@localhost/harbor"}),
            clear=True,
        ):
            self.assertTrue(is_development_database("postgresql://harbor@localhost:5432/harbor"))

    def test_another_database_on_the_same_server_is_allowed(self) -> None:
        with patch.dict("os.environ", env(), clear=True):
            self.assertFalse(is_development_database(DISPOSABLE_URL))

    def test_another_server_is_allowed(self) -> None:
        with patch.dict("os.environ", env(), clear=True):
            self.assertFalse(is_development_database("postgresql://harbor@db-2:5433/harbor"))

    def test_without_a_development_url_nothing_is_blocked(self) -> None:
        with patch.dict("os.environ", env(**{DEV_DATABASE_URL_ENV: ""}), clear=True):
            self.assertFalse(is_development_database(DEV_URL))

    def test_an_unparseable_url_is_not_treated_as_the_development_database(self) -> None:
        with patch.dict("os.environ", env(), clear=True):
            self.assertFalse(is_development_database("definitely not a url"))


class DisposableDatabaseUrlTests(unittest.TestCase):
    def test_unset_means_the_suites_must_not_run(self) -> None:
        with patch.dict("os.environ", env(), clear=True):
            self.assertIsNone(disposable_database_url())

    def test_the_development_database_is_refused(self) -> None:
        with patch.dict("os.environ", env(**{TEST_DATABASE_URL_ENV: DEV_URL}), clear=True):
            self.assertIsNone(disposable_database_url())

    def test_a_disposable_database_is_returned_unchanged(self) -> None:
        with patch.dict("os.environ", env(**{TEST_DATABASE_URL_ENV: DISPOSABLE_URL}), clear=True):
            self.assertEqual(disposable_database_url(), DISPOSABLE_URL)

    def test_the_override_makes_writing_to_development_an_explicit_choice(self) -> None:
        with patch.dict(
            "os.environ",
            env(**{TEST_DATABASE_URL_ENV: DEV_URL, OVERRIDE_ENV: "1"}),
            clear=True,
        ):
            self.assertEqual(disposable_database_url(), DEV_URL)
            self.assertIsNone(skip_reason("the migration suite"))


class SkipReasonTests(unittest.TestCase):
    def test_an_unset_variable_asks_for_one(self) -> None:
        with patch.dict("os.environ", env(), clear=True):
            reason = skip_reason("the migration suite")

        assert reason is not None
        self.assertIn(TEST_DATABASE_URL_ENV, reason)
        self.assertIn("the migration suite", reason)

    def test_the_development_database_is_named_without_its_password(self) -> None:
        with patch.dict("os.environ", env(**{TEST_DATABASE_URL_ENV: DEV_URL}), clear=True):
            reason = skip_reason("the migration suite")

        assert reason is not None
        self.assertIn("localhost:5433/harbor", reason)
        self.assertIn(OVERRIDE_ENV, reason)
        self.assertNotIn("hunter2", reason)

    def test_a_disposable_database_is_not_refused(self) -> None:
        with patch.dict("os.environ", env(**{TEST_DATABASE_URL_ENV: DISPOSABLE_URL}), clear=True):
            self.assertIsNone(skip_reason("the migration suite"))


class DescribeTargetTests(unittest.TestCase):
    def test_it_renders_host_port_and_database_only(self) -> None:
        self.assertEqual(describe_target(DEV_URL), "localhost:5433/harbor")

    def test_an_unparseable_url_says_so(self) -> None:
        self.assertEqual(describe_target("definitely not a url"), "an unparseable URL")


if __name__ == "__main__":
    unittest.main()
