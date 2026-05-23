"""
Sandbox — QuantStats HTML tear sheets for validated FX strategies.

Per ChatGPT review recommendation. Generates rich HTML reports with:
  - Equity curve + drawdown plot
  - Monthly/yearly return heatmap
  - Risk metrics (Sortino, Calmar, VaR, CVaR, tail ratio, etc.)
  - Rolling Sharpe + rolling vol + rolling beta
  - Worst drawdown table + recovery time
  - Distribution histograms
  - Monte Carlo equity simulations
  - SPY benchmark comparison (downloaded via yfinance)

One HTML per spec + one for combined portfolio.
"""
from __future__ import annotations

import math
import warnings
from pathlib import Path

import pandas as pd
import quantstats as qs

import strategy_propfirm_cashmax as cash


warnings.filterwarnings("ignore")

HERE = Path(__file__).parent
OUT_DIR = HERE / "reports"
OUT_DIR.mkdir(exist_ok=True)

SPECS_TO_REPORT = ["FX_MR_STACK", "NO_EUR_STACK", "COMDOLL_STACK", "EURUSD_MR5"]
VOL_TARGET = 0.10  # main production target
ANN_DAYS = 252


def to_quantstats_returns(daily_pl_decimal: pd.Series) -> pd.Series:
    """QuantStats expects daily returns as a Series with DatetimeIndex.
    Our daily P&L is already in decimal return units (e.g., 0.005 = +0.5%)."""
    s = daily_pl_decimal.dropna().copy()
    # QuantStats requires tz-naive index
    if s.index.tz is not None:
        s.index = s.index.tz_localize(None)
    s.name = "strategy"
    return s


def levered_returns(daily: pd.Series, intraday: pd.Series, target_vol: float) -> pd.Series:
    realized = daily.rolling(60).std() * math.sqrt(ANN_DAYS)
    lev = (target_vol / realized).clip(upper=cash.MAX_LEVERAGE).shift(1).fillna(1.0)
    return (daily * lev).dropna()


def generate_report(spec_name: str, returns: pd.Series, output_path: Path,
                    benchmark: str = "SPY") -> None:
    """Generate QuantStats HTML tear sheet for one strategy."""
    try:
        qs.reports.html(
            returns,
            benchmark=benchmark,
            output=str(output_path),
            title=f"FX MR Strategy — {spec_name}",
            download_filename=output_path.name,
        )
        print(f"  wrote {output_path}")
    except Exception as e:
        # Fallback to no benchmark if SPY download fails
        print(f"  benchmark failed ({e}), retrying without SPY...")
        qs.reports.html(
            returns,
            output=str(output_path),
            title=f"FX MR Strategy — {spec_name} (no benchmark)",
            download_filename=output_path.name,
        )
        print(f"  wrote {output_path} (no benchmark)")


def print_quick_stats(spec_name: str, returns: pd.Series) -> None:
    """Print key risk metrics directly."""
    print(f"\n=== {spec_name} (vol-target {int(VOL_TARGET*100)}%) ===")
    print(f"  Sharpe       : {qs.stats.sharpe(returns):+.2f}")
    print(f"  Sortino      : {qs.stats.sortino(returns):+.2f}")
    print(f"  Calmar       : {qs.stats.calmar(returns):+.2f}")
    print(f"  CAGR%        : {qs.stats.cagr(returns)*100:+.2f}")
    print(f"  Max DD       : {qs.stats.max_drawdown(returns)*100:.2f}%")
    print(f"  Vol (ann)    : {qs.stats.volatility(returns)*100:.2f}%")
    print(f"  Best day     : {returns.max()*100:+.2f}%")
    print(f"  Worst day    : {returns.min()*100:+.2f}%")
    print(f"  Skew         : {qs.stats.skew(returns):+.2f}")
    print(f"  Kurtosis     : {qs.stats.kurtosis(returns):+.2f}")
    print(f"  Tail ratio   : {qs.stats.tail_ratio(returns):.2f}")
    print(f"  Payoff ratio : {qs.stats.payoff_ratio(returns):.2f}")
    var = qs.stats.value_at_risk(returns)
    cvar = qs.stats.cvar(returns)
    print(f"  Daily VaR95  : {var*100:+.2f}%")
    print(f"  Daily CVaR95 : {cvar*100:+.2f}%")
    ulcer = qs.stats.ulcer_index(returns)
    print(f"  Ulcer index  : {ulcer:.2f}")
    print(f"  Win rate     : {qs.stats.win_rate(returns)*100:.1f}%")


def main() -> None:
    print("Loading FX strategy universe...")
    specs = cash.build_strategy_universe()
    print(f"  Available: {list(specs)}")

    # Build levered returns for each validated spec
    print(f"\nApplying vol-target {VOL_TARGET*100:.0f}% to each spec...")
    levered_streams = {}
    for spec_name in SPECS_TO_REPORT:
        if spec_name not in specs:
            print(f"  SKIP {spec_name} (not available)")
            continue
        daily = specs[spec_name]["daily"]
        intraday = specs[spec_name]["intraday"]
        lev = levered_returns(daily, intraday, VOL_TARGET)
        levered_streams[spec_name] = lev
        print(f"  {spec_name}: {len(lev)} levered daily obs")

    # Print quick stats
    for spec_name, lev in levered_streams.items():
        returns = to_quantstats_returns(lev)
        print_quick_stats(spec_name, returns)

    # Combined portfolio : equal-weight of 3 ROBUST specs
    robust_specs = ["FX_MR_STACK", "NO_EUR_STACK", "COMDOLL_STACK"]
    aligned = pd.DataFrame({s: levered_streams[s] for s in robust_specs if s in levered_streams})
    portfolio_levered = aligned.dropna().mean(axis=1)
    portfolio_returns = to_quantstats_returns(portfolio_levered)
    print_quick_stats("ROBUST_PORTFOLIO_3spec_eqwt", portfolio_returns)

    # Generate HTML reports
    print(f"\nGenerating QuantStats HTML tear sheets to {OUT_DIR}...")
    for spec_name, lev in levered_streams.items():
        returns = to_quantstats_returns(lev)
        out_path = OUT_DIR / f"quantstats_{spec_name.lower()}.html"
        generate_report(spec_name, returns, out_path)

    # Portfolio report
    portfolio_out = OUT_DIR / "quantstats_portfolio_3robust_eqwt.html"
    generate_report("ROBUST_PORTFOLIO_3spec_eqwt", portfolio_returns, portfolio_out)

    print("\ndone")
    print(f"reports dir: {OUT_DIR}")
    for f in sorted(OUT_DIR.glob("quantstats_*.html")):
        print(f"  {f.name}")


if __name__ == "__main__":
    main()
