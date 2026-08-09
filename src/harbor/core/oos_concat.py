"""OOS net-value concatenation (MVP 3 / SP 3.37).

Concatenates the non-overlapping per-fold out-of-sample net-value series
(折叠 OOS 净值) of an SP 3.35 :class:`RollingOosRun` into one time-ordered OOS
equity path (按时间顺序拼接不重叠的折叠 OOS 净值). The aggregation is refused
whenever the folds overlap in time, leave a gap, or disagree on currency
(重叠、缺口和货币不一致时拒绝汇总).

The per-fold series are produced by the injected
``net_values_for(fold) -> Sequence[NetValue]`` callback (the fold's MVP 2 run
on its unseen interval). The concatenation enforces, in fold order:

- every fold has a non-empty series (a fold with no OOS run cannot be silently
  omitted — SP 3.43);
- each series is strictly ascending and single-currency;
- consecutive series are contiguous (the next fold's first day is exactly the
  day after the previous fold's last day — no overlap, no gap);
- every series shares one currency.

The resulting :class:`OosEquityPath` records the per-fold index ranges so a
downstream fold-dispersion analysis (SP 3.39) can slice each fold's segment.

Pure core layer: depends only on the SP 3.35 run and the MVP 2 net-value
domain type, never on storage, services or CLI.
"""

import hashlib
import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta

from harbor.core.backtest_domain import Currency, NetValue
from harbor.core.rolling_oos import RollingOosRun
from harbor.core.validation_domain import WalkForwardFold


class OosConcatError(ValueError):
    """Raised when fold OOS net values cannot be concatenated (SP 3.37)."""


@dataclass(frozen=True)
class OosEquityPath:
    """The concatenated, time-ordered OOS equity path (SP 3.37).

    ``net_values`` are all folds' OOS net values in time order;
    ``fold_ranges`` maps fold_index to the inclusive ``(start, end)`` index
    range into ``net_values`` so each fold's segment is attributable.
    """

    net_values: tuple[NetValue, ...]
    currency: Currency
    fold_ranges: tuple[tuple[int, int], ...]
    dataset_fingerprint: str
    code_version: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.net_values:
            raise OosConcatError("an OOS equity path requires at least one net value.")
        if not self.dataset_fingerprint:
            raise OosConcatError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise OosConcatError("code version must be non-empty.")
        if not self.fold_ranges:
            raise OosConcatError("an OOS equity path requires at least one fold segment.")
        previous: date | None = None
        for net in self.net_values:
            if net.currency != self.currency:
                raise OosConcatError(
                    f"net value on {net.as_of_date.isoformat()} has currency "
                    f"{net.currency.value}, expected the path currency {self.currency.value}."
                )
            if previous is not None and not (previous < net.as_of_date):
                raise OosConcatError("net values must be strictly ascending by date.")
            previous = net.as_of_date
        first_start, first_end = self.fold_ranges[0]
        if first_start != 0:
            raise OosConcatError("the first fold segment must start at index 0.")
        previous_end = first_end
        for start, end in self.fold_ranges[1:]:
            if start != previous_end + 1:
                raise OosConcatError("fold segments must be contiguous in the path.")
            if not (start <= end < len(self.net_values)):
                raise OosConcatError("a fold segment is out of range.")
            previous_end = end
        if previous_end != len(self.net_values) - 1:
            raise OosConcatError("the last fold segment must end at the final index.")
        if not self.fingerprint:
            raise OosConcatError("OOS equity path fingerprint must be non-empty.")

    def __len__(self) -> int:
        return len(self.net_values)

    def __iter__(self) -> Iterator[NetValue]:
        return iter(self.net_values)

    def __getitem__(self, index: int) -> NetValue:
        return self.net_values[index]

    @property
    def start_date(self) -> date:
        """The first day of the OOS equity path."""
        return self.net_values[0].as_of_date

    @property
    def end_date(self) -> date:
        """The last day of the OOS equity path."""
        return self.net_values[-1].as_of_date

    @property
    def fold_count(self) -> int:
        """Number of fold segments in the path."""
        return len(self.fold_ranges)

    def fold_net_values(self, fold_index: int) -> tuple[NetValue, ...]:
        """Return the net values of one fold's segment (empty when absent)."""
        if fold_index < 0 or fold_index >= len(self.fold_ranges):
            return ()
        start, end = self.fold_ranges[fold_index]
        return self.net_values[start : end + 1]

    def readable(self) -> str:
        """Render the OOS equity path as one line."""
        return (
            f"OOS path {self.start_date.isoformat()}..{self.end_date.isoformat()} "
            f"{self.currency.value} {len(self.net_values)} net values across "
            f"{self.fold_count} folds fp {self.fingerprint}"
        )


def concatenate_fold_oos(
    oos_run: RollingOosRun,
    *,
    net_values_for: Callable[[WalkForwardFold], Sequence[NetValue]],
) -> OosEquityPath:
    """Concatenate the folds' OOS net values into one equity path (SP 3.37).

    Folds are processed in fold order; every series must be non-empty, strictly
    ascending and single-currency, consecutive series must be contiguous (no
    overlap, no gap) and all series must share one currency — any violation
    raises :class:`OosConcatError` instead of producing an invalid path.
    """
    merged: list[NetValue] = []
    fold_ranges: list[tuple[int, int]] = []
    path_currency: Currency | None = None
    previous_end: date | None = None
    for result in oos_run.results:
        fold = result.validation.fold
        series = tuple(net_values_for(fold))
        if not series:
            raise OosConcatError(
                f"fold {fold.fold_index} OOS has no net values; cannot concatenate "
                "without silently omitting the fold."
            )
        series_currency = series[0].currency
        prior: date | None = None
        for net in series:
            if net.currency != series_currency:
                raise OosConcatError(f"fold {fold.fold_index} net values must share one currency.")
            if prior is not None and not (prior < net.as_of_date):
                raise OosConcatError(
                    f"fold {fold.fold_index} net values must be strictly ascending by date."
                )
            prior = net.as_of_date
        if path_currency is None:
            path_currency = series_currency
        elif series_currency != path_currency:
            raise OosConcatError(
                f"fold {fold.fold_index} currency {series_currency.value} does not "
                f"match the OOS path currency {path_currency.value}."
            )
        first = series[0].as_of_date
        last = series[-1].as_of_date
        if previous_end is not None:
            if first <= previous_end:
                raise OosConcatError(
                    f"fold {fold.fold_index} OOS overlaps the previous fold: "
                    f"{first.isoformat()} is not after {previous_end.isoformat()}."
                )
            if first != previous_end + timedelta(days=1):
                raise OosConcatError(
                    f"fold {fold.fold_index} OOS leaves a gap: "
                    f"{first.isoformat()} is not the day after {previous_end.isoformat()}."
                )
        start = len(merged)
        merged.extend(series)
        fold_ranges.append((start, len(merged) - 1))
        previous_end = last

    assert path_currency is not None
    path = OosEquityPath(
        net_values=tuple(merged),
        currency=path_currency,
        fold_ranges=tuple(fold_ranges),
        dataset_fingerprint=oos_run.dataset_fingerprint,
        code_version=oos_run.code_version,
        fingerprint="unfingerprinted",
    )
    return replace(path, fingerprint=oos_concat_fingerprint(path))


def oos_concat_json(path: OosEquityPath) -> str:
    """Return a stable, key-sorted JSON serialization of an OOS equity path.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "currency": path.currency.value,
        "dataset_fingerprint": path.dataset_fingerprint,
        "code_version": path.code_version,
        "fold_ranges": [
            {"fold_index": index, "start": start, "end": end}
            for index, (start, end) in enumerate(path.fold_ranges)
        ],
        "net_values": [
            {
                "as_of_date": net.as_of_date.isoformat(),
                "currency": net.currency.value,
                "cash": net.cash,
                "securities_value": net.securities_value,
                "fees_paid": net.fees_paid,
            }
            for net in path.net_values
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def oos_concat_fingerprint(path: OosEquityPath) -> str:
    """Return the stable SHA-256 fingerprint of an OOS equity path (SP 3.37)."""
    return hashlib.sha256(oos_concat_json(path).encode("utf-8")).hexdigest()
