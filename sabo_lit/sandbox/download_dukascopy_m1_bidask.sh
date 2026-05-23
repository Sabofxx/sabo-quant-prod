#!/usr/bin/env bash
set -u -o pipefail

FROM="${FROM:-2012-01-01}"
TO="${TO:-2026-01-01}"
TIMEFRAME="${TIMEFRAME:-m1}"
FORMAT="${FORMAT:-csv}"
MIN_FREE_GB="${MIN_FREE_GB:-15}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/data/dukascopy_${TIMEFRAME}_bidask_${FROM}_${TO}}"
LOG_DIR="$OUT_DIR/logs"
MONTH_CHUNKS_FILE="$OUT_DIR/.month_chunks"

SYMBOLS=(
  eurusd gbpusd usdjpy usdchf audusd usdcad nzdusd
  gbpjpy eurjpy audjpy cadjpy chfjpy
  eurgbp eurcad euraud
  xauusd xagusd
  usa100idxusd us30idxusd usa500idxusd deuidxeur
  usoilusd
)

PRICE_TYPES=(bid ask)

mkdir -p "$OUT_DIR" "$LOG_DIR"
python3 - "$FROM" "$TO" > "$MONTH_CHUNKS_FILE" <<'PY'
import sys
from datetime import date

start = date.fromisoformat(sys.argv[1])
end = date.fromisoformat(sys.argv[2])
current = date(start.year, start.month, 1)
while current < end:
    nxt = date(current.year + 1, 1, 1) if current.month == 12 else date(current.year, current.month + 1, 1)
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
echo "Type  : $TIMEFRAME / $FORMAT / bid+ask"
echo "Free  : $(free_gb) GiB"
echo

for symbol in "${SYMBOLS[@]}"; do
  for price_type in "${PRICE_TYPES[@]}"; do
    symbol_done_file="$OUT_DIR/.${symbol}.${price_type}.done"
    if [[ -f "$symbol_done_file" ]]; then
      echo "SKIP $symbol $price_type (done marker exists)"
      continue
    fi

    symbol_dir="$OUT_DIR/$symbol/$price_type"
    mkdir -p "$symbol_dir"
    echo "DOWNLOAD $symbol $price_type monthly chunks"

    while IFS=, read -r chunk_from chunk_to; do
      if (( "$(free_gb)" < MIN_FREE_GB )); then
        echo "ERROR: free disk below ${MIN_FREE_GB}GiB, stopping before $symbol $price_type $chunk_from" >&2
        exit 2
      fi

      chunk_label="${chunk_from:0:7}"
      chunk_stamp="$OUT_DIR/.${symbol}.${price_type}.${chunk_label}.done"
      fail_stamp="$OUT_DIR/.${symbol}.${price_type}.${chunk_label}.failed"
      log_file="$LOG_DIR/${symbol}-${price_type}-${chunk_label}.log"

      if [[ -f "$chunk_stamp" ]]; then
        echo "  SKIP $symbol $price_type $chunk_label (done marker exists)"
        continue
      fi

      echo "  DOWNLOAD $symbol $price_type $chunk_label"
      npx --yes dukascopy-node \
        -i "$symbol" \
        -from "$chunk_from" \
        -to "$chunk_to" \
        -t "$TIMEFRAME" \
        -p "$price_type" \
        -f "$FORMAT" \
        -dir "$symbol_dir" \
        -fn "${symbol}-${price_type}-${TIMEFRAME}-${chunk_from}-${chunk_to}" \
        -bs 50 \
        -bp 500 \
        -r 3 \
        -rp 2000 \
        -re \
        -fr 2>&1 | tee "$log_file"

      status="${PIPESTATUS[0]}"
      if [[ "$status" -eq 0 ]]; then
        rm -f "$fail_stamp"
        touch "$chunk_stamp"
        echo "  DONE $symbol $price_type $chunk_label"
      else
        touch "$fail_stamp"
        echo "  FAIL $symbol $price_type $chunk_label (status $status); continuing" >&2
      fi
    done < "$MONTH_CHUNKS_FILE"

    # Mark symbol/side complete only if no failed chunks remain for it.
    if ! compgen -G "$OUT_DIR/.${symbol}.${price_type}.*.failed" > /dev/null; then
      touch "$symbol_done_file"
      echo "DONE $symbol $price_type"
    else
      echo "PARTIAL $symbol $price_type (some years failed)"
    fi
    echo "Free: $(free_gb) GiB"
    echo
  done
done

echo "Download pass completed."
