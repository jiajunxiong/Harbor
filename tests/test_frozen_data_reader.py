"""Frozen data reader tests (MVP 3 / SP 3.8).

Verifies that the frozen data reader wraps the MVP 2 point-in-time reader and
rejects every read that falls outside the frozen dataset manifest: a market
not in the manifest, a date range outside the frozen window or data cutoff, a
symbol absent from the frozen historical stock pool, a data component not
frozen in the manifest, or a served data version that differs from the frozen
component version.
"""

import unittest
from collections.abc import Sequence
from datetime import date

from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import (
    AdjustmentFactor,
    BacktestDataReader,
    DailyQuote,
    Dividend,
    FundamentalRecord,
)
from harbor.core.equity import EntitlementEvent
from harbor.core.frozen_data_reader import FrozenDataError, FrozenDataReader
from harbor.core.validation_domain import (
    DataComponentManifest,
    DatasetManifest,
    ManifestComponent,
)


class _FakeReader(BacktestDataReader):
    """In-memory reader that records calls and returns canned values."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.pool: dict[tuple[Market, date], Sequence[str]] = {}
        self.result: Sequence[object] = ("canned",)

    def _record(self, method: str, *parts: object) -> Sequence[object]:
        self.calls.append(":".join([method, *(str(part) for part in parts)]))
        return self.result

    def list_securities(self, market: Market, as_of: date) -> Sequence[str]:
        self.calls.append(f"list_securities:{market.value}:{as_of.isoformat()}")
        return self.pool.get((market, as_of), ())

    def daily_quotes(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[DailyQuote]:
        return self._record(
            "daily_quotes", market.value, symbol, start.isoformat(), end.isoformat()
        )

    def dividends(self, market: Market, symbol: str, start: date, end: date) -> Sequence[Dividend]:
        return self._record("dividends", market.value, symbol, start.isoformat(), end.isoformat())

    def fundamentals(self, market: Market, symbol: str, as_of: date) -> Sequence[FundamentalRecord]:
        return self._record("fundamentals", market.value, symbol, as_of.isoformat())

    def corporate_actions(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[EntitlementEvent]:
        return self._record(
            "corporate_actions", market.value, symbol, start.isoformat(), end.isoformat()
        )

    def adjustment_factors(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[AdjustmentFactor]:
        return self._record(
            "adjustment_factors", market.value, symbol, start.isoformat(), end.isoformat()
        )


def _component(
    kind: ManifestComponent,
    version: str = "2024-12",
) -> DataComponentManifest:
    """Return a bounded component record within the default manifest range."""
    return DataComponentManifest(
        component=kind,
        source="mock",
        version=version,
        start=date(2019, 1, 1),
        end=date(2024, 12, 31),
    )


def _manifest(**overrides: object) -> DatasetManifest:
    """Return a valid frozen manifest with the main components recorded."""
    fields: dict[str, object] = {
        "markets": (Market.HK, Market.US),
        "base_currency": Currency.HKD,
        "start_date": date(2019, 1, 1),
        "end_date": date(2024, 12, 31),
        "data_cutoff": date(2024, 12, 31),
        "config_hash": "abc123",
        "code_version": "1.0.0",
        "calendar_version": "hkex-2024",
        "fx_source": "mock",
        "fingerprint": "fp-1",
        "components": (
            _component(ManifestComponent.PRICES),
            _component(ManifestComponent.DIVIDENDS),
            _component(ManifestComponent.FUNDAMENTALS),
            _component(ManifestComponent.CORPORATE_ACTIONS),
            _component(ManifestComponent.STOCK_POOL),
        ),
    }
    fields.update(overrides)
    return DatasetManifest(**fields)  # type: ignore[arg-type]


def _hk_frozen() -> tuple[_FakeReader, FrozenDataReader]:
    """Return a fake reader and frozen reader with a populated HK pool."""
    fake = _FakeReader()
    frozen = FrozenDataReader(fake, _manifest())
    fake.pool[(Market.HK, date(2021, 12, 31))] = ("0001.HK", "0002.HK")
    fake.pool[(Market.US, date(2021, 12, 31))] = ("AAPL", "MSFT")
    return fake, frozen


class FrozenDataReaderConstructionTests(unittest.TestCase):
    """Verify the reader exposes the manifest it is bound to."""

    def test_exposes_the_manifest(self) -> None:
        _, frozen = _hk_frozen()
        self.assertEqual(frozen.manifest, _manifest())


class MarketGuardTests(unittest.TestCase):
    """Verify out-of-manifest markets are rejected (SP 3.8)."""

    def test_rejects_market_outside_manifest(self) -> None:
        manifest = _manifest(markets=(Market.HK,))
        frozen = FrozenDataReader(_FakeReader(), manifest)
        with self.assertRaisesRegex(FrozenDataError, "market US is not frozen"):
            frozen.daily_quotes(Market.US, "AAPL", date(2019, 1, 1), date(2021, 12, 31))

    def test_accepts_market_in_manifest(self) -> None:
        fake, frozen = _hk_frozen()
        result = frozen.daily_quotes(Market.HK, "0001.HK", date(2019, 1, 1), date(2021, 12, 31))
        self.assertEqual(result, ("canned",))


class RangeGuardTests(unittest.TestCase):
    """Verify out-of-window and beyond-cutoff reads are rejected (SP 3.8)."""

    def _frozen(self, **manifest_overrides: object) -> tuple[_FakeReader, FrozenDataReader]:
        fake = _FakeReader()
        frozen = FrozenDataReader(fake, _manifest(**manifest_overrides))
        fake.pool[(Market.HK, date(2021, 12, 31))] = ("0001.HK",)
        return fake, frozen

    def test_rejects_range_before_frozen_window(self) -> None:
        _, frozen = self._frozen()
        with self.assertRaisesRegex(FrozenDataError, "outside the frozen window"):
            frozen.daily_quotes(Market.HK, "0001.HK", date(2018, 1, 1), date(2021, 12, 31))

    def test_rejects_range_after_frozen_window(self) -> None:
        _, frozen = self._frozen()
        with self.assertRaisesRegex(FrozenDataError, "outside the frozen window"):
            frozen.daily_quotes(Market.HK, "0001.HK", date(2019, 1, 1), date(2025, 1, 1))

    def test_rejects_empty_range(self) -> None:
        _, frozen = self._frozen()
        with self.assertRaisesRegex(FrozenDataError, "empty read range"):
            frozen.daily_quotes(Market.HK, "0001.HK", date(2021, 12, 31), date(2019, 1, 1))

    def test_rejects_range_beyond_data_cutoff(self) -> None:
        _, frozen = self._frozen(data_cutoff=date(2024, 6, 30))
        with self.assertRaisesRegex(FrozenDataError, "after the frozen data cutoff"):
            frozen.daily_quotes(Market.HK, "0001.HK", date(2019, 1, 1), date(2024, 12, 31))

    def test_rejects_as_of_after_cutoff(self) -> None:
        _, frozen = self._frozen(data_cutoff=date(2024, 6, 30))
        with self.assertRaisesRegex(FrozenDataError, "after the frozen data cutoff"):
            frozen.list_securities(Market.HK, date(2024, 12, 31))

    def test_accepts_range_within_window(self) -> None:
        fake, frozen = self._frozen()
        result = frozen.daily_quotes(Market.HK, "0001.HK", date(2019, 1, 1), date(2021, 12, 31))
        self.assertEqual(result, ("canned",))


class SymbolGuardTests(unittest.TestCase):
    """Verify symbols outside the frozen historical pool are rejected (SP 3.8)."""

    def test_rejects_symbol_not_in_frozen_pool(self) -> None:
        fake, frozen = _hk_frozen()
        with self.assertRaisesRegex(FrozenDataError, "not in the frozen historical stock pool"):
            frozen.daily_quotes(Market.HK, "ZZZZ", date(2019, 1, 1), date(2021, 12, 31))

    def test_accepts_symbol_in_frozen_pool(self) -> None:
        fake, frozen = _hk_frozen()
        result = frozen.daily_quotes(Market.HK, "0001.HK", date(2019, 1, 1), date(2021, 12, 31))
        self.assertEqual(result, ("canned",))
        self.assertTrue(any("daily_quotes:HK:0001.HK" in call for call in fake.calls))


class ComponentGuardTests(unittest.TestCase):
    """Verify un-frozen components and version mismatches are rejected (SP 3.8)."""

    def test_rejects_component_not_frozen(self) -> None:
        manifest = _manifest(
            components=(
                _component(ManifestComponent.PRICES),
                _component(ManifestComponent.STOCK_POOL),
            )
        )
        frozen = FrozenDataReader(_FakeReader(), manifest)
        with self.assertRaisesRegex(FrozenDataError, "dividends is not frozen in the manifest"):
            frozen.dividends(Market.HK, "0001.HK", date(2019, 1, 1), date(2021, 12, 31))

    def test_rejects_served_version_mismatch(self) -> None:
        fake, _ = _hk_frozen()
        versioned = FrozenDataReader(
            fake,
            _manifest(),
            served_versions={ManifestComponent.PRICES: "2025-01"},
        )
        with self.assertRaisesRegex(FrozenDataError, "data version mismatch for prices"):
            versioned.daily_quotes(Market.HK, "0001.HK", date(2019, 1, 1), date(2021, 12, 31))

    def test_accepts_matching_served_version(self) -> None:
        fake, _ = _hk_frozen()
        versioned = FrozenDataReader(
            fake,
            _manifest(),
            served_versions={ManifestComponent.PRICES: "2024-12"},
        )
        result = versioned.daily_quotes(Market.HK, "0001.HK", date(2019, 1, 1), date(2021, 12, 31))
        self.assertEqual(result, ("canned",))

    def test_version_check_skipped_when_served_unknown(self) -> None:
        fake, frozen = _hk_frozen()
        result = frozen.daily_quotes(Market.HK, "0001.HK", date(2019, 1, 1), date(2021, 12, 31))
        self.assertEqual(result, ("canned",))


class DelegationTests(unittest.TestCase):
    """Verify each read delegates to the underlying MVP 2 reader (SP 3.8)."""

    def test_delegates_daily_quotes(self) -> None:
        fake, frozen = _hk_frozen()
        frozen.daily_quotes(Market.HK, "0001.HK", date(2019, 1, 1), date(2021, 12, 31))
        self.assertIn("daily_quotes:HK:0001.HK:2019-01-01:2021-12-31", fake.calls)

    def test_delegates_dividends(self) -> None:
        fake, frozen = _hk_frozen()
        frozen.dividends(Market.HK, "0001.HK", date(2019, 1, 1), date(2021, 12, 31))
        self.assertIn("dividends:HK:0001.HK:2019-01-01:2021-12-31", fake.calls)

    def test_delegates_fundamentals(self) -> None:
        fake, frozen = _hk_frozen()
        frozen.fundamentals(Market.HK, "0001.HK", date(2021, 12, 31))
        self.assertIn("fundamentals:HK:0001.HK:2021-12-31", fake.calls)

    def test_delegates_corporate_actions(self) -> None:
        fake, frozen = _hk_frozen()
        frozen.corporate_actions(Market.HK, "0001.HK", date(2019, 1, 1), date(2021, 12, 31))
        self.assertTrue(any("corporate_actions:HK:0001.HK" in call for call in fake.calls))

    def test_delegates_adjustment_factors(self) -> None:
        fake, frozen = _hk_frozen()
        frozen.adjustment_factors(Market.HK, "0001.HK", date(2019, 1, 1), date(2021, 12, 31))
        self.assertTrue(any("adjustment_factors:HK:0001.HK" in call for call in fake.calls))

    def test_delegates_list_securities(self) -> None:
        fake, frozen = _hk_frozen()
        pool = frozen.list_securities(Market.HK, date(2021, 12, 31))
        self.assertEqual(pool, ("0001.HK", "0002.HK"))
        self.assertIn("list_securities:HK:2021-12-31", fake.calls)


if __name__ == "__main__":
    unittest.main()
