"""
Unified validation of the PRODUCTION strategy configs (configs/*.json).

Unlike the earlier research scripts, this backtests the exact signal code the
live runner trades (_signal_series/_apply_filters/carry_signal_series +
portfolio vol-target leverage from strategy_signals) and the exact config
parameters, with the true cost profile of the executor: the daily --reset
rebalance pays one full spread per open position per day, plus a GSL premium.

Judged pre-registered, per repo discipline:
  ROBUST  = OOS(2024+) Sharpe >= 0.5  AND  full-sample block-bootstrap CI95
            low > 0  AND  lookback plateau (>=60% of neighbors OOS-positive)
            AND  worst peak-DD within -8%
  OBSERVE = OOS Sharpe > 0 and at least two of the other three
  DEAD    = otherwise

Also reports: FTMO-fit (peak DD vs -8%, daily vs -2%), the cost of resetting
unchanged positions (skip-reset saving), and program-level portfolio combos.

Run: python validate_strategy_configs.py   (from sabo_lit/sandbox)
Outputs: validate_strategy_configs_report.md + _metrics.json
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from strategy_config import StrategyConfig, CONFIG_DIR
from strategy_signals import (
    VOL_LOOKBACK, _apply_filters, _signal_series, carry_signal_series,
    load_daily_close, load_rates,
)

HERE = Path(__file__).parent
ANN = 252
OOS_START = pd.Timestamp("2024-01-01", tz="UTC")
BOOT_ITERS = 1000
BLOCK = 10
RNG = np.random.default_rng(42)

# One full spread per position per day (the --reset close+reopen crosses the
# book once) + guaranteed-stop premium. Conservative Capital.com estimates,
# in bps of notional per reset day.
COST_BPS_PER_DAY = {
    "EURUSD": 0.9, "GBPUSD": 1.1, "USDJPY": 0.9, "AUDUSD": 1.2,
    "NZDUSD": 1.5, "USDCAD": 1.2,
    "XAUUSD": 1.5, "US500": 1.0, "US100": 1.2, "DE40": 1.0,
    "OILWTI": 4.5, "US10Y": 2.5, "COPPER": 5.0,
}
GSL_PREMIUM_BPS = 0.3  # extra charged on guaranteed-stop orders


def metrics(pl: pd.Series) -> dict:
    pl = pl.dropna()
    if len(pl) < 30:
        return {}
    mu, sd = pl.mean(), pl.std()
    cum = (1 + pl).cumprod()
    dd = cum / cum.cummax() - 1
    sh = mu / sd * math.sqrt(ANN) if sd > 0 else 0.0
    return {
        "sharpe": round(sh, 2),
        "ann_ret_pct": round(mu * ANN * 100, 2),
        "ann_vol_pct": round(sd * math.sqrt(ANN) * 100, 2),
        "max_dd_pct": round(float(dd.min()) * 100, 2),
        "worst_day_pct": round(float(pl.min()) * 100, 2),
        "calmar": round((mu * ANN) / abs(dd.min()), 2) if dd.min() < 0 else float("inf"),
        "n_days": int(len(pl)),
        "daily_breach_-2pct": int((pl < -0.02).sum()),
    }


def block_bootstrap_ci(pl: pd.Series, iters: int = BOOT_ITERS,
                       block: int = BLOCK) -> tuple[float, float]:
    """CI95 on annualized Sharpe via circular block bootstrap."""
    x = pl.dropna().to_numpy()
    n = len(x)
    if n < 60:
        return float("nan"), float("nan")
    n_blocks = math.ceil(n / block)
    sharpes = np.empty(iters)
    for i in range(iters):
        starts = RNG.integers(0, n, n_blocks)
        idx = (starts[:, None] + np.arange(block)[None, :]).ravel() % n
        s = x[idx[:n]]
        sd = s.std()
        sharpes[i] = s.mean() / sd * math.sqrt(ANN) if sd > 0 else 0.0
    lo, hi = np.percentile(sharpes, [2.5, 97.5])
    return round(float(lo), 2), round(float(hi), 2)


def strategy_returns(cfg: StrategyConfig, rates=None,
                     lookback_override: int | None = None
                     ) -> tuple[pd.Series, pd.Series, dict]:
    """Net daily returns of a config, exactly as prod computes and pays them.

    Returns (net, gross, info). Leverage = vol-target on the equal-weight
    signed stream (shifted, like generate_delta_orders), recomputed daily.
    """
    closes, sigs = {}, {}
    for ins in cfg.instruments:
        c = load_daily_close(ins.csv)
        lb = lookback_override or ins.lookback
        if cfg.signal == "carry":
            s = carry_signal_series(c, ins.symbol, rates, lb)
        else:
            s = _apply_filters(_signal_series(c, lb, cfg.signal), c, cfg)
        closes[ins.symbol], sigs[ins.symbol] = c, s
    rets = pd.DataFrame({k: c.pct_change() for k, c in closes.items()})
    sig = pd.DataFrame(sigs).reindex(rets.index)
    pos = sig.shift(1)                                   # traded next day
    gross = (pos * rets).mean(axis=1)

    # rolling vol-target leverage, shifted (no look-ahead), like prod
    realized = gross.rolling(VOL_LOOKBACK).std() * math.sqrt(ANN)
    lev = (cfg.target_vol / realized).clip(upper=cfg.max_leverage).shift(1)
    lev = lev.replace([np.inf, -np.inf], np.nan).fillna(0.0)

    n = len(cfg.instruments)
    cost_bps = pd.Series({s: COST_BPS_PER_DAY.get(s, 2.0) + GSL_PREMIUM_BPS
                          for s in pos.columns})
    # current executor: --reset pays the spread on every day a position is open
    daily_cost = (pos.abs() * cost_bps / 1e4).sum(axis=1) / n * lev
    net = gross * lev - daily_cost

    # skip-reset variant: pay the spread only on the days a position CHANGES
    # (signal flip or entry/exit) — half a spread out, half back in, i.e. one
    # full spread per position change, plus leverage-drift rebalances ignored.
    per_ins_changed = pos.fillna(0.0).diff().abs() > 0
    change_cost = ((per_ins_changed * cost_bps / 1e4).sum(axis=1) / n * lev)
    net_skip = gross * lev - change_cost

    active = pos.abs().sum(axis=1) > 0
    changed = per_ins_changed.any(axis=1) & active
    info = {
        "avg_leverage": round(float(lev[active].mean() or 0), 3),
        "pct_days_active": round(float(active.mean()) * 100, 1),
        "signal_change_days": int(changed.sum()),
        "reset_only_days": int((active & ~changed).sum()),
        "ann_cost_drag_pct": round(float(daily_cost.mean()) * ANN * 100, 2),
        "ann_cost_drag_skip_reset_pct": round(float(change_cost.mean()) * ANN * 100, 2),
    }
    return net.dropna(), net_skip.dropna(), info


def lookback_plateau(cfg: StrategyConfig, rates=None) -> dict:
    """OOS Sharpe across neighboring lookbacks — edge must be a plateau, not a spike."""
    base = cfg.instruments[0].lookback
    grid = sorted({max(10, int(base * f)) for f in (0.5, 0.75, 1.0, 1.25, 1.5)})
    out = {}
    for lb in grid:
        net, _, _ = strategy_returns(cfg, rates, lookback_override=lb)
        m = metrics(net[net.index >= OOS_START])
        out[lb] = m.get("sharpe", float("nan"))
    return out


def verdict(full: dict, oos: dict, ci_lo: float, plateau: dict, base_lb: int) -> str:
    neighbors = [v for k, v in plateau.items() if not math.isnan(v)]
    plateau_ok = neighbors and sum(v > 0 for v in neighbors) / len(neighbors) >= 0.6
    oos_sh = oos.get("sharpe", float("nan"))
    checks = [ci_lo > 0, plateau_ok, full.get("max_dd_pct", -99) > -8.0]
    if not math.isnan(oos_sh) and oos_sh >= 0.5 and all(checks):
        return "ROBUST"
    if not math.isnan(oos_sh) and oos_sh > 0 and sum(checks) >= 2:
        return "OBSERVE"
    return "DEAD"


def main() -> None:
    rates = load_rates()
    results: dict[str, dict] = {}
    streams: dict[str, pd.Series] = {}

    streams_skip: dict[str, pd.Series] = {}
    for cfgfile in sorted(CONFIG_DIR.glob("*.json")):
        cfg = StrategyConfig.from_json(cfgfile)
        try:
            net, net_skip, info = strategy_returns(
                cfg, rates if cfg.signal == "carry" else None)
        except FileNotFoundError as exc:
            results[cfg.id] = {"error": f"data missing: {exc}"}
            continue
        full = metrics(net)
        oos = metrics(net[net.index >= OOS_START])
        full_skip = metrics(net_skip)
        oos_skip = metrics(net_skip[net_skip.index >= OOS_START])
        ci_lo, ci_hi = block_bootstrap_ci(net)
        oos_ci_lo, oos_ci_hi = block_bootstrap_ci(net[net.index >= OOS_START])
        skip_ci_lo, skip_ci_hi = block_bootstrap_ci(net_skip)
        plateau = lookback_plateau(cfg, rates if cfg.signal == "carry" else None)
        results[cfg.id] = {
            "signal": cfg.signal,
            "target_vol": cfg.target_vol,
            "lookback": cfg.instruments[0].lookback,
            "full": full,
            "oos_2024plus": oos,
            "full_skip_reset": full_skip,
            "oos_skip_reset": oos_skip,
            "ci95_sharpe_full": [ci_lo, ci_hi],
            "ci95_sharpe_oos": [oos_ci_lo, oos_ci_hi],
            "oos_sharpe_by_lookback": plateau,
            "ci95_sharpe_full_skip": [skip_ci_lo, skip_ci_hi],
            "execution": info,
            "verdict": verdict(full, oos, ci_lo, plateau,
                               cfg.instruments[0].lookback),
            "verdict_skip_reset": verdict(full_skip, oos_skip, skip_ci_lo,
                                          plateau, cfg.instruments[0].lookback),
        }
        streams[cfg.id] = net
        streams_skip[cfg.id] = net_skip
        print(f"{cfg.id:8} {results[cfg.id]['verdict']:8} "
              f"full Sh={full.get('sharpe', float('nan')):+.2f} "
              f"OOS Sh={oos.get('sharpe', float('nan')):+.2f} "
              f"| skip-reset full={full_skip.get('sharpe', float('nan')):+.2f} "
              f"OOS={oos_skip.get('sharpe', float('nan')):+.2f} "
              f"| CIfull=[{ci_lo:+.2f},{ci_hi:+.2f}] "
              f"DD={full.get('max_dd_pct', 0):+.1f}% "
              f"drag={info['ann_cost_drag_pct']:.2f}->"
              f"{info['ann_cost_drag_skip_reset_pct']:.2f}%/yr")

    # ---- program-level portfolios (one strategy per account, equal capital) ----
    combos = {
        "gold_only": ["gold"],
        "gold+bonds": ["gold", "bonds"],
        "gold+oil": ["gold", "oil"],
        "gold+index": ["gold", "index"],
        "gold+bonds+oil": ["gold", "bonds", "oil"],
        "gold+bonds+index": ["gold", "bonds", "index"],
        "gold+bonds+oil+index": ["gold", "bonds", "oil", "index"],
        "all_seven": list(streams.keys()),
    }
    port: dict[str, dict] = {}
    for name, ids in combos.items():
        for tag, pool in (("reset", streams), ("skip", streams_skip)):
            members = [pool[i] for i in ids if i in pool]
            if len(members) != len(ids):
                continue
            eq = pd.concat(members, axis=1, sort=True).dropna(how="all") \
                   .fillna(0.0).mean(axis=1)
            pf, po = metrics(eq), metrics(eq[eq.index >= OOS_START])
            lo, hi = block_bootstrap_ci(eq)
            port[f"{name}[{tag}]"] = {"full": pf, "oos": po, "ci95_full": [lo, hi]}
            print(f"PORT {name:22}[{tag:5}] full Sh={pf.get('sharpe', 0):+.2f} "
                  f"OOS Sh={po.get('sharpe', 0):+.2f} DD={pf.get('max_dd_pct', 0):+.1f}%")

    # correlation matrix of strategy streams
    corr = pd.concat(streams, axis=1, sort=True).corr().round(2).to_dict()

    out = {"strategies": results, "portfolios": port, "correlations": corr,
           "oos_start": str(OOS_START.date()),
           "cost_model_bps_per_reset_day": COST_BPS_PER_DAY,
           "gsl_premium_bps": GSL_PREMIUM_BPS}
    (HERE / "validate_strategy_configs_metrics.json").write_text(
        json.dumps(out, indent=2, default=str))

    lines = ["# Production config validation — pre-registered verdicts\n",
             f"OOS split: {OOS_START.date()}. Costs: 1 full spread/position/day "
             f"(daily --reset) + {GSL_PREMIUM_BPS}bp GSL premium.\n",
             "| strategy | signal | verdict reset→skip | full Sharpe | OOS Sharpe | OOS CI95 | skip-reset full/OOS | maxDD | worst day | drag reset→skip |",
             "|---|---|---|---:|---:|---|---|---:|---:|---|"]
    for sid, r in results.items():
        if "error" in r:
            lines.append(f"| {sid} | - | NO DATA | | | | | | | |")
            continue
        lines.append(
            f"| {sid} | {r['signal']} | **{r['verdict']} → {r['verdict_skip_reset']}** "
            f"| {r['full'].get('sharpe')} | {r['oos_2024plus'].get('sharpe')} "
            f"| [{r['ci95_sharpe_oos'][0]}, {r['ci95_sharpe_oos'][1]}] "
            f"| {r['full_skip_reset'].get('sharpe')} / {r['oos_skip_reset'].get('sharpe')} "
            f"| {r['full'].get('max_dd_pct')}% | {r['full'].get('worst_day_pct')}% "
            f"| {r['execution']['ann_cost_drag_pct']}% → "
            f"{r['execution']['ann_cost_drag_skip_reset_pct']}% |")
    lines.append("\n## Portfolios (equal capital per account)\n")
    lines.append("| combo | full Sharpe | OOS Sharpe | full maxDD | CI95 full |")
    lines.append("|---|---:|---:|---:|---|")
    for name, p in port.items():
        lines.append(f"| {name} | {p['full'].get('sharpe')} | {p['oos'].get('sharpe')} "
                     f"| {p['full'].get('max_dd_pct')}% | {p['ci95_full']} |")
    (HERE / "validate_strategy_configs_report.md").write_text("\n".join(lines) + "\n")
    print("\nWrote validate_strategy_configs_report.md + _metrics.json")


if __name__ == "__main__":
    main()
