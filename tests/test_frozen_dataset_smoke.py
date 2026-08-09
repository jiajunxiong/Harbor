"""SP 3.14: frozen dataset smoke test (冻结数据集冒烟测试).

Runs the full SP 3.4-3.13 freeze chain against fixed HK, US and cross-market
Mock data: a validated configuration with a frozen split (SP 3.2/3.3/3.4), a
dataset manifest covering all nine data components (SP 3.6), a stable dataset
fingerprint (SP 3.7), a read-only load through the frozen data reader (SP 3.8)
that rejects anything outside the frozen boundaries, coverage scoring and the
coverage gate (SP 3.9/3.10), the independent holdout registration (SP 3.5)
and the state machine freeze to DATA_FROZEN (SP 3.13). Loading is strictly
read-only: repeated loads return equal immutable records and never mutate the
source data.
"""

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone

from pydantic import ValidationError

from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import (
    AdjustmentFactor,
    BacktestDataReader,
    DailyQuote,
    Dividend,
    FundamentalRecord,
)
from harbor.core.coverage_gate import CoverageThresholdConfig, evaluate_coverage
from harbor.core.coverage_scoring import coverage_from_manifest
from harbor.core.dataset_fingerprint import dataset_fingerprint
from harbor.core.dataset_manifest import (
    ALL_COMPONENTS,
    build_dataset_manifest,
    component_manifest,
)
from harbor.core.equity import EntitlementEvent
from harbor.core.frozen_data_reader import FrozenDataError, FrozenDataReader
from harbor.core.holdout_registry import (
    HoldoutAccessError,
    HoldoutRegistration,
    guard_final_evaluation_read,
    register_test_set,
)
from harbor.core.market_registry import CorporateActionType
from harbor.core.validation_config import SplitConfig, ValidationConfig
from harbor.core.validation_config_loader import config_hash
from harbor.core.validation_domain import (
    DatasetManifest,
    EvaluationSplit,
    ManifestComponent,
    ValidationStatus,
)
from harbor.core.validation_split import validate_split_config
from harbor.core.validation_state_machine import (
    ValidationRunState,
    ValidationStateError,
    is_test_authorized,
    validation_initial_state,
)

_MANIFEST_START = date(2019, 1, 1)
_MANIFEST_END = date(2024, 12, 31)
_COMPONENT_VERSION = "2024-12"

_QUOTE_DATES: tuple[date, ...] = (
    date(2019, 1, 2),
    date(2019, 7, 1),
    date(2020, 1, 2),
    date(2021, 1, 4),
    date(2022, 1, 3),
    date(2023, 1, 2),
    date(2024, 1, 2),
)

#: Fixed research FX source recorded in the manifest (no rates needed here).
_FX_SOURCE = "mock"


def _split_config() -> SplitConfig:
    """Return a valid frozen train / validation / test split (SP 3.4)."""
    return SplitConfig(
        train_start=date(2019, 1, 1),
        train_end=date(2020, 12, 31),
        validation_start=date(2021, 1, 1),
        validation_end=date(2022, 12, 31),
        test_start=date(2023, 1, 1),
        test_end=date(2024, 12, 31),
    )


def _quote(market: Market, symbol: str, day: date, price: float) -> DailyQuote:
    """Return one fixed daily quote record."""
    return DailyQuote(
        market=market,
        symbol=symbol,
        day=day,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=100_000,
        adjusted_close=price,
    )


class _FixedDataReader(BacktestDataReader):
    """Read-only BacktestDataReader over fixed HK / US mock data (SP 3.14).

    Serves immutable domain records for a small fixed universe (HK 0001.HK /
    0002.HK, US AAPL / MSFT) and counts every read, so a smoke test can prove
    the load is read-only and repeatable without touching a database.
    """

    def __init__(self, markets: tuple[Market, ...]) -> None:
        self.read_count = 0
        self._markets = markets
        self._pool: dict[Market, tuple[str, ...]] = {
            Market.HK: ("0001.HK", "0002.HK"),
            Market.US: ("AAPL", "MSFT"),
        }
        self._quotes: dict[tuple[Market, str], tuple[DailyQuote, ...]] = {}
        self._dividends: dict[tuple[Market, str], tuple[Dividend, ...]] = {}
        self._financials: dict[tuple[Market, str], tuple[FundamentalRecord, ...]] = {}
        self._actions: dict[tuple[Market, str], tuple[EntitlementEvent, ...]] = {}
        self._factors: dict[tuple[Market, str], tuple[AdjustmentFactor, ...]] = {}
        self._build()

    def _build(self) -> None:
        """Populate the fixed dataset for each requested market."""
        hk_prices = {"0001.HK": 60.0, "0002.HK": 30.0}
        us_prices = {"AAPL": 100.0, "MSFT": 200.0}
        prices = {Market.HK: hk_prices, Market.US: us_prices}
        for market in self._markets:
            for symbol in self._pool[market]:
                price = prices[market][symbol]
                quotes = tuple(_quote(market, symbol, day, price) for day in _QUOTE_DATES)
                self._quotes[(market, symbol)] = quotes
                self._factors[(market, symbol)] = tuple(
                    AdjustmentFactor(
                        market=market,
                        symbol=symbol,
                        date=day,
                        cumulative_factor=1.0,
                        daily_factor=1.0,
                    )
                    for day in _QUOTE_DATES
                )
        self._dividends[(Market.HK, "0001.HK")] = (
            Dividend(
                market=Market.HK,
                symbol="0001.HK",
                amount=1.0,
                currency=Currency.HKD,
                ex_date=date(2024, 6, 14),
                record_date=date(2024, 6, 18),
                payment_date=date(2024, 7, 5),
            ),
        )
        self._dividends[(Market.US, "AAPL")] = (
            Dividend(
                market=Market.US,
                symbol="AAPL",
                amount=0.24,
                currency=Currency.USD,
                ex_date=date(2024, 8, 8),
            ),
        )
        self._financials[(Market.HK, "0001.HK")] = (
            FundamentalRecord(
                market=Market.HK,
                symbol="0001.HK",
                report_date=date(2024, 3, 31),
                fiscal_period="2023FY",
                available_on=date(2024, 4, 30),
                roe=0.15,
                net_income=1_000_000_000.0,
                total_equity=10_000_000_000.0,
                revenue=5_000_000_000.0,
            ),
        )
        self._actions[(Market.HK, "0002.HK")] = (
            EntitlementEvent(
                action_id="hk-rights-1",
                action_type=CorporateActionType.RIGHTS_ISSUE,
                ex_date=date(2023, 6, 30),
            ),
        )
        self._actions[(Market.US, "AAPL")] = (
            EntitlementEvent(
                action_id="us-split-1",
                action_type=CorporateActionType.SPLIT,
                ex_date=date(2024, 6, 10),
            ),
        )

    def _tick(self) -> None:
        self.read_count += 1

    def list_securities(self, market: Market, as_of: date) -> Sequence[str]:
        self._tick()
        return self._pool.get(market, ())

    def daily_quotes(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[DailyQuote]:
        self._tick()
        return tuple(
            quote for quote in self._quotes.get((market, symbol), ()) if start <= quote.day <= end
        )

    def dividends(self, market: Market, symbol: str, start: date, end: date) -> Sequence[Dividend]:
        self._tick()
        return tuple(
            dividend
            for dividend in self._dividends.get((market, symbol), ())
            if start <= dividend.ex_date <= end
        )

    def fundamentals(self, market: Market, symbol: str, as_of: date) -> Sequence[FundamentalRecord]:
        self._tick()
        return tuple(
            record
            for record in self._financials.get((market, symbol), ())
            if record.available_on is not None and record.available_on <= as_of
        )

    def corporate_actions(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[EntitlementEvent]:
        self._tick()
        return tuple(
            event
            for event in self._actions.get((market, symbol), ())
            if event.ex_date is not None and start <= event.ex_date <= end
        )

    def adjustment_factors(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[AdjustmentFactor]:
        self._tick()
        return tuple(
            factor
            for factor in self._factors.get((market, symbol), ())
            if start <= factor.date <= end
        )


@dataclass(frozen=True)
class _FrozenUniverse:
    """A fully frozen validation universe for one market configuration."""

    config: ValidationConfig
    config_hash: str
    split: EvaluationSplit
    manifest: DatasetManifest
    fingerprint: str
    reader: _FixedDataReader
    frozen_reader: FrozenDataReader
    registration: HoldoutRegistration
    state: ValidationRunState


def _frozen_universe(
    markets: tuple[Market, ...],
    base_currency: Currency,
    *,
    data_cutoff: date = _MANIFEST_END,
    excluded_components: tuple[ManifestComponent, ...] = (),
    served_versions: Mapping[ManifestComponent, str] | None = None,
) -> _FrozenUniverse:
    """Assemble a frozen validation universe over fixed mock data (SP 3.14).

    Builds the validated configuration and its frozen-split hash (SP 3.2/3.3),
    validates the split boundaries (SP 3.4), assembles the dataset manifest
    with the frozen data components (SP 3.6), derives the dataset fingerprint
    (SP 3.7), wraps the fixed reader in the frozen data reader (SP 3.8),
    registers the independent holdout (SP 3.5) and freezes the run in the
    state machine (SP 3.13). ``data_cutoff`` controls the manifest's frozen
    data cutoff (the config keeps the full-window cutoff so the split stays
    valid).
    """
    config = ValidationConfig(
        markets=markets,
        base_currency=base_currency,
        data_cutoff=_MANIFEST_END,
        code_version="1.0.0",
        split=_split_config(),
    )
    frozen_hash = config_hash(config)
    split = validate_split_config(config.split)

    reader = _FixedDataReader(markets)
    components = tuple(
        component_manifest(
            kind, "mock", _COMPONENT_VERSION, start=_MANIFEST_START, end=_MANIFEST_END
        )
        for kind in ALL_COMPONENTS
        if kind not in excluded_components
    )
    placeholder = build_dataset_manifest(
        markets=markets,
        base_currency=base_currency,
        start_date=_MANIFEST_START,
        end_date=_MANIFEST_END,
        data_cutoff=data_cutoff,
        config_hash=frozen_hash,
        code_version="1.0.0",
        calendar_version="v1",
        fx_source=_FX_SOURCE,
        fingerprint="0" * 64,
        random_seed=42,
        components=components,
    )
    fingerprint = dataset_fingerprint(placeholder)
    manifest = build_dataset_manifest(
        markets=markets,
        base_currency=base_currency,
        start_date=_MANIFEST_START,
        end_date=_MANIFEST_END,
        data_cutoff=data_cutoff,
        config_hash=frozen_hash,
        code_version="1.0.0",
        calendar_version="v1",
        fx_source=_FX_SOURCE,
        fingerprint=fingerprint,
        random_seed=42,
        components=components,
    )
    frozen_reader = FrozenDataReader(reader, manifest, served_versions=served_versions)
    registration = register_test_set(
        "holdout-smoke",
        split=split,
        config_hash=frozen_hash,
        created_at=datetime(2026, 8, 9, tzinfo=timezone.utc),
    )
    state = validation_initial_state("validation-smoke").freeze()
    return _FrozenUniverse(
        config=config,
        config_hash=frozen_hash,
        split=split,
        manifest=manifest,
        fingerprint=fingerprint,
        reader=reader,
        frozen_reader=frozen_reader,
        registration=registration,
        state=state,
    )


class ManifestFingerprintTests(unittest.TestCase):
    """Verify the frozen manifest and stable fingerprint (SP 3.6/3.7)."""

    def test_hk_manifest_is_frozen_and_self_consistent(self) -> None:
        universe = _frozen_universe((Market.HK,), Currency.HKD)
        self.assertEqual(universe.manifest.markets, (Market.HK,))
        self.assertEqual(universe.manifest.base_currency, Currency.HKD)
        self.assertEqual(len(universe.manifest.components), len(ALL_COMPONENTS))
        self.assertEqual(universe.fingerprint, dataset_fingerprint(universe.manifest))
        self.assertIn("components 9", universe.manifest.readable())

    def test_us_manifest_is_frozen_and_self_consistent(self) -> None:
        universe = _frozen_universe((Market.US,), Currency.USD)
        self.assertEqual(universe.manifest.markets, (Market.US,))
        self.assertEqual(len(universe.manifest.components), len(ALL_COMPONENTS))
        self.assertEqual(universe.fingerprint, dataset_fingerprint(universe.manifest))

    def test_cross_market_manifest_is_frozen(self) -> None:
        universe = _frozen_universe((Market.HK, Market.US), Currency.HKD)
        self.assertEqual(universe.manifest.markets, (Market.HK, Market.US))
        self.assertEqual(universe.manifest.base_currency, Currency.HKD)
        self.assertEqual(universe.fingerprint, dataset_fingerprint(universe.manifest))

    def test_fingerprint_is_replayable(self) -> None:
        first = _frozen_universe((Market.HK,), Currency.HKD)
        second = _frozen_universe((Market.HK,), Currency.HKD)
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_fingerprint_changes_with_cutoff(self) -> None:
        base = _frozen_universe((Market.HK,), Currency.HKD)
        drifted = _frozen_universe((Market.HK,), Currency.HKD, data_cutoff=date(2024, 6, 30))
        self.assertNotEqual(base.fingerprint, drifted.fingerprint)


class SplitFreezeTests(unittest.TestCase):
    """Verify the frozen split and the state-machine freeze (SP 3.4/3.13)."""

    def test_split_boundaries_are_frozen(self) -> None:
        universe = _frozen_universe((Market.HK,), Currency.HKD)
        self.assertEqual(universe.split.train_start, date(2019, 1, 1))
        self.assertEqual(universe.split.test_end, date(2024, 12, 31))
        self.assertIn("split train", universe.split.readable())

    def test_invalid_split_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            ValidationConfig(
                markets=(Market.HK,),
                base_currency=Currency.HKD,
                split=SplitConfig(
                    train_start=date(2019, 1, 1),
                    train_end=date(2021, 12, 31),
                    validation_start=date(2021, 1, 1),
                    validation_end=date(2022, 12, 31),
                    test_start=date(2023, 1, 1),
                    test_end=date(2024, 12, 31),
                ),
            )

    def test_state_machine_freezes_to_data_frozen(self) -> None:
        universe = _frozen_universe((Market.HK,), Currency.HKD)
        self.assertEqual(universe.state.status, ValidationStatus.DATA_FROZEN)
        self.assertEqual(len(universe.state.transitions), 1)
        self.assertEqual(universe.state.transitions[0].to_status, ValidationStatus.DATA_FROZEN)

    def test_state_machine_rejects_illegal_transition(self) -> None:
        with self.assertRaisesRegex(ValidationStateError, "DRAFT -> TUNING"):
            validation_initial_state("validation-smoke").tune()


class ReadOnlyLoadTests(unittest.TestCase):
    """Verify the frozen data reader loads read-only (SP 3.8 acceptance)."""

    def test_hk_read_only_load(self) -> None:
        universe = _frozen_universe((Market.HK,), Currency.HKD)
        frozen = universe.frozen_reader
        self.assertEqual(
            frozen.list_securities(Market.HK, date(2024, 1, 2)), ("0001.HK", "0002.HK")
        )
        quotes = frozen.daily_quotes(Market.HK, "0001.HK", date(2023, 1, 1), date(2024, 12, 31))
        self.assertEqual([q.day for q in quotes], [date(2023, 1, 2), date(2024, 1, 2)])
        dividends = frozen.dividends(Market.HK, "0001.HK", date(2024, 1, 1), date(2024, 12, 31))
        self.assertEqual([d.ex_date for d in dividends], [date(2024, 6, 14)])
        fundamentals = frozen.fundamentals(Market.HK, "0001.HK", date(2024, 6, 1))
        self.assertEqual([f.report_date for f in fundamentals], [date(2024, 3, 31)])
        actions = frozen.corporate_actions(
            Market.HK, "0002.HK", date(2023, 1, 1), date(2023, 12, 31)
        )
        self.assertEqual([a.action_id for a in actions], ["hk-rights-1"])

    def test_us_read_only_load(self) -> None:
        universe = _frozen_universe((Market.US,), Currency.USD)
        frozen = universe.frozen_reader
        self.assertEqual(frozen.list_securities(Market.US, date(2024, 1, 2)), ("AAPL", "MSFT"))
        quotes = frozen.daily_quotes(Market.US, "AAPL", date(2023, 1, 1), date(2024, 12, 31))
        self.assertEqual(len(quotes), 2)
        dividends = frozen.dividends(Market.US, "AAPL", date(2024, 1, 1), date(2024, 12, 31))
        self.assertEqual([d.amount for d in dividends], [0.24])
        actions = frozen.corporate_actions(Market.US, "AAPL", date(2024, 1, 1), date(2024, 12, 31))
        self.assertEqual([a.action_id for a in actions], ["us-split-1"])

    def test_cross_market_read_only_load(self) -> None:
        universe = _frozen_universe((Market.HK, Market.US), Currency.HKD)
        frozen = universe.frozen_reader
        for market in (Market.HK, Market.US):
            pool = frozen.list_securities(market, date(2024, 1, 2))
            self.assertTrue(pool)
            for symbol in pool:
                quotes = frozen.daily_quotes(market, symbol, date(2023, 1, 1), date(2024, 12, 31))
                self.assertTrue(quotes)

    def test_load_is_repeatable_and_read_only(self) -> None:
        universe = _frozen_universe((Market.HK,), Currency.HKD)
        frozen = universe.frozen_reader
        before = universe.reader.read_count
        first = frozen.daily_quotes(Market.HK, "0001.HK", date(2023, 1, 1), date(2024, 12, 31))
        second = frozen.daily_quotes(Market.HK, "0001.HK", date(2023, 1, 1), date(2024, 12, 31))
        self.assertEqual(first, second)
        self.assertGreater(universe.reader.read_count, before)

    def test_out_of_window_read_is_rejected(self) -> None:
        universe = _frozen_universe((Market.HK,), Currency.HKD)
        with self.assertRaisesRegex(FrozenDataError, "outside the frozen window"):
            universe.frozen_reader.daily_quotes(
                Market.HK, "0001.HK", date(2015, 1, 1), date(2015, 12, 31)
            )

    def test_after_cutoff_read_is_rejected(self) -> None:
        universe = _frozen_universe((Market.HK,), Currency.HKD, data_cutoff=date(2024, 6, 30))
        with self.assertRaisesRegex(FrozenDataError, "data cutoff"):
            universe.frozen_reader.daily_quotes(
                Market.HK, "0001.HK", date(2024, 7, 1), date(2024, 12, 31)
            )

    def test_out_of_pool_symbol_is_rejected(self) -> None:
        universe = _frozen_universe((Market.HK,), Currency.HKD)
        with self.assertRaisesRegex(FrozenDataError, "not in the frozen historical stock pool"):
            universe.frozen_reader.daily_quotes(
                Market.HK, "9999.HK", date(2023, 1, 1), date(2024, 12, 31)
            )

    def test_out_of_manifest_market_is_rejected(self) -> None:
        universe = _frozen_universe((Market.HK,), Currency.HKD)
        with self.assertRaisesRegex(FrozenDataError, "market US is not frozen"):
            universe.frozen_reader.list_securities(Market.US, date(2024, 1, 2))

    def test_served_version_mismatch_is_rejected(self) -> None:
        universe = _frozen_universe(
            (Market.HK,),
            Currency.HKD,
            served_versions={ManifestComponent.PRICES: "2024-06"},
        )
        with self.assertRaisesRegex(FrozenDataError, "data version mismatch for prices"):
            universe.frozen_reader.daily_quotes(
                Market.HK, "0001.HK", date(2023, 1, 1), date(2024, 12, 31)
            )


class CoverageGateTests(unittest.TestCase):
    """Verify coverage scoring and the gate (SP 3.9/3.10)."""

    def test_coverage_is_full_when_all_components_frozen(self) -> None:
        for markets, base in (
            ((Market.HK,), Currency.HKD),
            ((Market.US,), Currency.USD),
            ((Market.HK, Market.US), Currency.HKD),
        ):
            with self.subTest(markets=tuple(m.value for m in markets)):
                universe = _frozen_universe(markets, base)
                coverage = coverage_from_manifest(universe.manifest, markets[0])
                self.assertEqual(coverage.overall_pct, 100.0)
                self.assertEqual(coverage.gaps(), ())

    def test_coverage_gate_passes_when_frozen(self) -> None:
        universe = _frozen_universe((Market.HK,), Currency.HKD)
        coverage = coverage_from_manifest(universe.manifest, Market.HK)
        gate = evaluate_coverage(coverage, CoverageThresholdConfig())
        self.assertTrue(gate.passes)
        self.assertFalse(gate.blocked)
        self.assertEqual(gate.errors, ())

    def test_missing_fx_is_not_qualified_and_blocks(self) -> None:
        universe = _frozen_universe(
            (Market.HK,),
            Currency.HKD,
            excluded_components=(ManifestComponent.FX,),
        )
        coverage = coverage_from_manifest(universe.manifest, Market.HK)
        gate = evaluate_coverage(coverage, CoverageThresholdConfig())
        self.assertFalse(gate.passes)
        self.assertTrue(
            any(result.item is ManifestComponent.FX for result in gate.not_qualified_items)
        )

    def test_missing_benchmark_is_a_gap(self) -> None:
        universe = _frozen_universe(
            (Market.US,),
            Currency.USD,
            excluded_components=(ManifestComponent.BENCHMARK,),
        )
        coverage = coverage_from_manifest(universe.manifest, Market.US)
        self.assertIsNotNone(coverage.score(ManifestComponent.BENCHMARK))
        self.assertTrue(coverage.score(ManifestComponent.BENCHMARK).is_gap)


class HoldoutRegistrationTests(unittest.TestCase):
    """Verify the independent holdout registration (SP 3.5) and test access."""

    def test_holdout_registered_with_split_and_hash(self) -> None:
        universe = _frozen_universe((Market.HK,), Currency.HKD)
        registration = universe.registration
        self.assertEqual(registration.test_set_id, "holdout-smoke")
        self.assertEqual(registration.split, universe.split)
        self.assertEqual(registration.config_hash, universe.config_hash)
        self.assertEqual(registration.authorized_stage, ValidationStatus.TEST_LOCKED)

    def test_holdout_guarded_until_test_locked(self) -> None:
        universe = _frozen_universe((Market.HK,), Currency.HKD)
        registration = universe.registration
        guard_final_evaluation_read(registration, ValidationStatus.TEST_LOCKED)
        guard_final_evaluation_read(registration, ValidationStatus.EVALUATED)
        with self.assertRaises(HoldoutAccessError):
            guard_final_evaluation_read(registration, ValidationStatus.TUNING)

    def test_state_machine_authorizes_test_read_after_lock(self) -> None:
        self.assertFalse(is_test_authorized(ValidationStatus.TUNING))
        self.assertTrue(is_test_authorized(ValidationStatus.TEST_LOCKED))
        universe = _frozen_universe((Market.HK,), Currency.HKD)
        locked = universe.state.lock_test_set()
        self.assertTrue(locked.test_authorized)


if __name__ == "__main__":
    unittest.main()
