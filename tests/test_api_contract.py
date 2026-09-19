"""API contract tests (MVP 5 / SP 5.11).

These tests pin the guarantees a dashboard — or any other client — may rely
on, and they are written against the *published* contract (the OpenAPI
document and the HTTP responses) rather than the internal route objects, so a
framework upgrade cannot silently change what clients observe.

Covered contract points:

* SP 5.3 — the route table is read-only: only ``GET``/``HEAD``/``OPTIONS``.
* SP 5.2 — data routes live under a versioned prefix.
* SP 5.4 — authentication is enforced *before* any database access.
* SP 5.5 — credentials never leave the process, whether named by a key or
  embedded in a value.
* SP 5.6 — collections are always paginated and ``limit`` is bounded.
* SP 5.7 — every failure is ``application/problem+json`` with a stable code
  and a correlatable request id.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import date, datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI

from harbor.api.app import READ_ONLY_METHODS, create_app
from harbor.api.config import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, ApiSettings
from harbor.api.deps import get_read_store
from harbor.api.errors import PROBLEM_MEDIA_TYPE
from harbor.api.redaction import redact_document
from harbor.api.schemas import API_VERSION

TOKEN = "read-token"
OPS_TOKEN = "ops-token"
BACKTEST_RUN_ID = "bt-run-1"
MISSING_RUN_ID = "does-not-exist"

WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")

SENSITIVE_VALUE = "sk-live-do-not-leak"
CREDENTIAL_VALUE = "ghp-do-not-leak"
DSN_PASSWORD = "hunter2"


def make_settings(**overrides: Any) -> ApiSettings:
    """Build settings without reading the developer's `.env` (hermetic tests)."""
    defaults: dict[str, Any] = {"token": TOKEN, "ops_token": OPS_TOKEN}
    return ApiSettings(_env_file=None, **(defaults | overrides))


def call(
    app: FastAPI,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Send one request to ``app`` over ASGI.

    The application is driven directly instead of through
    ``starlette.testclient`` so the suite does not depend on a deprecated
    shim. ``raise_app_exceptions`` is off so the generic 500 handler's
    response can be asserted (SP 5.7).
    """
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    async def _send() -> httpx.Response:
        async with httpx.AsyncClient(transport=transport, base_url="http://harbor.test") as client:
            return await client.request(method, path, headers=headers, **kwargs)

    return asyncio.run(_send())


def backtest_row() -> dict[str, Any]:
    """A backtest row shaped like the persisted columns, plus a secret-laden config."""
    return {
        "run_id": BACKTEST_RUN_ID,
        "status": "completed",
        "strategy": "momentum",
        "strategy_version": "1.0.0",
        "code_version": "abc1234",
        "config_hash": "hash-1",
        "data_cutoff": date(2026, 1, 2),
        "started_at": datetime(2026, 1, 2, 8, tzinfo=timezone.utc),
        "finished_at": datetime(2026, 1, 2, 9, tzinfo=timezone.utc),
        "error_summary": None,
        "resume_of": None,
        "config_snapshot": {
            "strategy": "momentum",
            "top_n": 10,
            "api_key": SENSITIVE_VALUE,
            "database_url": f"postgresql://harbor:{DSN_PASSWORD}@localhost:5433/harbor",
            "nested": {"token": CREDENTIAL_VALUE, "keep": "visible"},
        },
    }


def validation_row() -> dict[str, Any]:
    """A validation row shaped like the persisted columns."""
    return {
        "run_id": "val-1",
        "status": "completed",
        "code_version": "abc1234",
        "config_hash": "hash-2",
        "test_set_id": "oos-2026h1",
        "created_at": datetime(2026, 2, 1, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 2, 1, 1, tzinfo=timezone.utc),
        "error_summary": None,
        "config_snapshot": {"strategy": "momentum", "credential": SENSITIVE_VALUE},
    }


def conclusion_row() -> dict[str, Any]:
    """A recorded out-of-sample conclusion with its limitations (SP 3.58)."""
    return {
        "conclusion": "QUALIFIED",
        "rule_version": "oos-rule-1",
        "created_at": datetime(2026, 2, 1, 2, tzinfo=timezone.utc),
        "limitations": [{"code": "single_regime", "detail": "One regime only."}],
        "evidence": {"sharpe": 0.9},
    }


def paper_row() -> dict[str, Any]:
    """A paper-run row shaped like the persisted columns."""
    return {
        "run_id": "paper-1",
        "status": "stopped",
        "strategy": "momentum",
        "strategy_version": "1.0.0",
        "markets": ["HK", "US"],
        "base_currency": "HKD",
        "code_version": "abc1234",
        "dataset_fingerprint": "fp-1",
        "created_at": datetime(2026, 3, 1, tzinfo=timezone.utc),
        "started_at": datetime(2026, 3, 1, 1, tzinfo=timezone.utc),
        "stopped_at": datetime(2026, 3, 2, tzinfo=timezone.utc),
        "config_snapshot": {"strategy": "momentum"},
    }


def quality_row() -> dict[str, Any]:
    """A data-quality summary shaped like the persisted columns (SP 5.11)."""
    return {
        "market": "HK",
        "security_count": 12,
        "bar_count": 1000,
        "latest_bar_date": date(2026, 3, 1),
        "open_issue_count": 2,
        "issues_by_severity": {"high": 1, "low": 1},
        "latest_ingestion": {
            "run_id": "ing-1",
            "status": "completed",
            "source": "akshare",
            "start_time": datetime(2026, 3, 1, tzinfo=timezone.utc),
            "end_time": datetime(2026, 3, 1, 1, tzinfo=timezone.utc),
            "records_processed": 500,
        },
    }


class FakeReadStore:
    """An in-memory read store so the contract is tested without a database (SP 5.8).

    Every call is recorded, which lets a test prove that a refused request
    never reached the data layer.
    """

    def __init__(self, *, backtests: list[dict[str, Any]] | None = None) -> None:
        self.backtests = backtests if backtests is not None else [backtest_row()]
        self.calls: list[str] = []
        self.explode = False

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if self.explode:
            raise RuntimeError("simulated driver failure with a secret DSN")

    # -- backtests -------------------------------------------------------

    def list_backtest_runs(self, *, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        self._record("list_backtest_runs")
        return self.backtests[offset : offset + limit], len(self.backtests)

    def get_backtest_run(self, run_id: str) -> dict[str, Any] | None:
        self._record("get_backtest_run")
        return next((row for row in self.backtests if row["run_id"] == run_id), None)

    def backtest_run_stats(self, run_id: str) -> dict[str, int]:
        self._record("backtest_run_stats")
        return {
            "net_value_points": 2,
            "positions": 1,
            "fills": 3,
            "metrics": 4,
            "rejected_trades": 0,
        }

    # -- validations -----------------------------------------------------

    def list_validation_runs(self, *, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        self._record("list_validation_runs")
        rows = [validation_row()]
        return rows[offset : offset + limit], len(rows)

    def get_validation_run(self, run_id: str) -> dict[str, Any] | None:
        self._record("get_validation_run")
        return validation_row() if run_id == "val-1" else None

    def get_validation_manifest(self, run_id: str) -> dict[str, Any] | None:
        self._record("get_validation_manifest")
        return {"run_id": run_id, "fingerprint": "fp-oos-1"}

    def get_validation_conclusion(self, run_id: str) -> dict[str, Any] | None:
        self._record("get_validation_conclusion")
        return conclusion_row()

    def validation_warning_stats(self, run_id: str) -> tuple[int, dict[str, int]]:
        self._record("validation_warning_stats")
        return 3, {"high": 1, "medium": 2}

    # -- paper -----------------------------------------------------------

    def list_paper_runs(self, *, limit: int, offset: int) -> tuple[list[dict[str, Any]], int]:
        self._record("list_paper_runs")
        rows = [paper_row()]
        return rows[offset : offset + limit], len(rows)

    def get_paper_run(self, run_id: str) -> dict[str, Any] | None:
        self._record("get_paper_run")
        return paper_row() if run_id == "paper-1" else None

    def paper_run_stats(self, run_id: str) -> dict[str, int]:
        self._record("paper_run_stats")
        return {
            "orders": 5,
            "fills": 4,
            "net_value_points": 30,
            "approvals": 2,
            "circuit_breakers": 0,
            "reconciliation_differences": 1,
        }

    # -- quality ---------------------------------------------------------

    def quality_summary(self, market: str) -> dict[str, Any]:
        self._record("quality_summary")
        return quality_row() | {"market": market}


class ApiContractTestCase(unittest.TestCase):
    """Shared wiring: an app with a fake store and a valid read token."""

    def setUp(self) -> None:
        self.store = FakeReadStore()
        self.app = create_app(make_settings())
        self.app.dependency_overrides[get_read_store] = lambda: self.store

    def auth_call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send an authenticated request."""
        headers = {"Authorization": f"Bearer {TOKEN}", **kwargs.pop("headers", {})}
        return call(self.app, method, path, headers=headers, **kwargs)

    def anon_call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send a request with no credentials at all."""
        return call(self.app, method, path, **kwargs)


class ReadOnlyRouteTableTests(ApiContractTestCase):
    """SP 5.3 — the API can only read, and that is a property of its route table."""

    def test_only_read_methods_are_published(self) -> None:
        paths = self.app.openapi()["paths"]
        self.assertTrue(paths, "the API must publish at least one path")
        for path, operations in paths.items():
            for method in operations:
                self.assertIn(
                    method.upper(),
                    READ_ONLY_METHODS,
                    f"{method.upper()} {path} is a write-capable route; the API is read-only.",
                )

    def test_no_write_verb_is_accepted(self) -> None:
        for method in WRITE_METHODS:
            with self.subTest(method=method):
                response = self.auth_call(method, f"/api/{API_VERSION}/backtests")
                self.assertEqual(response.status_code, 405)

    def test_every_data_route_is_versioned(self) -> None:
        for path in self.app.openapi()["paths"]:
            if path == "/health":  # an unversioned liveness probe
                continue
            self.assertTrue(
                path.startswith(f"/api/{API_VERSION}/"),
                f"{path} is outside the versioned API surface.",
            )

    def test_the_schema_and_docs_are_versioned(self) -> None:
        self.assertEqual(self.anon_call("GET", f"/api/{API_VERSION}/openapi.json").status_code, 200)
        self.assertEqual(self.anon_call("GET", "/openapi.json").status_code, 404)
        self.assertEqual(self.anon_call("GET", f"/api/{API_VERSION}/docs").status_code, 200)


class AuthenticationTests(ApiContractTestCase):
    """SP 5.4 — authentication is mandatory, constant-time and checked first."""

    def test_missing_credentials_are_refused(self) -> None:
        response = self.anon_call("GET", f"/api/{API_VERSION}/backtests")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["content-type"], PROBLEM_MEDIA_TYPE)
        self.assertEqual(response.json()["code"], "missing_credentials")
        self.assertTrue(response.headers["WWW-Authenticate"].startswith("Bearer"))

    def test_invalid_token_is_refused(self) -> None:
        response = self.anon_call(
            "GET",
            f"/api/{API_VERSION}/backtests",
            headers={"Authorization": "Bearer wrong-token"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "invalid_credentials")

    def test_non_bearer_scheme_is_refused(self) -> None:
        response = self.anon_call(
            "GET",
            f"/api/{API_VERSION}/backtests",
            headers={"Authorization": f"Basic {TOKEN}"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "invalid_credentials")

    def test_empty_bearer_token_is_refused(self) -> None:
        response = self.anon_call(
            "GET", f"/api/{API_VERSION}/backtests", headers={"Authorization": "Bearer   "}
        )
        self.assertEqual(response.status_code, 401)

    def test_valid_token_is_accepted(self) -> None:
        response = self.auth_call("GET", f"/api/{API_VERSION}/backtests")
        self.assertEqual(response.status_code, 200)

    def test_authentication_precedes_database_access(self) -> None:
        """A refused request must never reach the data layer (SP 5.4)."""
        self.anon_call("GET", f"/api/{API_VERSION}/backtests")
        self.assertEqual(self.store.calls, [])

    def test_unauthenticated_mode_grants_the_readonly_role(self) -> None:
        app = create_app(make_settings(token=None, allow_unauthenticated=True))
        app.dependency_overrides[get_read_store] = lambda: self.store
        response = call(app, "GET", f"/api/{API_VERSION}/backtests")
        self.assertEqual(response.status_code, 200)

    def test_settings_refuse_to_default_to_unauthenticated(self) -> None:
        with self.assertRaises(ValueError):
            ApiSettings(_env_file=None)

    def test_settings_reject_a_default_above_the_maximum(self) -> None:
        with self.assertRaises(ValueError):
            make_settings(default_page_limit=MAX_PAGE_LIMIT + 1)


class PaginationContractTests(ApiContractTestCase):
    """SP 5.6 — collections are paginated and ``limit`` is always bounded."""

    def test_default_page_limit_is_applied(self) -> None:
        response = self.auth_call("GET", f"/api/{API_VERSION}/backtests")
        body = response.json()
        self.assertEqual(body["limit"], DEFAULT_PAGE_LIMIT)
        self.assertEqual(body["offset"], 0)
        self.assertEqual(body["total"], 1)
        self.assertIsNone(body["next_offset"])

    def test_page_envelope_reports_the_next_offset(self) -> None:
        self.store.backtests = [backtest_row() | {"run_id": f"bt-{index}"} for index in range(3)]
        first = self.auth_call("GET", f"/api/{API_VERSION}/backtests?limit=2").json()
        self.assertEqual(len(first["items"]), 2)
        self.assertEqual(first["total"], 3)
        self.assertEqual(first["next_offset"], 2)

        last = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests?limit=2&offset={first['next_offset']}"
        ).json()
        self.assertEqual(len(last["items"]), 1)
        self.assertIsNone(last["next_offset"])

    def test_limit_above_the_maximum_is_refused(self) -> None:
        response = self.auth_call("GET", f"/api/{API_VERSION}/backtests?limit={MAX_PAGE_LIMIT + 1}")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_page_limit")

    def test_unbounded_and_negative_pages_are_refused(self) -> None:
        for query, code in (
            ("limit=0", "invalid_page_limit"),
            ("limit=-1", "invalid_page_limit"),
            ("limit=abc", "invalid_request"),
            ("offset=-1", "invalid_request"),
            ("sort=%20", "invalid_sort"),
        ):
            with self.subTest(query=query):
                response = self.auth_call("GET", f"/api/{API_VERSION}/backtests?{query}")
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], code)

    def test_a_single_page_has_no_next_offset(self) -> None:
        body = self.auth_call("GET", f"/api/{API_VERSION}/backtests?limit=1").json()
        self.assertEqual(len(body["items"]), 1)
        self.assertIsNone(body["next_offset"])


class ErrorContractTests(ApiContractTestCase):
    """SP 5.7 — every failure is problem+json with a stable code and request id."""

    def test_a_missing_run_is_a_tagged_404(self) -> None:
        response = self.auth_call("GET", f"/api/{API_VERSION}/backtests/{MISSING_RUN_ID}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["content-type"], PROBLEM_MEDIA_TYPE)
        body = response.json()
        self.assertEqual(body["code"], "backtest_run_not_found")
        self.assertEqual(body["status"], 404)
        self.assertTrue(body["request_id"])
        self.assertEqual(response.headers["X-Request-ID"], body["request_id"])

    def test_a_client_request_id_is_honoured_and_echoed(self) -> None:
        supplied = "client-supplied-id-123"
        response = self.auth_call(
            "GET",
            f"/api/{API_VERSION}/backtests",
            headers={"X-Request-ID": supplied},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], supplied)

    def test_a_problem_response_always_carries_a_request_id(self) -> None:
        body = self.auth_call("GET", f"/api/{API_VERSION}/backtests/{MISSING_RUN_ID}").json()
        self.assertTrue(body["request_id"])
        self.assertTrue(body["type"].endswith("backtest_run_not_found"))
        self.assertTrue(body["detail"])

    def test_an_invalid_path_parameter_is_a_tagged_422(self) -> None:
        response = self.auth_call("GET", f"/api/{API_VERSION}/quality/XX")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_request")

    def test_a_missing_route_still_uses_the_problem_contract(self) -> None:
        response = self.auth_call("GET", f"/api/{API_VERSION}/no-such-collection")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["content-type"], PROBLEM_MEDIA_TYPE)
        self.assertIn("code", response.json())

    def test_an_unexpected_error_never_leaks_its_cause(self) -> None:
        self.store.explode = True
        response = self.auth_call("GET", f"/api/{API_VERSION}/backtests")
        self.assertEqual(response.status_code, 500)
        body = response.json()
        self.assertEqual(body["code"], "internal_error")
        self.assertNotIn("simulated driver failure", str(body))
        self.assertNotIn(DSN_PASSWORD, str(body))

    def test_health_is_reachable_without_credentials(self) -> None:
        response = self.anon_call("GET", "/health")
        self.assertEqual(response.status_code, 200)


class DatabaseAvailabilityTests(ApiContractTestCase):
    """SP 5.8 — an instance without a database degrades visibly, never silently."""

    def test_data_routes_report_503_without_an_engine(self) -> None:
        app = create_app(make_settings())
        response = call(
            app,
            "GET",
            f"/api/{API_VERSION}/backtests",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["code"], "database_unavailable")
        self.assertEqual(body["title"], "Service unavailable")

    def test_health_reports_database_readiness_separately(self) -> None:
        body = self.anon_call("GET", "/health").json()
        self.assertEqual(body["status"], "degraded")
        self.assertEqual(body["database"], "unavailable")
        self.assertTrue(body["read_only"])

    def test_version_reports_the_read_only_capability(self) -> None:
        body = self.auth_call("GET", f"/api/{API_VERSION}/version").json()
        self.assertEqual(body["api_version"], API_VERSION)
        self.assertTrue(body["read_only"])
        self.assertTrue(body["auth_required"])


class CollectionPayloadTests(ApiContractTestCase):
    """SP 5.2 — each collection serves the schema the dashboard renders."""

    def test_backtest_list_and_detail(self) -> None:
        listing = self.auth_call("GET", f"/api/{API_VERSION}/backtests").json()
        self.assertEqual(listing["items"][0]["run_id"], BACKTEST_RUN_ID)

        detail = self.auth_call("GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}").json()
        self.assertEqual(detail["run_id"], BACKTEST_RUN_ID)
        self.assertEqual(detail["counts"]["fills"], 3)

    def test_validation_detail_carries_the_conclusion_with_its_limitations(self) -> None:
        detail = self.auth_call("GET", f"/api/{API_VERSION}/validations/val-1").json()
        self.assertEqual(detail["dataset_fingerprint"], "fp-oos-1")
        self.assertEqual(detail["conclusion"]["conclusion"], "QUALIFIED")
        self.assertTrue(detail["conclusion"]["limitations"])
        self.assertEqual(detail["warning_count"], 3)
        self.assertEqual(detail["warnings_by_severity"], {"high": 1, "medium": 2})

    def test_paper_detail_exposes_counts(self) -> None:
        detail = self.auth_call("GET", f"/api/{API_VERSION}/paper-runs/paper-1").json()
        self.assertEqual(detail["counts"]["orders"], 5)
        self.assertEqual(detail["markets"], ["HK", "US"])

    def test_quality_summary_is_market_scoped(self) -> None:
        body = self.auth_call("GET", f"/api/{API_VERSION}/quality/HK").json()
        self.assertEqual(body["market"], "HK")
        self.assertEqual(body["open_issue_count"], 2)
        self.assertEqual(body["latest_ingestion"]["source"], "akshare")

    def test_a_missing_validation_or_paper_run_is_a_tagged_404(self) -> None:
        for collection, code in (
            ("validations", "validation_run_not_found"),
            ("paper-runs", "paper_run_not_found"),
        ):
            with self.subTest(collection=collection):
                response = self.auth_call(
                    "GET", f"/api/{API_VERSION}/{collection}/{MISSING_RUN_ID}"
                )
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["code"], code)


class RedactionTests(unittest.TestCase):
    """SP 5.5 — credentials are removed whether named by a key or embedded."""

    def test_sensitive_keys_are_masked_recursively(self) -> None:
        redacted = redact_document(backtest_row()["config_snapshot"])
        self.assertEqual(redacted["api_key"], "<redacted>")
        nested = redacted["nested"]
        self.assertIsInstance(nested, dict)
        assert isinstance(nested, dict)  # narrow for type checkers
        self.assertEqual(nested["token"], "<redacted>")
        self.assertEqual(nested["keep"], "visible")

    def test_a_password_embedded_in_a_url_is_scrubbed(self) -> None:
        redacted = redact_document(backtest_row()["config_snapshot"])
        rendered = str(redacted)
        self.assertNotIn(DSN_PASSWORD, rendered)
        self.assertIn("://<redacted>@", rendered)

    def test_redaction_does_not_mutate_the_input(self) -> None:
        original = backtest_row()
        snapshot = original["config_snapshot"]
        redact_document(original)
        self.assertEqual(original["config_snapshot"], snapshot)

    def test_a_served_detail_contains_no_credential(self) -> None:
        store = FakeReadStore()
        app = create_app(make_settings())
        app.dependency_overrides[get_read_store] = lambda: store
        response = call(
            app,
            "GET",
            f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        rendered = response.text
        self.assertNotIn(SENSITIVE_VALUE, rendered)
        self.assertNotIn(CREDENTIAL_VALUE, rendered)
        self.assertNotIn(DSN_PASSWORD, rendered)
        self.assertIn("<redacted>", rendered)

    def test_a_served_listing_omits_the_configuration_entirely(self) -> None:
        store = FakeReadStore()
        app = create_app(make_settings())
        app.dependency_overrides[get_read_store] = lambda: store
        response = call(
            app,
            "GET",
            f"/api/{API_VERSION}/backtests",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("config_snapshot", response.text)
        self.assertNotIn("postgresql://harbor:", response.text)


if __name__ == "__main__":
    unittest.main()
