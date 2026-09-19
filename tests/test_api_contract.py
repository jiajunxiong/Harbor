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
from harbor.core.consistency_check import ConsistencyIssue
from harbor.services.backtest import REPORT_FORMATS, REPORT_MEDIA_TYPES, RenderedReport
from harbor.services.backtest_replay import (
    BacktestReplayError,
    ManifestView,
    ReplayConsistency,
    SiblingConsistency,
)

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
    """A backtest row shaped like the persisted columns, plus a secret-laden config.

    The status is the persisted vocabulary (``BacktestStatus``), not a
    lowercased rendering: the API validates filters against those exact values,
    so a fake that used anything else would hide a real mismatch.
    """
    return {
        "run_id": BACKTEST_RUN_ID,
        "status": "COMPLETED",
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


def net_value_rows() -> list[dict[str, Any]]:
    """A series with a peak, an 11% drawdown and a recovery.

    Chosen so the derived endpoints have something real to work with: three
    points is the metrics minimum, the fall crosses all of 5% / 8% / 10%, and
    the recovery makes ``recovered_date`` non-null.
    """
    return [
        {
            "as_of_date": date(2026, 1, 2),
            "currency": "HKD",
            "cash": 1000.0,
            "securities_value": 9000.0,
            "fees_paid": 10.0,
        },
        {
            "as_of_date": date(2026, 1, 5),
            "currency": "HKD",
            "cash": 1000.0,
            "securities_value": 10500.0,
            "fees_paid": 12.0,
        },
        {
            "as_of_date": date(2026, 1, 6),
            "currency": "HKD",
            "cash": 1000.0,
            "securities_value": 9200.0,
            "fees_paid": 12.0,
        },
        {
            "as_of_date": date(2026, 1, 7),
            "currency": "HKD",
            "cash": 1000.0,
            "securities_value": 11600.0,
            "fees_paid": 12.0,
        },
    ]


def net_value_rows_us() -> list[dict[str, Any]]:
    """A second run's series: another currency, another period, another path.

    Deliberately unlike :func:`net_value_rows` so a comparison has something to
    compare: 1000 -> 1300 USD is +30% against the HKD run's +26%.
    """
    return [
        {
            "as_of_date": date(2026, 2, 2),
            "currency": "USD",
            "cash": 200.0,
            "securities_value": 800.0,
            "fees_paid": 1.0,
        },
        {
            "as_of_date": date(2026, 2, 3),
            "currency": "USD",
            "cash": 200.0,
            "securities_value": 1200.0,
            "fees_paid": 1.0,
        },
        {
            "as_of_date": date(2026, 2, 4),
            "currency": "USD",
            "cash": 200.0,
            "securities_value": 1100.0,
            "fees_paid": 1.0,
        },
    ]


def fill_row(market: str = "HK", symbol: str = "0001.HK") -> dict[str, Any]:
    """One executed order row."""
    return {
        "trade_date": date(2026, 1, 2),
        "market": market,
        "symbol": symbol,
        "side": "BUY",
        "quantity": 100.0,
        "price": 50.0,
        "fee": 12.5,
        "currency": "HKD" if market == "HK" else "USD",
        "order_ref": f"ref-{market}-1",
    }


def rejected_row(
    symbol: str = "0002.HK",
    reason: str = "no quote on 2026-01-02; symbol suspended or untradeable.",
) -> dict[str, Any]:
    """One refused-trade row."""
    return {
        "market": "HK",
        "symbol": symbol,
        "side": "BUY",
        "quantity": 50.0,
        "reason": reason,
        "order_ref": f"ref-{symbol}",
    }


class FakeReadStore:
    """An in-memory read store so the contract is tested without a database (SP 5.8).

    Every call is recorded, which lets a test prove that a refused request
    never reached the data layer.
    """

    def __init__(self, *, backtests: list[dict[str, Any]] | None = None) -> None:
        self.backtests = backtests if backtests is not None else [backtest_row()]
        self.net_values = net_value_rows()
        # Per-run overrides give a comparison genuinely different runs; a run id
        # mapped to an empty list is a run with no valuations at all.
        self.net_values_by_run: dict[str, list[dict[str, Any]]] = {}
        self.replay_error: str | None = None
        self.fills = [fill_row(), fill_row(market="US", symbol="AAPL")]
        self.rejected = [rejected_row(), rejected_row(symbol="0003.HK", reason="no cash")]
        self.calls: list[str] = []
        self.explode = False

    def _record(self, name: str) -> None:
        self.calls.append(name)
        if self.explode:
            raise RuntimeError("simulated driver failure with a secret DSN")

    # -- backtests -------------------------------------------------------

    def list_backtest_runs(
        self,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
        strategy: str | None = None,
        data_cutoff_from: Any = None,
        data_cutoff_to: Any = None,
        sort: str = "started_at",
        order: str = "desc",
    ) -> tuple[list[dict[str, Any]], int]:
        self._record("list_backtest_runs")
        rows = list(self.backtests)
        if status is not None:
            rows = [row for row in rows if row["status"] == status]
        if strategy is not None:
            rows = [row for row in rows if row["strategy"] == strategy]
        if data_cutoff_from is not None:
            rows = [row for row in rows if row["data_cutoff"] >= data_cutoff_from]
        if data_cutoff_to is not None:
            rows = [row for row in rows if row["data_cutoff"] <= data_cutoff_to]
        rows.sort(key=lambda row: str(row.get(sort, "")), reverse=order == "desc")
        # The total describes the *filtered* set, like the real store does.
        return rows[offset : offset + limit], len(rows)

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

    def list_net_values(self, run_id: str) -> list[dict[str, Any]]:
        self._record("list_net_values")
        if run_id in self.net_values_by_run:
            return list(self.net_values_by_run[run_id])
        return list(self.net_values)

    def list_fills(
        self,
        run_id: str,
        *,
        limit: int,
        offset: int,
        market: str | None = None,
        symbol: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        self._record("list_fills")
        rows = list(self.fills)
        if market is not None:
            rows = [row for row in rows if row["market"] == market]
        if symbol is not None:
            rows = [row for row in rows if row["symbol"] == symbol]
        return rows[offset : offset + limit], len(rows)

    def list_rejected_trades(
        self,
        run_id: str,
        *,
        limit: int,
        offset: int,
        market: str | None = None,
        symbol: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        self._record("list_rejected_trades")
        rows = list(self.rejected)
        if market is not None:
            rows = [row for row in rows if row["market"] == market]
        if symbol is not None:
            rows = [row for row in rows if row["symbol"] == symbol]
        return rows[offset : offset + limit], len(rows)

    def rejected_reason_counts(
        self,
        run_id: str,
        *,
        market: str | None = None,
        symbol: str | None = None,
    ) -> list[tuple[str, int]]:
        self._record("rejected_reason_counts")
        rows, _total = self.list_rejected_trades(
            run_id, limit=len(self.rejected) + 1, offset=0, market=market, symbol=symbol
        )
        counts: dict[str, int] = {}
        for row in rows:
            counts[str(row["reason"])] = counts.get(str(row["reason"]), 0) + 1
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))

    def run_filter_options(self) -> tuple[list[str], list[str]]:
        self._record("run_filter_options")
        statuses = sorted({str(row["status"]) for row in self.backtests})
        strategies = sorted({str(row["strategy"]) for row in self.backtests})
        return statuses, strategies

    # -- derived views ---------------------------------------------------

    def render_report(self, run_id: str, report_format: str) -> RenderedReport:
        self._record("render_report")
        return RenderedReport(
            report_format=report_format,
            content=f"report:{report_format}:{run_id}",
            media_type=REPORT_MEDIA_TYPES[report_format],
            filename=f"harbor-backtest-{run_id}.{report_format}",
        )

    def replay_consistency(self, run_id: str, *, max_siblings: int = 5) -> ReplayConsistency:
        self._record("replay_consistency")
        if self.replay_error is not None:
            raise BacktestReplayError(self.replay_error)
        return ReplayConsistency(
            run_id=run_id,
            manifest=ManifestView(
                run_id=run_id,
                config_hash="hash-1",
                code_version="abc1234",
                start_date=date(2020, 1, 1),
                end_date=date(2026, 8, 27),
                data_cutoff=date(2026, 8, 27),
                fx_source=None,
                calendar_version=None,
                random_seed=None,
                fingerprint="fp-self",
            ),
            siblings=(
                SiblingConsistency(
                    run_id="bt-run-2",
                    status="FAILED",
                    same_status=False,
                    consistent=True,
                    outcome_agrees=False,
                    difference_count=3,
                    differences=(
                        ConsistencyIssue(
                            section="net_values",
                            location="length",
                            expected="4",
                            actual="3",
                        ),
                    ),
                ),
            ),
            sibling_total=2,
            notes=("note-one",),
        )

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

    def test_every_versioned_route_requires_authentication(self) -> None:
        """Unauthenticated requests are refused everywhere under the API prefix.

        ``/health`` is the single documented exception: an orchestrator has to
        probe it before it can hold a token. ``/version`` is not exempt — the
        capability document is not a probe (SP 5.4).
        """
        for path in self.app.openapi()["paths"]:
            if not path.startswith(f"/api/{API_VERSION}/"):
                continue
            with self.subTest(path=path):
                self.assertEqual(self.anon_call("GET", path).status_code, 401)

    def test_the_liveness_probe_is_the_only_unauthenticated_route(self) -> None:
        self.assertEqual(self.anon_call("GET", "/health").status_code, 200)

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


class RunListFilterTests(ApiContractTestCase):
    """SP 5.13 — the list filters and sorts, and ``total`` follows the filter."""

    def test_filters_narrow_the_page_and_the_total(self) -> None:
        self.store.backtests = [
            backtest_row() | {"run_id": "bt-a", "status": "COMPLETED"},
            backtest_row() | {"run_id": "bt-b", "status": "FAILED"},
        ]
        body = self.auth_call("GET", f"/api/{API_VERSION}/backtests?status=FAILED").json()
        self.assertEqual(body["total"], 1)
        self.assertEqual([item["run_id"] for item in body["items"]], ["bt-b"])

    def test_an_unknown_status_is_a_tagged_422(self) -> None:
        response = self.auth_call("GET", f"/api/{API_VERSION}/backtests?status=NOPE")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_status")

    def test_an_unknown_sort_field_is_a_tagged_422(self) -> None:
        response = self.auth_call("GET", f"/api/{API_VERSION}/backtests?sort=total_value")
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_sort")

    def test_an_inverted_date_range_is_a_tagged_422(self) -> None:
        response = self.auth_call(
            "GET",
            f"/api/{API_VERSION}/backtests?data_cutoff_from=2026-02-01&data_cutoff_to=2026-01-01",
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_date_range")

    def test_sorting_is_applied(self) -> None:
        self.store.backtests = [
            backtest_row() | {"run_id": "bt-a", "started_at": "2026-01-02T00:00:00Z"},
            backtest_row() | {"run_id": "bt-b", "started_at": "2026-03-02T00:00:00Z"},
        ]
        body = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests?sort=started_at&order=asc"
        ).json()
        self.assertEqual([item["run_id"] for item in body["items"]], ["bt-a", "bt-b"])

    def test_filter_options_report_what_is_present(self) -> None:
        body = self.auth_call("GET", f"/api/{API_VERSION}/backtests/filters").json()
        self.assertIn("COMPLETED", body["statuses"])
        self.assertIn("momentum", body["strategies"])
        self.assertIn("started_at", body["sort_fields"])
        self.assertEqual(body["sort_orders"], ["asc", "desc"])


class NetValueCurveTests(ApiContractTestCase):
    """SP 5.15 — the curve, and honesty about subsampling."""

    def test_returns_the_full_series_by_default(self) -> None:
        body = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/net-values"
        ).json()
        self.assertEqual(body["point_count"], 4)
        self.assertEqual(body["returned_count"], 4)
        self.assertFalse(body["downsampled"])
        self.assertEqual(body["currency"], "HKD")
        self.assertEqual(body["first_date"], "2026-01-02")
        self.assertEqual(body["points"][0]["securities_value"], 9000.0)

    def test_subsampling_is_declared_and_keeps_the_ends(self) -> None:
        body = self.auth_call(
            "GET",
            f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/net-values?max_points=3",
        ).json()
        self.assertEqual(body["point_count"], 4)
        self.assertEqual(body["returned_count"], 3)
        self.assertTrue(body["downsampled"])
        self.assertEqual(body["points"][0]["as_of_date"], "2026-01-02")
        self.assertEqual(body["points"][-1]["as_of_date"], "2026-01-07")

    def test_a_run_without_valuations_has_an_empty_curve(self) -> None:
        self.store.net_values = []
        body = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/net-values"
        ).json()
        self.assertEqual(body["point_count"], 0)
        self.assertEqual(body["points"], [])

    def test_an_ambiguous_series_is_refused_rather_than_charted(self) -> None:
        rows = net_value_rows()
        self.store.net_values = rows[:2] + [rows[2] | {"currency": "USD"}]
        response = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/net-values"
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "net_value_series_unusable")

    def test_the_subsample_bound_is_validated(self) -> None:
        response = self.auth_call(
            "GET",
            f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/net-values?max_points=2",
        )
        self.assertEqual(response.status_code, 422)


class MetricsAndDrawdownTests(ApiContractTestCase):
    """SP 5.16 / SP 5.17 — derived numbers, and why they can be absent."""

    def test_metrics_are_computed_from_persisted_net_values(self) -> None:
        body = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/metrics"
        ).json()
        self.assertTrue(body["available"])
        self.assertEqual(body["source"], "persisted_net_values")
        self.assertEqual(body["currency"], "HKD")
        metrics = body["metrics"]
        self.assertAlmostEqual(metrics["cumulative_return"], 0.26, places=6)
        self.assertAlmostEqual(metrics["max_drawdown"], (11500 - 10200) / 11500, places=6)
        # `periods` counts returns, so four valuations give three.
        self.assertEqual(metrics["periods"], 3)
        self.assertIn("sharpe_ratio", metrics)

    def test_a_run_without_net_values_reports_metrics_as_unavailable(self) -> None:
        self.store.net_values = []
        body = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/metrics"
        ).json()
        self.assertFalse(body["available"])
        self.assertIsNone(body["metrics"])
        self.assertIn("no persisted net values", body["unavailable_reason"] or "")

    def test_a_degenerate_series_reports_the_reason_not_a_zero(self) -> None:
        self.store.net_values = [
            {
                "as_of_date": date(2026, 1, day),
                "currency": "HKD",
                "cash": 1000.0,
                "securities_value": 9000.0,
                "fees_paid": 0.0,
            }
            for day in (2, 5, 6)
        ]
        body = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/metrics"
        ).json()
        self.assertFalse(body["available"])
        self.assertIn("volatility", body["unavailable_reason"] or "")

    def test_drawdowns_use_the_cli_thresholds(self) -> None:
        body = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/drawdowns"
        ).json()
        self.assertTrue(body["available"])
        self.assertEqual(body["thresholds"], [0.05, 0.08, 0.10])
        depths = {event["threshold"]: event["depth"] for event in body["events"]}
        self.assertAlmostEqual(depths[0.05], (11500 - 10200) / 11500, places=6)
        first = body["events"][0]
        self.assertEqual(first["peak_date"], "2026-01-05")
        self.assertEqual(first["trough_date"], "2026-01-06")
        self.assertEqual(first["recovered_date"], "2026-01-07")

    def test_drawdown_events_admit_that_position_detail_is_missing(self) -> None:
        body = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/drawdowns"
        ).json()
        self.assertTrue(body["events"])
        for event in body["events"]:
            self.assertFalse(event["position_detail_available"])

    def test_a_single_day_series_reports_drawdowns_as_unavailable(self) -> None:
        self.store.net_values = net_value_rows()[:1]
        body = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/drawdowns"
        ).json()
        self.assertFalse(body["available"])
        self.assertIsNotNone(body["unavailable_reason"])


class ReplayConsistencyTests(ApiContractTestCase):
    """SP 5.21 — the replay manifest fingerprint and the sibling check."""

    def test_the_manifest_is_published_with_its_fingerprint(self) -> None:
        body = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/replay"
        ).json()

        self.assertEqual(body["manifest"]["fingerprint"], "fp-self")
        self.assertEqual(body["manifest"]["config_hash"], "hash-1")
        self.assertEqual(body["manifest"]["start_date"], "2020-01-01")
        self.assertEqual(body["manifest"]["data_cutoff"], "2026-08-27")

    def test_the_notes_are_served_as_given(self) -> None:
        body = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/replay"
        ).json()

        # The router passes the service's caveats through untouched; the content
        # of those notes is pinned by the service's own tests.
        self.assertEqual(body["notes"], ["note-one"])

    def test_a_sibling_is_compared_and_its_differences_are_located(self) -> None:
        body = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/replay"
        ).json()

        self.assertEqual(len(body["siblings"]), 1)
        sibling = body["siblings"][0]
        self.assertEqual(sibling["run_id"], "bt-run-2")
        self.assertEqual(sibling["difference_count"], 3)
        # The count is exact while the list may be capped, so both are published.
        self.assertEqual(len(sibling["differences"]), 1)
        self.assertEqual(sibling["differences"][0]["section"], "net_values")
        self.assertEqual(sibling["differences"][0]["location"], "length")

    def test_result_agreement_is_not_presented_as_an_outcome_agreement(self) -> None:
        body = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/replay"
        ).json()

        sibling = body["siblings"][0]
        # The fixture is the trap this field exists for: the result sections agree
        # (both runs produced the same rows) while the recorded status does not, so
        # the outcome must not be presented as matching.
        self.assertTrue(sibling["consistent"])
        self.assertFalse(sibling["same_status"])
        self.assertFalse(sibling["outcome_agrees"])

    def test_a_bounded_sibling_list_says_how_many_were_left_out(self) -> None:
        body = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/replay"
        ).json()

        self.assertEqual(body["sibling_total"], 2)
        self.assertTrue(body["truncated"])

    def test_an_underivable_manifest_is_a_tagged_422(self) -> None:
        self.store.replay_error = "The config snapshot is missing start_date/end_date."

        response = self.auth_call("GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/replay")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "replay_manifest_unavailable")

    def test_an_unknown_run_is_a_tagged_404(self) -> None:
        response = self.auth_call("GET", f"/api/{API_VERSION}/backtests/{MISSING_RUN_ID}/replay")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "backtest_run_not_found")
        # The manifest is never derived for a run that does not exist.
        self.assertNotIn("replay_consistency", self.store.calls)


class ReportExportTests(ApiContractTestCase):
    """SP 5.22 — reports are rendered server-side and served as downloads."""

    def test_each_format_is_served_with_its_media_type_and_filename(self) -> None:
        for report_format, media_type in REPORT_MEDIA_TYPES.items():
            with self.subTest(report_format):
                response = self.auth_call(
                    "GET",
                    f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/report?format={report_format}",
                )

                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.headers["content-type"].startswith(media_type))
                disposition = response.headers["content-disposition"]
                self.assertIn("attachment", disposition)
                self.assertIn(f"harbor-backtest-{BACKTEST_RUN_ID}.{report_format}", disposition)
                self.assertEqual(response.text, f"report:{report_format}:{BACKTEST_RUN_ID}")

    def test_the_default_format_is_json(self) -> None:
        response = self.auth_call("GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/report")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("application/json"))

    def test_an_unknown_format_is_refused_before_anything_is_rendered(self) -> None:
        response = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/report?format=xml"
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_report_format")
        self.assertNotIn("render_report", self.store.calls)

    def test_the_offered_formats_are_published_by_the_capability_document(self) -> None:
        info = self.auth_call("GET", f"/api/{API_VERSION}/version").json()

        # Pinning the published list to the renderer's own tuple means a client
        # can never offer a download the API would reject.
        self.assertEqual(info["report_formats"], list(REPORT_FORMATS))
        for report_format in info["report_formats"]:
            with self.subTest(report_format):
                response = self.auth_call(
                    "GET",
                    f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/report?format={report_format}",
                )
                self.assertEqual(response.status_code, 200)

    def test_an_unknown_run_is_a_tagged_404(self) -> None:
        response = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{MISSING_RUN_ID}/report?format=json"
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "backtest_run_not_found")


class MultiRunComparisonTests(ApiContractTestCase):
    """SP 5.23 — several runs side by side, with the caveats stated."""

    def setUp(self) -> None:
        super().setUp()
        self.store.backtests = [
            backtest_row(),
            {**backtest_row(), "run_id": "bt-run-2", "code_version": "def5678"},
        ]

    def compare(
        self, run_ids: str = f"{BACKTEST_RUN_ID},bt-run-2", **kwargs: Any
    ) -> httpx.Response:
        return self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/compare?run_ids={run_ids}", **kwargs
        )

    def test_two_runs_are_returned_with_rebased_curves(self) -> None:
        body = self.compare().json()

        self.assertEqual([run["run_id"] for run in body["runs"]], [BACKTEST_RUN_ID, "bt-run-2"])
        for run in body["runs"]:
            with self.subTest(run["run_id"]):
                # Rebased on each run's own first valuation, so it starts at zero.
                self.assertAlmostEqual(run["points"][0]["cumulative_return"], 0.0)

    def test_each_curve_is_rebased_on_its_own_first_value(self) -> None:
        self.store.net_values_by_run["bt-run-2"] = net_value_rows_us()

        body = self.compare().json()

        hk, us = body["runs"]
        # 10000 -> 12600 HKD and 1000 -> 1300 USD: returns are comparable even
        # though the amounts and currencies are not.
        self.assertAlmostEqual(hk["points"][-1]["cumulative_return"], 0.26)
        self.assertAlmostEqual(us["points"][-1]["cumulative_return"], 0.3)
        self.assertEqual(hk["currency"], "HKD")
        self.assertEqual(us["currency"], "USD")

    def test_the_metrics_come_from_the_same_core_function(self) -> None:
        body = self.compare().json()

        hk = body["runs"][0]
        self.assertTrue(hk["available"])
        self.assertAlmostEqual(hk["metrics"]["cumulative_return"], 0.26)
        self.assertEqual(hk["metrics"]["periods"], len(net_value_rows()) - 1)

    def test_a_differing_currency_is_warned_about(self) -> None:
        self.store.net_values_by_run["bt-run-2"] = net_value_rows_us()

        warnings = " ".join(self.compare().json()["warnings"])

        self.assertIn("币种不同", warnings)

    def test_a_differing_date_range_is_warned_about(self) -> None:
        self.store.net_values_by_run["bt-run-2"] = net_value_rows_us()

        warnings = " ".join(self.compare().json()["warnings"])

        self.assertIn("时间区间不同", warnings)

    def test_a_run_without_a_curve_still_appears_with_the_reason(self) -> None:
        self.store.net_values_by_run["bt-run-2"] = []

        body = self.compare().json()

        failed = body["runs"][1]
        self.assertEqual(failed["point_count"], 0)
        self.assertFalse(failed["available"])
        self.assertIsNotNone(failed["unavailable_reason"])
        # Dropping it would leave a two-run choice looking like a one-run screen.
        self.assertIn("没有可绘制的净值序列", " ".join(body["warnings"]))

    def test_the_notes_refuse_a_cross_currency_amount_comparison(self) -> None:
        notes = " ".join(self.compare().json()["notes"])

        self.assertIn("1:1", notes)

    def test_fewer_than_two_runs_is_refused(self) -> None:
        response = self.compare(run_ids=BACKTEST_RUN_ID)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "too_few_runs")

    def test_a_repeated_run_is_refused(self) -> None:
        response = self.compare(run_ids=f"{BACKTEST_RUN_ID},{BACKTEST_RUN_ID}")

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "duplicate_run_ids")

    def test_too_many_runs_is_refused(self) -> None:
        run_ids = ",".join(f"bt-{index}" for index in range(6))

        response = self.compare(run_ids=run_ids)

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "too_many_runs")

    def test_an_unknown_run_is_a_tagged_404(self) -> None:
        response = self.compare(run_ids=f"{BACKTEST_RUN_ID},{MISSING_RUN_ID}")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "backtest_run_not_found")

    def test_the_compare_route_is_not_shadowed_by_the_run_id_route(self) -> None:
        # ``/backtests/compare`` must win over ``/backtests/{run_id}``; if the
        # order ever flips, this becomes a 404 for a run called "compare".
        response = self.compare()

        self.assertEqual(response.status_code, 200)
        self.assertIn("runs", response.json())


class TradeDetailTests(ApiContractTestCase):
    """SP 5.18 — fills and refusals, filtered, with a full-set distribution."""

    def test_fills_are_paginated_and_filterable(self) -> None:
        body = self.auth_call("GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/fills").json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(len(body["items"]), 2)
        hk = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/fills?market=HK"
        ).json()
        self.assertEqual(hk["total"], 1)
        self.assertEqual(hk["items"][0]["symbol"], "0001.HK")

    def test_an_unknown_market_is_refused(self) -> None:
        response = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/fills?market=JP"
        )
        self.assertEqual(response.status_code, 422)

    def test_refusals_carry_the_full_reason_distribution(self) -> None:
        body = self.auth_call(
            "GET", f"/api/{API_VERSION}/backtests/{BACKTEST_RUN_ID}/rejected-trades"
        ).json()
        self.assertEqual(body["total"], 2)
        reasons = {entry["reason"]: entry["count"] for entry in body["reasons"]}
        self.assertEqual(sum(reasons.values()), 2)
        self.assertIn("no quote on 2026-01-02; symbol suspended or untradeable.", reasons)

    def test_an_unknown_run_is_a_tagged_404_on_every_view(self) -> None:
        for suffix in ("fills", "rejected-trades", "net-values", "metrics", "drawdowns"):
            with self.subTest(suffix=suffix):
                response = self.auth_call(
                    "GET", f"/api/{API_VERSION}/backtests/{MISSING_RUN_ID}/{suffix}"
                )
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["code"], "backtest_run_not_found")


if __name__ == "__main__":
    unittest.main()
