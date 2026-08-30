"""Minimal paper-loop smoke test (MVP 4 / SP 4.13).

Runs the stage-1 acceptance end to end with fixed Mock data: load a paper
config and hash it (SP 4.2), admit a QUALIFIED strategy (SP 4.3), open a
multi-currency account (SP 4.4), draft/approve/activate a paper run (SP 4.10),
assess data availability through the MVP 2 reader (SP 4.12), generate a signal
intention, create an order, fill it and apply it to the account, then complete
a basic reconciliation: assets close (cash + cost basis) and the net value
equals cash + securities (SP 4.8 / 4.13). The full loop is also recorded to an
audit log with a stable fingerprint (SP 4.11).

Pure domain smoke: no database required, so it runs everywhere (the storage
paths are separately covered by the repository and migration tests).
"""

import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from harbor.core.backtest_domain import Currency, Market, OrderSide
from harbor.core.backtest_interfaces import BacktestDataReader, DailyQuote
from harbor.core.paper_account import (
    account_assets_base,
    apply_paper_fill,
    open_paper_account,
)
from harbor.core.paper_admission import AdmissionDecision, admit_strategy
from harbor.core.paper_audit import (
    AuditActor,
    AuditEventType,
    audit_fingerprint,
    new_paper_audit_log,
)
from harbor.core.paper_config_loader import config_hash, load_paper_config
from harbor.core.paper_data_reader import PaperReaderAdapter, assess_paper_data
from harbor.core.paper_domain import (
    PaperFill,
    PaperOrder,
    PaperOrderStatus,
    PaperPriceType,
    PaperRun,
    PaperStatus,
    SignalIntention,
)
from harbor.core.paper_replay_identity import build_paper_replay_identity
from harbor.core.paper_state_machine import paper_initial_state
from harbor.core.validation_domain import OOSConclusion

_UTC = timezone.utc
_OPENED = date(2026, 1, 1)
_TRADE = date(2026, 1, 2)
_FILL_PRICE = 50.0
_FILL_FEE = 5.0

_CONFIG_YAML = """\
strategy: mvp3-qualified
strategy_version: "1.0.0"
description: research only smoke
markets: [HK]
base_currency: HKD
currencies: [HKD]
initial_capital: 1000000.0
rebalance_frequency: QUARTERLY
run_mode: MANUAL
calendar_version: hkex-2026
fx_source: mock
random_seed: 42
"""


def _utc_at(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=_UTC)


class _MockQuoteReader(BacktestDataReader):
    """A minimal mock MVP 2 reader serving one fixed HK quote."""

    def list_securities(self, market: Market, as_of: date) -> list[str]:
        return ["0001.HK"] if market is Market.HK else []

    def daily_quotes(self, market: Market, symbol: str, start: date, end: date) -> list[DailyQuote]:
        return [
            DailyQuote(
                market=Market.HK,
                symbol="0001.HK",
                day=_TRADE,
                open=_FILL_PRICE,
                high=_FILL_PRICE,
                low=_FILL_PRICE,
                close=_FILL_PRICE,
                volume=100_000,
                adjusted_close=_FILL_PRICE,
            )
        ]

    def dividends(self, market: Market, symbol: str, start: date, end: date) -> list[object]:
        return []

    def fundamentals(self, market: Market, symbol: str, as_of: date) -> list[object]:
        return []

    def corporate_actions(
        self, market: Market, symbol: str, start: date, end: date
    ) -> list[object]:
        return []

    def adjustment_factors(
        self, market: Market, symbol: str, start: date, end: date
    ) -> list[object]:
        return []


class PaperSmokeTests(unittest.TestCase):
    """The stage-1 paper loop end to end (SP 4.13)."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self._config_path = Path(self._tmp.name) / "paper.yaml"
        self._config_path.write_text(_CONFIG_YAML, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_paper_loop(self):
        """Run the full stage-1 loop and return the intermediate artifacts."""
        config = load_paper_config(self._config_path)

        # 4.3 — only a QUALIFIED strategy enters the paper loop
        admission = admit_strategy(
            strategy=config.strategy,
            strategy_version=config.strategy_version,
            conclusion=OOSConclusion.QUALIFIED,
            source_run_id="mvp3-acceptance-001",
            dataset_fingerprint="f2400c7e8b858b82553a9836a4411cfca0",
            admitted_at=_utc_at(2026, 1, 1, 7),
        )
        self.assertEqual(admission.decision, AdmissionDecision.ADMITTED)

        # 4.4 — open the multi-currency account funded in the base currency
        account = open_paper_account(
            account_id="acc-smoke",
            base_currency=config.base_currency,
            currencies=config.currencies,
            initial_capital=config.initial_capital,
            opened_at=_OPENED,
        )
        self.assertEqual(account.balance(Currency.HKD), 1_000_000.0)

        # 4.9 — replayable run identity
        identity = build_paper_replay_identity(
            config=config,
            dataset_fingerprint=admission.dataset_fingerprint or "",
            code_version="1.0.0",
        )
        self.assertEqual(config_hash(config), identity.config_hash)

        # 4.5 + 4.10 — draft, approve and activate the paper run
        run = PaperRun(
            run_id="paper-smoke-001",
            strategy=config.strategy,
            strategy_version=config.strategy_version,
            config_hash=identity.config_hash,
            dataset_fingerprint=identity.dataset_fingerprint,
            code_version=identity.code_version,
            markets=config.markets,
            base_currency=config.base_currency,
            status=PaperStatus.DRAFT,
            created_at=_utc_at(2026, 1, 1, 8),
        )
        state = paper_initial_state(run.run_id)
        state = state.approve(recorded_at=_utc_at(2026, 1, 1, 9)).activate(
            recorded_at=_utc_at(2026, 1, 1, 10)
        )
        self.assertEqual(state.status, PaperStatus.ACTIVE)
        self.assertEqual(len(state.transitions), 2)

        # 4.12 — data availability through the MVP 2 reader
        reader = PaperReaderAdapter(
            _MockQuoteReader(),
            fx_rate_with_date=lambda _from, _to, _as_of: None,
        )
        availability = assess_paper_data(
            reader=reader,
            market=Market.HK,
            symbol="0001.HK",
            as_of=_TRADE,
            quote_currency=Currency.HKD,
            base_currency=Currency.HKD,
        )
        self.assertTrue(availability.ok)

        # 4.14 — a signal intention from the strategy output
        intention = SignalIntention(
            intention_id="sig-smoke-001",
            paper_run_id=run.run_id,
            strategy=config.strategy,
            strategy_version=config.strategy_version,
            market=Market.HK,
            rebalance_date=_TRADE,
            target_weights=(("0001.HK", 1.0),),
            source_run_id="mvp3-acceptance-001",
            created_at=_utc_at(2026, 1, 2, 8),
        )

        # 4.6 — create the order, then fill it at the mock quote
        order = PaperOrder(
            order_id="order-smoke-001",
            paper_run_id=run.run_id,
            market=Market.HK,
            symbol="0001.HK",
            side=OrderSide.BUY,
            quantity=100.0,
            currency=Currency.HKD,
            price_type=PaperPriceType.REFERENCE,
            status=PaperOrderStatus.CREATED,
            created_at=_utc_at(2026, 1, 2, 9),
            intention_id=intention.intention_id,
        )
        fill = PaperFill(
            fill_id="fill-smoke-001",
            paper_order_id=order.order_id,
            paper_run_id=run.run_id,
            market=Market.HK,
            symbol="0001.HK",
            side=OrderSide.BUY,
            quantity=order.quantity,
            price=availability.latest_close or _FILL_PRICE,
            currency=Currency.HKD,
            trade_date=_TRADE,
            fee=_FILL_FEE,
        )
        account = apply_paper_fill(account, fill=fill)
        position = account.position(Market.HK, "0001.HK")
        self.assertIsNotNone(position)
        self.assertEqual(position.quantity, 100.0)
        self.assertEqual(position.average_cost, _FILL_PRICE)
        self.assertEqual(account.balance(Currency.HKD), 1_000_000.0 - 5_005.0)

        # 4.8 — daily net value and basic reconciliation (assets close)
        assets = account_assets_base(account, fx_rate=lambda _from, _to: 1.0)
        self.assertTrue(assets.reconciled())
        self.assertAlmostEqual(assets.total_base, 999_995.0, places=6)

        # 4.11 — the full loop is audited with a stable fingerprint
        log = new_paper_audit_log(run.run_id, recorded_at=_utc_at(2026, 1, 1, 8))
        log = log.append(
            event_id="e-signal",
            event_type=AuditEventType.SIGNAL,
            actor=AuditActor.SYSTEM,
            detail=f"signal {intention.intention_id} recorded",
            recorded_at=_utc_at(2026, 1, 2, 8),
        )
        log = log.append(
            event_id="e-order",
            event_type=AuditEventType.ORDER,
            actor=AuditActor.SYSTEM,
            detail=f"order {order.order_id} created",
            recorded_at=_utc_at(2026, 1, 2, 9),
        )
        log = log.append(
            event_id="e-fill",
            event_type=AuditEventType.FILL,
            actor=AuditActor.SYSTEM,
            detail=f"fill {fill.fill_id} applied",
            recorded_at=_utc_at(2026, 1, 2, 10),
        )
        self.assertEqual(len(log.events), 4)
        self.assertEqual(len(log.events_for(AuditEventType.SIGNAL)), 1)

        return {
            "config": config,
            "admission": admission,
            "identity": identity,
            "run": run,
            "state": state,
            "account": account,
            "order": order,
            "fill": fill,
            "log": log,
        }

    def test_full_stage1_loop(self) -> None:
        artifacts = self._run_paper_loop()
        self.assertEqual(artifacts["state"].status, PaperStatus.ACTIVE)
        self.assertEqual(artifacts["admission"].decision, AdmissionDecision.ADMITTED)

    def test_inconclusive_strategy_rejected(self) -> None:
        config = load_paper_config(self._config_path)
        admission = admit_strategy(
            strategy=config.strategy,
            strategy_version=config.strategy_version,
            conclusion=OOSConclusion.INCONCLUSIVE,
            admitted_at=_utc_at(2026, 1, 1, 7),
        )
        self.assertEqual(admission.decision, AdmissionDecision.REJECTED)
        self.assertIn("INCONCLUSIVE", admission.reason)

    def test_audit_fingerprint_is_replayable(self) -> None:
        first = self._run_paper_loop()["log"]
        second = self._run_paper_loop()["log"]
        self.assertEqual(audit_fingerprint(first), audit_fingerprint(second))

    def test_paper_run_master_record(self) -> None:
        run = self._run_paper_loop()["run"]
        self.assertEqual(run.strategy, "mvp3-qualified")
        self.assertEqual(run.base_currency, Currency.HKD)
        self.assertIn("paper run paper-smoke-001", run.readable())
