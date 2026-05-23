#!/usr/bin/env bash
set -euo pipefail

FROM="${FROM:-2012-01-01}"
TO="${TO:-2026-01-01}"
TIMEFRAME="${TIMEFRAME:-tick}"
FORMAT="${FORMAT:-csv}"
MIN_FREE_GB="${MIN_FREE_GB:-10}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/data/dukascopy_${TIMEFRAME}_${FROM}_${TO}}"
LOG_DIR="$OUT_DIR/logs"

SYMBOLS=(
  eurusd gbpusd usdjpy usdchf audusd usdcad nzdusd
  gbpjpy eurjpy audjpy cadjpy chfjpy
  eurgbp eurcad euraud
  xauusd xagusd
  usa100idxusd us30idxusd usa500idxusd deuidxeur
  usoilusd
)

mkdir -p "$OUT_DIR" "$LOG_DIR"
MONTH_CHUNKS_FILE="$OUT_DIR/.month_chunks"
python3 - "$FROM" "$TO" > "$MONTH_CHUNKS_FILE" <<'PY'
import sys
from datetime import date

start = date.fromisoformat(sys.argv[1])
end = date.fromisoformat(sys.argv[2])
current = date(start.year, start.month, 1)
while current < end:
    if current.month == 12:
        nxt = date(current.year + 1, 1, 1)
    else:
        nxt = date(current.year, current.month + 1, 1)
    chunk_from = max(current, start)
    chunk_to = min(nxt, end)
    if chunk_from < chunk_to:
        print(f"{chunk_from},{chunk_to}")
    current = nxt
PY

free_gb() {
  df -g "$OUT_DIR" | awk 'NR==2 {print $4}'
}

echo "Output: $OUT_DIR"
echo "Range : $FROM -> $TO"
echo "Type  : $TIMEFRAME / $FORMAT"
echo "Free  : $(free_gb) GiB"
echo

for symbol in "${SYMBOLS[@]}"; do
  if (( "$(free_gb)" < MIN_FREE_GB )); then
    echo "ERROR: free disk below ${MIN_FREE_GB}GiB, stopping before $symbol" >&2
    exit 2
  fi

  symbol_done_file="$OUT_DIR/.${symbol}.done"
  if [[ -f "$symbol_done_file" ]]; then
    echo "SKIP $symbol (done marker exists)"
    continue
  fi

  echo "DOWNLOAD $symbol by monthly chunks"
  symbol_dir="$OUT_DIR/$symbol"
  mkdir -p "$symbol_dir"
  while IFS=, read -r chunk_from chunk_to; do
    if (( "$(free_gb)" < MIN_FREE_GB )); then
      echo "ERROR: free disk below ${MIN_FREE_GB}GiB, stopping before $symbol $chunk_from" >&2
      exit 2
    fi
    chunk_label="${chunk_from:0:7}"
    chunk_stamp="$OUT_DIR/.${symbol}.${chunk_label}.done"
    log_file="$LOG_DIR/${symbol}-${chunk_label}.log"
    if [[ -f "$chunk_stamp" ]]; then
      echo "  SKIP $symbol $chunk_label (done marker exists)"
      continue
    fi
    echo "  DOWNLOAD $symbol $chunk_label"
    npx --yes dukascopy-node \
      -i "$symbol" \
      -from "$chunk_from" \
      -to "$chunk_to" \
      -t "$TIMEFRAME" \
      -f "$FORMAT" \
      -dir "$symbol_dir" \
      -fn "${symbol}-${TIMEFRAME}-${chunk_from}-${chunk_to}" \
      -bs 50 \
      -bp 500 \
      -r 3 \
      -rp 2000 \
      -re 2>&1 | tee "$log_file"

    touch "$chunk_stamp"
    echo "  DONE $symbol $chunk_label"
  done < "$MONTH_CHUNKS_FILE"

  touch "$symbol_done_file"
  echo "DONE $symbol"
  echo "Free: $(free_gb) GiB"
  echo
done

echo "All requested downloads completed."
