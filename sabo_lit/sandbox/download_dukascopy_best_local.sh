#!/usr/bin/env bash
set -u -o pipefail

# Best local data pack without external storage.
#
# Full tick CSV for the requested 22-instrument universe is not realistic on the
# current internal disk. This script prioritizes data that is actually useful
# for strategy mining:
#   1. H1 bid/ask for the full universe, 2012-2026.
#   2. M1 bid/ask for high-value instruments only, 2020-2026.
#
# It is resumable via .done/.failed markers and stops before disk gets low.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_ROOT="${DATA_ROOT:-$ROOT_DIR/data}"
MIN_FREE_GB="${MIN_FREE_GB:-20}"
FORMAT="${FORMAT:-csv}"

RUN_H1_ALL="${RUN_H1_ALL:-1}"
RUN_M1_PRIORITY="${RUN_M1_PRIORITY:-1}"

H1_FROM="${H1_FROM:-2012-01-01}"
H1_TO="${H1_TO:-2026-01-01}"
M1_FROM="${M1_FROM:-2020-01-01}"
M1_TO="${M1_TO:-2026-01-01}"

PRICE_TYPES=(bid ask)

ALL_SYMBOLS=(
  eurusd gbpusd usdjpy usdchf audusd usdcad nzdusd
  gbpjpy eurjpy audjpy cadjpy chfjpy
  eurgbp eurcad euraud
  xauusd xagusd
  usatechidxusd usa30idxusd usa500idxusd deuidxeur
  lightcmdusd brentcmdusd
)

PRIORITY_M1_SYMBOLS=(
  xauusd usatechidxusd usa30idxusd usa500idxusd deuidxeur lightcmdusd brentcmdusd
  eurusd gbpusd usdjpy eurjpy gbpjpy
)

if [[ -n "${SYMBOLS_OVERRIDE:-}" ]]; then
  # Space-separated list, e.g. SYMBOLS_OVERRIDE="eurusd xauusd".
  # shellcheck disable=SC2206
  ALL_SYMBOLS=($SYMBOLS_OVERRIDE)
  # shellcheck disable=SC2206
  PRIORITY_M1_SYMBOLS=($SYMBOLS_OVERRIDE)
fi

free_gb() {
  df -g "$DATA_ROOT" | awk 'NR==2 {print $4}'
}

check_disk() {
  local context="$1"
  if (( "$(free_gb)" < MIN_FREE_GB )); then
    echo "ERROR: free disk below ${MIN_FREE_GB}GiB before ${context}; stopping." >&2
    exit 2
  fi
}

emit_chunks() {
  local chunk_type="$1"
  local from="$2"
  local to="$3"
  python3 - "$chunk_type" "$from" "$to" <<'PY'
import sys
from datetime import date

chunk_type, start_s, end_s = sys.argv[1:4]
start = date.fromisoformat(start_s)
end = date.fromisoformat(end_s)

if chunk_type == "year":
    for year in range(start.year, end.year):
        chunk_from = max(date(year, 1, 1), start)
        chunk_to = min(date(year + 1, 1, 1), end)
        if chunk_from < chunk_to:
            print(f"{chunk_from},{chunk_to},{year}")
elif chunk_type == "month":
    current = date(start.year, start.month, 1)
    while current < end:
        nxt = date(current.year + 1, 1, 1) if current.month == 12 else date(current.year, current.month + 1, 1)
        chunk_from = max(current, start)
        chunk_to = min(nxt, end)
        if chunk_from < chunk_to:
            print(f"{chunk_from},{chunk_to},{chunk_from:%Y-%m}")
        current = nxt
else:
    raise SystemExit(f"unknown chunk_type={chunk_type}")
PY
}

download_stage() {
  local stage="$1"
  local timeframe="$2"
  local from="$3"
  local to="$4"
  local chunk_type="$5"
  shift 5
  local symbols=("$@")

  local out_dir="$DATA_ROOT/dukascopy_${stage}_${timeframe}_${from}_${to}"
  local log_dir="$out_dir/logs"
  local chunks_file="$out_dir/.chunks"
  mkdir -p "$out_dir" "$log_dir"
  emit_chunks "$chunk_type" "$from" "$to" > "$chunks_file"

  echo
  echo "==== STAGE $stage ===="
  echo "Output : $out_dir"
  echo "Range  : $from -> $to"
  echo "Type   : $timeframe / $FORMAT / bid+ask"
  echo "Symbols: ${#symbols[@]}"
  echo "Free   : $(free_gb) GiB"
  echo

  local chunk_limit="${MAX_CHUNKS_PER_SIDE:-0}"
  for symbol in "${symbols[@]}"; do
    for price_type in "${PRICE_TYPES[@]}"; do
      local side_done="$out_dir/.${symbol}.${price_type}.done"
      if [[ -f "$side_done" ]]; then
        echo "SKIP $stage $symbol $price_type (done marker exists)"
        continue
      fi

      local symbol_dir="$out_dir/$symbol/$price_type"
      mkdir -p "$symbol_dir"
      echo "DOWNLOAD $stage $symbol $price_type"

      local chunks_seen=0
      while IFS=, read -r chunk_from chunk_to chunk_label; do
        check_disk "$stage $symbol $price_type $chunk_label"

        if (( chunk_limit > 0 && chunks_seen >= chunk_limit )); then
          echo "  STOP $symbol $price_type after MAX_CHUNKS_PER_SIDE=$chunk_limit"
          break
        fi
        chunks_seen=$((chunks_seen + 1))

        local done_stamp="$out_dir/.${symbol}.${price_type}.${chunk_label}.done"
        local fail_stamp="$out_dir/.${symbol}.${price_type}.${chunk_label}.failed"
        local log_file="$log_dir/${symbol}-${price_type}-${chunk_label}.log"
        if [[ -f "$done_stamp" ]]; then
          echo "  SKIP $symbol $price_type $chunk_label"
          continue
        fi

        echo "  DOWNLOAD $symbol $price_type $chunk_label"
        npx --yes dukascopy-node \
          -i "$symbol" \
          -from "$chunk_from" \
          -to "$chunk_to" \
          -t "$timeframe" \
          -p "$price_type" \
          -f "$FORMAT" \
          -dir "$symbol_dir" \
          -fn "${symbol}-${price_type}-${timeframe}-${chunk_from}-${chunk_to}" \
          -bs 50 \
          -bp 500 \
          -r 3 \
          -rp 2000 \
          -re \
          -fr 2>&1 | tee "$log_file"

        local status="${PIPESTATUS[0]}"
        if [[ "$status" -eq 0 ]]; then
          rm -f "$fail_stamp"
          touch "$done_stamp"
          echo "  DONE $symbol $price_type $chunk_label"
        else
          touch "$fail_stamp"
          echo "  FAIL $symbol $price_type $chunk_label (status $status); continuing" >&2
        fi
      done < "$chunks_file"

      if ! compgen -G "$out_dir/.${symbol}.${price_type}.*.failed" > /dev/null; then
        touch "$side_done"
        echo "DONE $stage $symbol $price_type"
      else
        echo "PARTIAL $stage $symbol $price_type (some chunks failed)"
      fi
      echo "Free: $(free_gb) GiB"
      echo
    done
  done
}

mkdir -p "$DATA_ROOT"
echo "Best-local Dukascopy acquisition"
echo "Data root: $DATA_ROOT"
echo "Free disk: $(free_gb) GiB"
echo "Min free : ${MIN_FREE_GB} GiB"

if [[ "$RUN_H1_ALL" == "1" ]]; then
  download_stage "research_full" "h1" "$H1_FROM" "$H1_TO" "year" "${ALL_SYMBOLS[@]}"
fi

if [[ "$RUN_M1_PRIORITY" == "1" ]]; then
  download_stage "research_priority" "m1" "$M1_FROM" "$M1_TO" "month" "${PRIORITY_M1_SYMBOLS[@]}"
fi

echo "Best-local acquisition pass completed."
