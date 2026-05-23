"""
Multi-asset adaptive test: FX production stack + crypto MR/TSM candidates.

Earlier crypto tests were fragile. This script re-tests them under the final
ADAPTIVE_75_50 sizing and answers one question: does adding crypto improve the
portfolio enough to justify operational complexity for prop firms?
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd

import strategy_propfirm_cashmax as cash


HERE = Path(__file__).parent
DATA = HERE / "data"
OUT_REPORT = HERE / "multi_asset_adaptive_report.md"
OUT_METRICS = HERE / "multi_asset_adaptive_metrics.json"

ANN_DAYS = 252
VOL_LOOKBACK = 60
ADAPTIVE_WINDOW = 126
MAX_LEVERAGE = 10.0
VOL_TARGET = 0.10
CRYPTO_RT_COST = 0.0020
ADAPTIVE_TIERS = [(0.3, 1.0), (0.0, 0.75), (-1e9, 0.5)]

CRYPTO_FILES = {
    "BTC": "btcusdt-d1-spot-2019-01-01-2026-01-01.csv",
    "ETH": "ethusdt-d1-spot-2019-01-01-2026-01-01.csv",
    "SOL": "solusdt-d1-spot-2020-08-11-2026-01-01.csv",
}


def metrics(pl: pd.Series) -> dict:
    pl = pl.dropna()
    if len(pl) < 30:
        return {"n": len(pl), "sharpe": 0.0, "ann_ret": 0.0, "ann_vol": 0.0, "max_dd": 0.0, "calmar": 0.0}
    mean = float(pl.mean())
    std = float(pl.std())
    ann_ret = mean * ANN_DAYS
    ann_vol = std * math.sqrt(ANN_DAYS)
    cum = pl.cumsum()
    max_dd = float((cum - cum.cummax()).min())
    return {
        "n": len(pl),
        "sharpe": mean / std * math.sqrt(ANN_DAYS) if std > 0 else 0.0,
        "ann_ret": ann_ret,
        "ann_vol": ann_vol,
        "max_dd": max_dd,
        "calmar": ann_ret / abs(max_dd) if max_dd < 0 else 0.0,
        "worst_day": float(pl.min()),
        "wr": float((pl > 0).mean()),
    }


def adaptive_75_50(daily: pd.Series, target_vol: float = VOL_TARGET) -> pd.Series:
    realized = daily.rolling(VOL_LOOKBACK).std() * math.sqrt(ANN_DAYS)
    base_lev = (target_vol / realized).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)
    static_returns = (daily * base_lev).dropna()
    rolling_mean = static_returns.rolling(ADAPTIVE_WINDOW).mean()
    rolling_std = static_returns.rolling(ADAPTIVE_WINDOW).std()
    rolling_sharpe = ((rolling_mean / rolling_std) * math.sqrt(ANN_DAYS)).shift(1)

    def to_mult(value: float) -> float:
        if pd.isna(value):
            return 1.0
        for threshold, multiplier in ADAPTIVE_TIERS:
            if value >= threshold:
                return multiplier
        return 0.5

    multiplier = rolling_sharpe.map(to_mult).fillna(1.0)
    lev = (target_vol * multiplier / realized).clip(upper=MAX_LEVERAGE).shift(1).fillna(1.0)
    return (daily * lev).dropna()


def load_crypto_close(symbol: str) -> pd.Series:
    path = DATA / CRYPTO_FILES[symbol]
    df = pd.read_csv(path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    return df["close"].astype(float)


def signal_from_returns(rets: pd.Series, lookback: int, family: str) -> pd.Series:
    cumulative = (1.0 + rets).rolling(lookback).apply(lambda values: values.prod() - 1.0, raw=True)
    if family == "TSM":
        signal = (cumulative > 0).astype(float) - (cumulative < 0).astype(float)
    elif family == "MR":
        signal = (cumulative < 0).astype(float) - (cumulative > 0).astype(float)
    else:
        raise ValueError(f"unknown family: {family}")
    return signal.shift(1)


def crypto_stream(close: pd.Series, family: str, lookback: int) -> pd.Series:
    rets = close.pct_change()
    signal = signal_from_returns(rets, lookback, family)
    common = signal.index.intersection(rets.index)
    gross = signal.loc[common] * rets.loc[common]
    turnover = signal.diff().abs().fillna(0.0) / 2.0
    cost = turnover.reindex(common).fillna(0.0) * CRYPTO_RT_COST
    net = gross - cost
    return net[net.index.weekday < 5]


def crypto_spec(closes: dict[str, pd.Series], family: str, lookback: int) -> pd.Series:
    streams = {symbol: crypto_stream(close, family, lookback) for symbol, close in closes.items()}
    return pd.DataFrame(streams).mean(axis=1).dropna()


def fx_adaptive_portfolio() -> pd.Series:
    specs = cash.build_strategy_universe()
    names = ["FX_MR_STACK", "NO_EUR_STACK", "COMDOLL_STACK"]
    streams = {name: adaptive_75_50(specs[name]["daily"], VOL_TARGET) for name in names}
    return pd.DataFrame(streams).fillna(0.0).mean(axis=1)


def main() -> None:
    print("Loading FX production stack...")
    fx = fx_adaptive_portfolio()
    print("Loading crypto daily closes...")
    closes = {symbol: load_crypto_close(symbol) for symbol in CRYPTO_FILES}

    crypto_candidates = {
        "CRYPTO_TSM63": crypto_spec(closes, "TSM", 63),
        "CRYPTO_TSM126": crypto_spec(closes, "TSM", 126),
        "CRYPTO_MR5": crypto_spec(closes, "MR", 5),
        "CRYPTO_MR10": crypto_spec(closes, "MR", 10),
    }
    crypto_adaptive = {name: adaptive_75_50(series, VOL_TARGET) for name, series in crypto_candidates.items()}

    best_crypto_name = max(crypto_adaptive, key=lambda name: metrics(crypto_adaptive[name])["sharpe"])
    best_crypto = crypto_adaptive[best_crypto_name]
    portfolios = {
        "FX_ONLY": fx,
        best_crypto_name: best_crypto,
        "FX_90_CRYPTO_10": pd.concat([fx.rename("fx"), best_crypto.rename("crypto")], axis=1).fillna(0.0).eval("fx*0.9 + crypto*0.1"),
        "FX_80_CRYPTO_20": pd.concat([fx.rename("fx"), best_crypto.rename("crypto")], axis=1).fillna(0.0).eval("fx*0.8 + crypto*0.2"),
        "FX_70_CRYPTO_30": pd.concat([fx.rename("fx"), best_crypto.rename("crypto")], axis=1).fillna(0.0).eval("fx*0.7 + crypto*0.3"),
    }

    periods = {
        "FULL": (pd.Timestamp("2019-01-01", tz="UTC"), pd.Timestamp("2025-12-31 23:59:59", tz="UTC")),
        "OOS_2024_2025": (pd.Timestamp("2024-01-01", tz="UTC"), pd.Timestamp("2025-12-31 23:59:59", tz="UTC")),
    }
    results: dict[str, dict] = {"crypto_candidates": {}, "portfolios": {}}
    for name, series in crypto_adaptive.items():
        results["crypto_candidates"][name] = {period: metrics(series.loc[start:end]) for period, (start, end) in periods.items()}
    for name, series in portfolios.items():
        results["portfolios"][name] = {period: metrics(series.loc[start:end]) for period, (start, end) in periods.items()}

    fx_full_sharpe = results["portfolios"]["FX_ONLY"]["FULL"]["sharpe"]
    fx_oos_sharpe = results["portfolios"]["FX_ONLY"]["OOS_2024_2025"]["sharpe"]
    mix_names = [name for name in results["portfolios"] if name == "FX_ONLY" or name.startswith("FX_")]
    best_combo = max(mix_names, key=lambda name: results["portfolios"][name]["FULL"]["sharpe"])
    best_combo_sharpe = results["portfolios"][best_combo]["FULL"]["sharpe"]
    best_combo_oos_sharpe = results["portfolios"][best_combo]["OOS_2024_2025"]["sharpe"]
    best_crypto_oos_sharpe = results["crypto_candidates"][best_crypto_name]["OOS_2024_2025"]["sharpe"]
    add_crypto = (
        best_combo != "FX_ONLY"
        and best_combo_sharpe > fx_full_sharpe + 0.15
        and best_combo_oos_sharpe >= fx_oos_sharpe - 0.10
        and best_crypto_oos_sharpe > 0.0
    )

    lines = [
        "# Multi-Asset Adaptive Test",
        "",
        "Question: does crypto improve the final FX prop-firm stack under ADAPTIVE_75_50?",
        "",
        "## Crypto Candidates",
        "",
        "| spec | period | Sharpe | ann% | DD% | Calmar | worst day% |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for name, period_results in results["crypto_candidates"].items():
        for period, m in period_results.items():
            lines.append(
                f"| {name} | {period} | {m['sharpe']:+.2f} | {m['ann_ret']*100:+.1f} | "
                f"{m['max_dd']*100:+.1f} | {m['calmar']:.2f} | {m['worst_day']*100:+.1f} |"
            )
    lines.extend([
        "",
        "## FX + Crypto Portfolios",
        "",
        "| portfolio | period | Sharpe | ann% | DD% | Calmar | worst day% |",
        "|---|---|---:|---:|---:|---:|---:|",
    ])
    for name, period_results in results["portfolios"].items():
        for period, m in period_results.items():
            lines.append(
                f"| {name} | {period} | {m['sharpe']:+.2f} | {m['ann_ret']*100:+.1f} | "
                f"{m['max_dd']*100:+.1f} | {m['calmar']:.2f} | {m['worst_day']*100:+.1f} |"
            )
    lines.extend([
        "",
        "## Verdict",
        "",
        f"- Best crypto candidate: `{best_crypto_name}`",
        f"- Best FX+crypto mix: `{best_combo}`",
        f"- FX full Sharpe: `{fx_full_sharpe:+.2f}`",
        f"- FX OOS Sharpe: `{fx_oos_sharpe:+.2f}`",
        f"- Best mix full Sharpe: `{best_combo_sharpe:+.2f}`",
        f"- Best mix OOS Sharpe: `{best_combo_oos_sharpe:+.2f}`",
        f"- Best crypto OOS Sharpe: `{best_crypto_oos_sharpe:+.2f}`",
        f"- Add crypto to production now: **{'YES' if add_crypto else 'NO'}**",
        "",
        "Rule: crypto must improve full-sample Sharpe by at least +0.15, keep OOS Sharpe within -0.10 of FX-only, and have positive standalone OOS Sharpe. Otherwise it stays research-only.",
        "",
        "## Caveats",
        "",
        "- Crypto is tested on spot daily bars; prop-firm crypto weekend/news rules differ by firm.",
        "- Cost model is a flat 0.20% round-trip; real prop spreads can be worse.",
        "- This does not override the production rule: current live stack remains FX-only unless improvement is large and stable.",
    ])
    OUT_REPORT.write_text("\n".join(lines), encoding="utf-8")
    payload = {"results": results, "best_crypto": best_crypto_name, "best_combo": best_combo, "add_crypto": add_crypto}
    OUT_METRICS.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print("Multi-asset adaptive complete.")
    print(f"  best_crypto={best_crypto_name}")
    print(f"  best_combo={best_combo}")
    print(f"  add_crypto={'YES' if add_crypto else 'NO'}")
    print(f"  wrote={OUT_REPORT}")


if __name__ == "__main__":
    main()
