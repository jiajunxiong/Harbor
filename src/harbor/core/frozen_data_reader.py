"""Frozen dataset data reader (MVP 3 / SP 3.8).

Wraps the MVP 2 point-in-time :class:`BacktestDataReader` (SP 2.8) and
restricts every read to the frozen dataset manifest (SP 3.6): a market
outside the manifest, a date range outside the frozen window or data cutoff,
a symbol absent from the frozen historical stock pool, a data component that
is not frozen in the manifest, or a served data version that differs from the
frozen component version is rejected with :class:`FrozenDataError` instead of
silently returning data outside the frozen boundary.

Reads map to manifest components: ``daily_quotes`` and ``adjustment_factors``
to ``PRICES``, ``dividends`` to ``DIVIDENDS``, ``fundamentals`` to
``FUNDAMENTALS``, ``corporate_actions`` to ``CORPORATE_ACTIONS`` and
``list_securities`` to ``STOCK_POOL``. Core layer: depends only on the
backtest interfaces and the validation-domain types, never on storage,
services or CLI code.
"""

from collections.abc import Mapping, Sequence
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.backtest_interfaces import (
    AdjustmentFactor,
    BacktestDataReader,
    DailyQuote,
    Dividend,
    FundamentalRecord,
)
from harbor.core.equity import EntitlementEvent
from harbor.core.validation_domain import (
    DataComponentManifest,
    DatasetManifest,
    ManifestComponent,
)


class FrozenDataError(ValueError):
    """Raised when a read falls outside the frozen dataset boundaries (SP 3.8)."""


class FrozenDataReader:
    """Point-in-time reads constrained to a frozen dataset (SP 3.8).

    Args:
        reader: The MVP 2 data reader serving the underlying data.
        manifest: The frozen dataset manifest describing the allowed markets,
            window, cutoff, stock pool and component versions.
        served_versions: The data versions the underlying reader actually
            serves, keyed by manifest component. When given, every read
            verifies the served version matches the frozen component version;
            a mismatch is rejected. When omitted, version matching is not
            enforced — the caller must assert versions elsewhere.
    """

    def __init__(
        self,
        reader: BacktestDataReader,
        manifest: DatasetManifest,
        served_versions: Mapping[ManifestComponent, str] | None = None,
    ) -> None:
        self._reader = reader
        self._manifest = manifest
        self._versions = dict(served_versions) if served_versions is not None else {}

    @property
    def manifest(self) -> DatasetManifest:
        """The frozen dataset manifest this reader is bound to."""
        return self._manifest

    def _guard_market(self, market: Market) -> None:
        if market not in self._manifest.markets:
            frozen = ", ".join(market_.value for market_ in self._manifest.markets)
            raise FrozenDataError(
                f"market {market.value} is not frozen in the manifest (frozen: {frozen})."
            )

    def _guard_range(self, start: date, end: date) -> None:
        if start > end:
            raise FrozenDataError(f"empty read range {start.isoformat()}..{end.isoformat()}.")
        if start < self._manifest.start_date or end > self._manifest.end_date:
            raise FrozenDataError(
                f"read range {start.isoformat()}..{end.isoformat()} is outside the frozen "
                f"window {self._manifest.start_date.isoformat()}.."
                f"{self._manifest.end_date.isoformat()}."
            )
        if end > self._manifest.data_cutoff:
            raise FrozenDataError(
                f"read range ends {end.isoformat()} after the frozen data cutoff "
                f"{self._manifest.data_cutoff.isoformat()}."
            )

    def _guard_as_of(self, as_of: date) -> None:
        if as_of < self._manifest.start_date or as_of > self._manifest.end_date:
            raise FrozenDataError(
                f"as_of {as_of.isoformat()} is outside the frozen window "
                f"{self._manifest.start_date.isoformat()}.."
                f"{self._manifest.end_date.isoformat()}."
            )
        if as_of > self._manifest.data_cutoff:
            raise FrozenDataError(
                f"as_of {as_of.isoformat()} is after the frozen data cutoff "
                f"{self._manifest.data_cutoff.isoformat()}."
            )

    def _guard_component(self, component: ManifestComponent) -> None:
        for entry in self._manifest.components:
            if entry.component is component:
                self._guard_version(component, entry)
                return
        raise FrozenDataError(f"component {component.value} is not frozen in the manifest.")

    def _guard_version(self, component: ManifestComponent, entry: DataComponentManifest) -> None:
        served = self._versions.get(component)
        if served is not None and served != entry.version:
            raise FrozenDataError(
                f"data version mismatch for {component.value}: serving {served!r} "
                f"vs frozen {entry.version!r}."
            )

    def _guard_symbol(self, market: Market, symbol: str, as_of: date) -> None:
        pool = self._reader.list_securities(market, as_of)
        if symbol not in pool:
            raise FrozenDataError(
                f"symbol {market.value}/{symbol} is not in the frozen historical "
                f"stock pool on {as_of.isoformat()}."
            )

    def list_securities(self, market: Market, as_of: date) -> Sequence[str]:
        """Return the frozen historical pool for ``market`` on ``as_of``."""
        self._guard_market(market)
        self._guard_as_of(as_of)
        self._guard_component(ManifestComponent.STOCK_POOL)
        return self._reader.list_securities(market, as_of)

    def daily_quotes(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[DailyQuote]:
        """Read daily quotes within the frozen boundaries (SP 3.8)."""
        self._guard_market(market)
        self._guard_range(start, end)
        self._guard_component(ManifestComponent.PRICES)
        self._guard_symbol(market, symbol, end)
        return self._reader.daily_quotes(market, symbol, start, end)

    def dividends(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Dividend]:
        """Read dividends within the frozen boundaries (SP 3.8)."""
        self._guard_market(market)
        self._guard_range(start, end)
        self._guard_component(ManifestComponent.DIVIDENDS)
        self._guard_symbol(market, symbol, end)
        return self._reader.dividends(market, symbol, start, end)

    def fundamentals(
        self,
        market: Market,
        symbol: str,
        as_of: date,
    ) -> Sequence[FundamentalRecord]:
        """Read fundamentals knowable on or before ``as_of`` (SP 3.8)."""
        self._guard_market(market)
        self._guard_as_of(as_of)
        self._guard_component(ManifestComponent.FUNDAMENTALS)
        self._guard_symbol(market, symbol, as_of)
        return self._reader.fundamentals(market, symbol, as_of)

    def corporate_actions(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[EntitlementEvent]:
        """Read corporate actions within the frozen boundaries (SP 3.8)."""
        self._guard_market(market)
        self._guard_range(start, end)
        self._guard_component(ManifestComponent.CORPORATE_ACTIONS)
        self._guard_symbol(market, symbol, end)
        return self._reader.corporate_actions(market, symbol, start, end)

    def adjustment_factors(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[AdjustmentFactor]:
        """Read adjusted-price factors within the frozen boundaries (SP 3.8)."""
        self._guard_market(market)
        self._guard_range(start, end)
        self._guard_component(ManifestComponent.PRICES)
        self._guard_symbol(market, symbol, end)
        return self._reader.adjustment_factors(market, symbol, start, end)
