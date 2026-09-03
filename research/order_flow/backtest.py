"""easy-tdx execution wrapper with an intraday-safe time and metric boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from .config import OrderFlowConfig
from .strategy import make_order_flow_strategy


@dataclass(frozen=True, slots=True)
class OrderFlowBacktestResult:
    """Backtest result plus raw engine metrics for auditability."""

    result: Any
    raw_engine_performance: dict[str, Any]
    execution_frame: pd.DataFrame
    corrected_performance: dict[str, Any]


def _numeric_time_key(value: Any) -> int:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError("backtest timestamp cannot be missing")
    return int(timestamp.strftime("%Y%m%d%H%M"))


def prepare_backtest_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Prepare numeric columns for easy-tdx's StrategyDataProxy.

    easy-tdx 1.30.3 normalizes a datetime-like column to ``YYYYMMDD`` internally.  That is correct
    for daily data but collapses every intraday bar of one day to one key.  We pass a unique numeric
    ``YYYYMMDDHHMM`` execution key and retain the real timestamp in the caller's feature frame.
    """

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("order-flow backtest frame must be a non-empty DataFrame")
    required = {"timestamp", "open", "high", "low", "close", "volume", "symbol"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("order-flow backtest frame is missing columns: " + ", ".join(missing))
    data = frame.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], errors="coerce")
    if data["timestamp"].isna().any():
        raise ValueError("order-flow backtest frame contains invalid timestamps")
    symbols = data["symbol"].astype("string").str.strip()
    if symbols.isna().any() or symbols.eq("").any() or symbols.nunique() != 1:
        raise ValueError("easy-tdx single-symbol backtest requires exactly one non-empty symbol")
    data = data.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    data["datetime"] = data["timestamp"].map(_numeric_time_key).astype(np.int64)
    if data["datetime"].duplicated().any():
        raise ValueError("backtest frame contains duplicate intraday execution keys")
    data["vol"] = pd.to_numeric(data["volume"], errors="coerce")
    if "amount" not in data.columns:
        data["amount"] = data["close"] * data["volume"]
    # StrategyDataProxy attempts to cast every non-datetime column to float.  Keep only numeric
    # feature columns at this boundary; human-readable labels remain in the report frame.
    numeric: dict[str, pd.Series] = {}
    for column in data.columns:
        if column in {"timestamp", "symbol", "exchange", "code"}:
            continue
        values = data[column]
        if pd.api.types.is_bool_dtype(values):
            numeric[column] = values.astype(float)
        elif pd.api.types.is_numeric_dtype(values):
            numeric[column] = pd.to_numeric(values, errors="coerce")
    execution = pd.DataFrame(numeric, index=data.index)
    for column in ("open", "high", "low", "close", "vol", "amount", "datetime"):
        if column not in execution.columns:
            raise ValueError(f"backtest frame is missing numeric column: {column}")
    if execution[["open", "high", "low", "close", "vol", "amount"]].isna().any().any():
        raise ValueError("backtest frame contains missing engine OHLCV values")
    if not np.isfinite(
        execution[["open", "high", "low", "close", "vol", "amount"]].to_numpy(dtype=float)
    ).all():
        raise ValueError("backtest frame contains non-finite engine OHLCV values")
    return execution.reset_index(drop=True)


def _trade_date(value: Any, key_to_timestamp: dict[int, pd.Timestamp]) -> date | None:
    try:
        key = int(value)
    except (TypeError, ValueError):
        return None
    timestamp = key_to_timestamp.get(key)
    return timestamp.date() if timestamp is not None else None


def _daily_equity(
    result: Any, execution_frame: pd.DataFrame, source_frame: pd.DataFrame
) -> pd.Series:
    equity = result.equity_curve
    if len(equity) != len(source_frame):
        raise ValueError("easy-tdx equity curve length does not match input bars")
    timestamps = pd.to_datetime(source_frame["timestamp"], errors="coerce")
    values = pd.to_numeric(equity["total"], errors="coerce")
    daily = (
        pd.DataFrame({"day": timestamps.dt.date, "total": values}).groupby("day")["total"].last()
    )
    return daily.astype(float)


def _corrected_performance(
    result: Any,
    execution_frame: pd.DataFrame,
    source_frame: pd.DataFrame,
    *,
    initial_cash: float,
) -> dict[str, Any]:
    """Compute daily-risk statistics; the bundled analyzer treats each intraday bar as a day."""

    daily = _daily_equity(result, execution_frame, source_frame)
    total = daily.to_numpy(dtype=float)
    if len(total) == 0:
        return {
            "start_cash": float(initial_cash),
            "end_value": float(initial_cash),
            "sessions": 0,
            "open_position_shares": 0.0,
            "closed_at_end": True,
        }
    # Compare with the declared starting cash, not the first end-of-day mark.  The latter can
    # already include a first-session trade and would understate/overstate the run return.
    total_return = float(total[-1] / initial_cash - 1.0) if initial_cash else 0.0
    # Include the declared starting cash as the pre-session equity point.  Without this point a
    # loss on the first trading day cannot contribute to drawdown or daily risk statistics.
    equity_path = np.concatenate(([float(initial_cash)], total))
    returns = (
        pd.Series(
            np.divide(
                np.diff(equity_path),
                equity_path[:-1],
                out=np.full(len(total), np.nan, dtype=float),
                where=equity_path[:-1] != 0,
            )
        )
        .replace([np.inf, -np.inf], np.nan)
        .dropna()
    )
    # Portfolio bookkeeping can leave machine-precision noise on otherwise flat days.  Treat it
    # as zero so a no-trade run does not report an infinite-looking Sortino ratio.
    returns = returns.mask(returns.abs() < 1e-12, 0.0)
    n_sessions = max(len(daily), 1)
    annual_return = (
        float((1.0 + total_return) ** (252.0 / n_sessions) - 1.0) if total_return > -1 else -1.0
    )
    peak = np.maximum.accumulate(equity_path)
    drawdowns = np.divide(peak - equity_path, peak, out=np.zeros_like(equity_path), where=peak != 0)
    max_drawdown = float(np.max(drawdowns)) if len(drawdowns) else 0.0
    max_dd_duration = 0
    current_duration = 0
    for value in drawdowns:
        if value > 0:
            current_duration += 1
            max_dd_duration = max(max_dd_duration, current_duration)
        else:
            current_duration = 0
    rf_daily = 0.03 / 252.0
    excess = returns.to_numpy(dtype=float) - rf_daily
    std = float(np.std(returns.to_numpy(dtype=float))) if len(returns) else 0.0
    sharpe = float(np.mean(excess) / std * np.sqrt(252.0)) if std > 0 else 0.0
    negative = excess[excess < 0]
    downside = float(np.std(negative)) if len(negative) else 0.0
    sortino = float(np.mean(excess) / downside * np.sqrt(252.0)) if downside > 0 else 0.0
    calmar = (
        annual_return / max_drawdown
        if max_drawdown > 1e-12
        else (999.0 if annual_return > 0 else 0.0)
    )

    trades = result.trades
    valid_trades = trades.loc[~trades["rejected"].astype(bool)] if not trades.empty else trades
    sells = (
        valid_trades.loc[valid_trades["direction"].eq("SELL")]
        if not valid_trades.empty
        else valid_trades
    )
    pnls = pd.to_numeric(sells.get("pnl", pd.Series(dtype=float)), errors="coerce").dropna()
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    gross_win = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0
    profit_factor = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    key_to_timestamp = {
        int(key): timestamp
        for key, timestamp in zip(
            execution_frame["datetime"], pd.to_datetime(source_frame["timestamp"])
        )
    }
    buy_dates: list[date] = []
    holding_days: list[float] = []
    for _, trade in valid_trades.iterrows():
        current = _trade_date(trade["datetime"], key_to_timestamp)
        if current is None:
            continue
        if trade["direction"] == "BUY":
            buy_dates.append(current)
        elif trade["direction"] == "SELL" and buy_dates:
            opened = buy_dates.pop(0)
            holding_days.append(float((current - opened).days))
    volatility = (
        float(np.std(returns.to_numpy(dtype=float)) * np.sqrt(252.0)) if len(returns) else 0.0
    )
    open_position = 0.0
    positions = getattr(result, "positions", None)
    if isinstance(positions, pd.DataFrame) and not positions.empty and "size" in positions.columns:
        final_size = pd.to_numeric(positions["size"], errors="coerce").iloc[-1]
        if pd.notna(final_size):
            open_position = float(final_size)
    return {
        "total_return": total_return,
        "annual_return": annual_return,
        "max_drawdown": max_drawdown,
        "max_dd_duration": int(max_dd_duration),
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": float(calmar),
        "total_trades": len(sells),
        "win_trades": len(wins),
        "lose_trades": len(losses),
        "rejected_trades": int(trades["rejected"].astype(bool).sum()) if not trades.empty else 0,
        "win_rate": float(len(wins) / len(sells)) if len(sells) else 0.0,
        "profit_factor": float(profit_factor),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "max_win": float(wins.max()) if len(wins) else 0.0,
        "max_loss": float(losses.min()) if len(losses) else 0.0,
        "avg_holding_days": float(np.mean(holding_days)) if holding_days else 0.0,
        "volatility": volatility,
        "start_cash": float(initial_cash),
        "end_value": float(total[-1]),
        "sessions": len(daily),
        "open_position_shares": open_position,
        "closed_at_end": bool(abs(open_position) < 1e-9),
    }


def run_order_flow_backtest(
    features: pd.DataFrame,
    *,
    config: OrderFlowConfig | None = None,
) -> OrderFlowBacktestResult:
    """Run the configured strategy through easy-tdx's independent pandas simulator."""

    settings = config or OrderFlowConfig()
    execution_frame = prepare_backtest_frame(features)
    try:
        from easy_tdx.backtest import BacktestEngine
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "easy-tdx is required for the backtest; install easy-tdx==1.30.3"
        ) from exc
    symbol = str(features["symbol"].iloc[0])
    strategy = make_order_flow_strategy(settings)
    engine = BacktestEngine(
        strategy,
        cash=settings.initial_cash,
        commission=settings.commission_rate,
        min_commission=settings.min_commission,
        stamp_tax=settings.stamp_tax_rate,
        slippage=settings.slippage_per_share,
        execution=settings.execution,
        position_mode=settings.position_mode,
        reject_policy=settings.reject_policy,
        warmup_bars=settings.warmup_bars,
        symbol=symbol,
        auto_fees=settings.auto_fees,
        signal_path=settings.signal_path,
    )
    result = engine.run(execution_frame)
    raw = dict(result.performance)
    corrected = _corrected_performance(
        result,
        execution_frame,
        features,
        initial_cash=settings.initial_cash,
    )
    result.performance = corrected
    return OrderFlowBacktestResult(
        result=result,
        raw_engine_performance=raw,
        execution_frame=execution_frame,
        corrected_performance=corrected,
    )


__all__ = [
    "OrderFlowBacktestResult",
    "prepare_backtest_frame",
    "run_order_flow_backtest",
]
