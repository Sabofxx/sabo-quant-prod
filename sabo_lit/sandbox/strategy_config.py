"""
Explicit per-strategy config schema for the parameterized runner.

One JSON file per strategy under configs/. Each config is fully self-contained:
which instruments + lookbacks, which signal, sizing, the Capital.com account it is
allowed to trade, and an isolated live/ subdirectory. Nothing is shared between
strategies — see strategy_runner.py.

Account credential resolution (handles both account models the user is testing):
  - secret_suffix == ""   -> use base CAPITAL_API_KEY / CAPITAL_IDENTIFIER / ...
                             (Cas A: one login, switch accountId per config)
  - secret_suffix == "_X" -> use CAPITAL_API_KEY_X / CAPITAL_IDENTIFIER_X / ...
                             (Cas B: separate login per strategy)
In BOTH cases account_id is asserted against the live session before any order.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).parent
LIVE_ROOT = HERE / "live"
CONFIG_DIR = HERE / "configs"


@dataclass(frozen=True)
class Instrument:
    symbol: str          # internal symbol used in delta CSV, e.g. "EURUSD", "XAUUSD"
    csv: str             # data file under data/ (m5 bid, resampled to daily)
    lookback: int        # momentum lookback in days


@dataclass(frozen=True)
class StrategyConfig:
    id: str                          # short id, e.g. "fxtsm", "gold"
    description: str
    signal: str                      # "tsm" (momentum) | "ma" (MA filter) | "donchian" (breakout)
    instruments: list[Instrument]
    target_vol: float                # ANNUALIZED vol target (e.g. 0.05 = 5%)
    account_id: str                  # Capital.com accountId this strategy may trade ("" = unset)
    env: str = "demo"                # demo | live
    secret_suffix: str = ""          # "" -> base CAPITAL_* ; "_FXTSM" -> CAPITAL_*_FXTSM
    max_leverage: float = 10.0
    min_notional_usd: float = 500.0
    live_subdir: str = ""            # defaults to id if empty
    long_only: bool = False          # True -> shorts become flat (e.g. index long-bias)
    vol_filter_pct: float = 0.0      # 0 = off; else flat when realized-vol rank > this (e.g. 0.80
                                     #   skips the top-20% most volatile regimes — crash protection)
    intraday: bool = False           # True -> intraday strategy (flat at EOD; see strategy_runner)

    # --- pre-registered elimination criteria (carried for reporting/dashboards) ---
    min_trades: int = 30
    min_live_sharpe: float = 0.50
    min_backtest_track: float = 0.50  # live Sharpe must be >= this * backtest Sharpe
    red_flag_track: float = 0.40      # below this * backtest -> eliminate even if PnL>0
    max_median_slippage_pips: float = 1.5
    min_observation_days: int = 60

    @property
    def live_dir(self) -> Path:
        return LIVE_ROOT / (self.live_subdir or self.id)

    def secret(self, name: str) -> str | None:
        """Resolve a CAPITAL_* secret with this strategy's suffix (Cas B) or base (Cas A)."""
        return os.environ.get(f"{name}{self.secret_suffix}") or os.environ.get(name)

    @staticmethod
    def from_json(path: Path) -> "StrategyConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        instruments = [Instrument(**i) for i in raw.pop("instruments")]
        cfg = StrategyConfig(instruments=instruments, **raw)
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.signal not in {"tsm", "ma", "donchian", "carry"}:
            raise ValueError(f"{self.id}: signal must be tsm|ma|donchian|carry, got {self.signal!r}")
        if not self.instruments:
            raise ValueError(f"{self.id}: no instruments")
        if self.target_vol <= 0 or self.target_vol > 0.5:
            raise ValueError(f"{self.id}: target_vol {self.target_vol} out of sane range")
        if self.env not in {"demo", "live"}:
            raise ValueError(f"{self.id}: env must be demo|live")
        for ins in self.instruments:
            if not (HERE / "data" / ins.csv).exists():
                raise FileNotFoundError(f"{self.id}: data file missing for {ins.symbol}: data/{ins.csv}")


def load_config(name_or_path: str) -> StrategyConfig:
    """Accept 'fxtsm', 'configs/fxtsm.json', or an absolute path."""
    p = Path(name_or_path)
    if not p.exists():
        p = CONFIG_DIR / f"{name_or_path}.json"
    return StrategyConfig.from_json(p)
