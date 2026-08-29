#!/usr/bin/env bash
# Harbor convenience commands — source this file to define hstart / hfetch /
# hclean / hshow. Add the following line to your ~/.bashrc to make them always
# available:
#
#   source ~/projects/Harbor/scripts/harbor.sh
#
#   hstart              start postgres+redis, load .env, apply migrations
#   hfetch [HK|US|ALL] [START] [END]
#                       refresh the security list (Wikipedia) then backfill any
#                       daily quotes missing between START (default 2020-01-01)
#                       and END (default the latest trading day); idempotent —
#                       existing data is left untouched, only missing rows are
#                       inserted
#   hclean              TRUNCATE all data tables (keep schema) so data can be
#                       re-pulled
#   hshow               show the securities and daily-quote state of the DB
#   hbacktest [hk|us|cross|CONFIG]
#                       run a backtest with the given config; shorthands
#                       hk/us/cross map to examples/configs/*_quarterly.yaml

HARBOR_DIR="${HARBOR_DIR:-$HOME/projects/Harbor}"
_HARBOR_VENV_BIN="$HARBOR_DIR/.venv/bin"

_harbor_cd() {
  cd "$HARBOR_DIR" || return 1
}

hstart() {
  local old="$PWD"
  _harbor_cd || return 1
  docker compose up -d postgres redis
  set -a && source .env && set +a
  "$_HARBOR_VENV_BIN/alembic" upgrade head
  cd "$old"
}

hfetch() {
  local old="$PWD"
  local market="${1:-ALL}"
  local start_date="${2:-2020-01-01}"
  local end_date="${3:-}"
  case "$market" in
    HK | US | ALL) ;;
    *)
      echo "usage: hfetch [HK|US|ALL] [START] [END]" >&2
      return 2
      ;;
  esac
  if [[ -z "$end_date" ]]; then
    # end defaults to the latest trading day (weekend-adjusted). A public
    # holiday is harmless: yfinance returns only real trading days, so the
    # idempotent upsert simply backfills whatever is missing in the window.
    case "$(date +%u)" in
      6) end_date="$(date -d '-1 day' +%F)" ;;   # Sat -> Friday
      7) end_date="$(date -d '-2 days' +%F)" ;;  # Sun -> Friday
      *) end_date="$(date +%F)" ;;               # Mon-Fri -> today
    esac
  fi
  if [[ "$start_date" > "$end_date" ]]; then
    echo "hfetch: START ($start_date) must not be after END ($end_date)" >&2
    return 2
  fi
  _harbor_cd || return 1
  # The idempotent upsert skips rows that already exist, so hfetch only
  # backfills the daily data missing between START and END.

  # 1) refresh the security list (HSI / S&P 500 parsed from Wikipedia).
  if [[ "$market" == "ALL" || "$market" == "HK" ]]; then
    "$_HARBOR_VENV_BIN/harbor-cli" fetch securities --market HK
  fi
  if [[ "$market" == "ALL" || "$market" == "US" ]]; then
    "$_HARBOR_VENV_BIN/harbor-cli" fetch securities --market US
  fi

  # 2) fetch daily quotes over the configured range.
  if [[ "$market" == "ALL" || "$market" == "HK" ]]; then
    "$_HARBOR_VENV_BIN/harbor-cli" fetch daily --market HK --all \
      --start "$start_date" --end "$end_date"
  fi
  if [[ "$market" == "ALL" || "$market" == "US" ]]; then
    "$_HARBOR_VENV_BIN/harbor-cli" fetch daily --market US --all \
      --start "$start_date" --end "$end_date"
  fi
  cd "$old"
}

hclean() {
  docker exec harbor-postgres-1 psql -U harbor -d harbor \
    -c "DO \$do\$ DECLARE r record; BEGIN FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename<>'alembic_version') LOOP EXECUTE format('TRUNCATE TABLE %I CASCADE', r.tablename); END LOOP; END \$do\$;"
}

hshow() {
  docker exec harbor-postgres-1 psql -U harbor -d harbor \
    -c "SELECT market, COUNT(*) AS securities FROM securities GROUP BY market ORDER BY market;"
  docker exec harbor-postgres-1 psql -U harbor -d harbor \
    -c "SELECT market, COUNT(*) AS quotes, MIN(date) AS first, MAX(date) AS last FROM daily_quotes GROUP BY market ORDER BY market;"
  docker exec harbor-postgres-1 psql -U harbor -d harbor \
    -c "SELECT market, symbol, name FROM securities ORDER BY market, symbol LIMIT 10;"
  echo "--- HK daily: first 10 rows ---"
  docker exec harbor-postgres-1 psql -U harbor -d harbor \
    -c "SELECT market, symbol, date, open, high, low, close, volume FROM daily_quotes WHERE market='HK' ORDER BY date ASC, symbol LIMIT 10;"
  echo "--- HK daily: last 10 rows ---"
  docker exec harbor-postgres-1 psql -U harbor -d harbor \
    -c "SELECT market, symbol, date, open, high, low, close, volume FROM daily_quotes WHERE market='HK' ORDER BY date DESC, symbol LIMIT 10;"
  echo "--- US daily: first 10 rows ---"
  docker exec harbor-postgres-1 psql -U harbor -d harbor \
    -c "SELECT market, symbol, date, open, high, low, close, volume FROM daily_quotes WHERE market='US' ORDER BY date ASC, symbol LIMIT 10;"
  echo "--- US daily: last 10 rows ---"
  docker exec harbor-postgres-1 psql -U harbor -d harbor \
    -c "SELECT market, symbol, date, open, high, low, close, volume FROM daily_quotes WHERE market='US' ORDER BY date DESC, symbol LIMIT 10;"
}

hbacktest() {
  local old="$PWD"
  local config="$1"
  case "$config" in
    "" | hk) config="examples/configs/hk_quarterly.yaml" ;;
    us) config="examples/configs/us_quarterly.yaml" ;;
    cross) config="examples/configs/cross_market_quarterly.yaml" ;;
  esac
  _harbor_cd || return 1
  if [[ ! -f "$config" ]]; then
    echo "hbacktest: config not found: $config" >&2
    return 2
  fi
  "$_HARBOR_VENV_BIN/harbor-cli" backtest run --config "$config"
  cd "$old"
}
