#!/usr/bin/env bash
set -u -o pipefail

FROM="${FROM:-2012-01-01}"
TO="${TO:-2026-01-01}"
TIMEFRAME="${TIMEFRAME:-h1}"
FORMAT="${FORMAT:-csv}"
MIN_FREE_GB="${MIN_FREE_GB:-10}"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/data/dukascopy_${TIMEFRAME}_bidask_${FROM}_${TO}}"
LOG_DIR="$OUT_DIR/logs"

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
    echo "DOWNLOAD $symbol $price_type yearly chunks"

    for year in $(seq "${FROM:0:4}" $(( ${TO:0:4} - 1 ))); do
      if (( "$(free_gb)" < MIN_FREE_GB )); then
        echo "ERROR: free disk below ${MIN_FREE_GB}GiB, stopping before $symbol $price_type $year" >&2
        exit 2
      fi

      chunk_from="${year}-01-01"
      chunk_to="$((year + 1))-01-01"
      chunk_stamp="$OUT_DIR/.${symbol}.${price_type}.${year}.done"
      fail_stamp="$OUT_DIR/.${symbol}.${price_type}.${year}.failed"
      log_file="$LOG_DIR/${symbol}-${price_type}-${year}.log"

      if [[ -f "$chunk_stamp" ]]; then
        echo "  SKIP $symbol $price_type $year (done marker exists)"
        continue
      fi

      echo "  DOWNLOAD $symbol $price_type $year"
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
        echo "  DONE $symbol $price_type $year"
      else
        touch "$fail_stamp"
        echo "  FAIL $symbol $price_type $year (status $status); continuing" >&2
      fi
    done

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
