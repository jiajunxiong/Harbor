#!/usr/bin/env python
"""Batch-fetch daily quotes for every registered security (HSI + S&P 500).

NOTE: the same batch logic is now built into the CLI —
``harbor-cli fetch daily --market <HK|US> --all [--limit N] [--delay S]``
is the canonical way to run it. This standalone script is retained as a
convenience (its ``--market ALL`` iterates both markets in one call).

Reads the securities universe from the Harbor database and backfills daily
quotes per symbol over a configurable date range. It reuses Harbor's own
``DailyQuoteIngestor`` so the writes are idempotent (``ON CONFLICT DO NOTHING``)
and the batch is safe to re-run: already-fetched symbols simply add 0 new rows.

Per-symbol behaviour:
  - every symbol commits in its own transaction (a failure rolls back only
    that symbol, never the whole batch);
  - transient/network errors and genuine data gaps (e.g. a Yahoo-side missing
    symbol) are recorded in a failure list and do not stop the batch;
  - a small delay between symbols (``--delay``) avoids hammering yfinance.

Usage:
  python scripts/batch_fetch_daily.py --market ALL --start 2019-01-01 --end 2024-12-31
  python scripts/batch_fetch_daily.py --market HK --limit 5   # smoke test
"""

import argparse
import time
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import create_engine, text

from harbor.config import MarketTarget, Settings
from harbor.core.ingestion import DailyQuoteIngestor
from harbor.core.interfaces import MarketDataProvider
from harbor.infrastructure.data_providers.factory import create_provider
from harbor.storage.repositories import Repository

_MARKET_TARGETS: dict[str, MarketTarget] = {
    "HK": MarketTarget.HK,
    "US": MarketTarget.US,
}


def _symbols(engine, market: str) -> list[tuple[str, str]]:
    """Return ``(market, symbol)`` rows for a market (or all markets)."""
    query = "SELECT market, symbol FROM securities"
    params: dict[str, str] = {}
    if market != "ALL":
        query += " WHERE market = :market"
        params = {"market": market}
    query += " ORDER BY market, symbol"
    with engine.connect() as connection:
        return [tuple(row) for row in connection.execute(text(query), params)]  # type: ignore[return-value]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch-fetch daily quotes for the registered securities universe."
    )
    parser.add_argument("--market", choices=("HK", "US", "ALL"), default="ALL")
    parser.add_argument("--start", default="2019-01-01", help="Start date (YYYY-MM-DD).")
    parser.add_argument("--end", default="2024-12-31", help="End date (YYYY-MM-DD).")
    parser.add_argument(
        "--delay",
        type=float,
        default=0.2,
        help="Seconds to wait between symbols (yfinance rate limiting).",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="Max symbols to fetch (0 = all)."
    )
    args = parser.parse_args()

    settings = Settings()  # type: ignore[call-arg]
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if end < start:
        parser.error("--end must not be earlier than --start.")
    engine = create_engine(settings.database_url)

    symbols = _symbols(engine, args.market)
    if args.limit:
        symbols = symbols[: args.limit]
    print(f"batch: {len(symbols)} symbols, {start.isoformat()} .. {end.isoformat()}")

    # One provider per market (stateless, reused across symbols and transactions).
    market_names = list(_MARKET_TARGETS) if args.market == "ALL" else [args.market]
    providers: dict[str, MarketDataProvider] = {}
    for name in market_names:
        provider_name = (
            settings.data_provider_us if name == "US" else settings.data_provider_hk
        )
        providers[name] = create_provider(_MARKET_TARGETS[name], provider_name)

    stats = {"ok": 0, "empty": 0, "failed": 0, "rows": 0}
    failures: list[tuple[str, str, str]] = []

    for market, symbol in symbols:
        target = _MARKET_TARGETS[market]
        provider = providers[market]
        try:
            with engine.begin() as connection:
                repository = Repository(connection)
                run_id = uuid.uuid4().hex
                repository.create_ingestion_run(
                    market, run_id, "yfinance", datetime.now(timezone.utc)
                )
                ingested = DailyQuoteIngestor(repository, run_id=run_id).ingest(
                    provider, target, symbol, start, end
                )
            stats["rows"] += ingested
            if ingested:
                stats["ok"] += 1
            else:
                stats["empty"] += 1
                failures.append((market, symbol, "no data in range"))
            print(f"  {market} {symbol}: {ingested} rows", flush=True)
        except Exception as error:  # noqa: BLE001 - one bad symbol must not stop the batch
            stats["failed"] += 1
            failures.append((market, symbol, str(error)))
            print(f"  {market} {symbol}: FAILED {error}", flush=True)
        time.sleep(args.delay)

    print(
        f"done: ok={stats['ok']} empty={stats['empty']} failed={stats['failed']} "
        f"rows={stats['rows']}"
    )
    if failures:
        print("failures / empty:")
        for market, symbol, reason in failures:
            print(f"  {market} {symbol}: {reason}")


if __name__ == "__main__":
    main()
