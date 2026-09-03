"""Align normalized transaction prints with easy-tdx K-lines."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import TransactionAlignment
from .normalize import normalize_bar_frame, parse_symbol

_TRANSACTION_FEATURE_COLUMNS = (
    "buy_volume",
    "sell_volume",
    "neutral_volume",
    "total_transaction_volume",
    "buy_amount",
    "sell_amount",
    "neutral_amount",
    "transaction_amount",
    "trade_count",
    "transaction_rows",
    "buy_trade_count",
    "sell_trade_count",
    "neutral_trade_count",
    "trade_volume_squared",
    "max_trade_volume",
    "large_trade_volume",
    "large_buy_volume",
    "large_sell_volume",
    "large_neutral_volume",
    "large_trade_rows",
    "vwap",
)


def _validate_bar_minutes(bar_minutes: int) -> None:
    if (
        isinstance(bar_minutes, bool)
        or not isinstance(bar_minutes, int)
        or bar_minutes not in {1, 5, 15, 30, 60}
    ):
        raise ValueError("bar_minutes must be one of 1, 5, 15, 30, or 60")


def resolve_transaction_alignment(
    bars: pd.DataFrame,
    *,
    bar_minutes: int = 5,
    alignment: TransactionAlignment = "auto",
) -> str:
    """Resolve the transaction-to-bar timestamp convention.

    ``easy-tdx`` documents ``bar_time="start"`` as a left-endpoint label, but the MAC servers
    currently used by this project return minute bars whose first morning/afternoon labels are the
    right endpoints (for example ``09:35`` and ``13:05`` for a five-minute request).  ``auto``
    detects that convention from the first bar in each session.  If a partial response has no
    session boundary to inspect, it conservatively falls back to ``floor``; callers with a known
    convention can always select ``floor`` or ``ceil`` explicitly.
    """

    _validate_bar_minutes(bar_minutes)
    if not isinstance(alignment, str) or alignment not in {"auto", "floor", "ceil"}:
        raise ValueError("transaction_alignment must be auto, floor, or ceil")
    if alignment != "auto":
        return alignment
    if not isinstance(bars, pd.DataFrame) or "timestamp" not in bars.columns:
        raise TypeError("bars must be a DataFrame with a timestamp column")
    timestamps = pd.to_datetime(bars["timestamp"], errors="coerce")
    timestamps = timestamps.dropna()
    if timestamps.empty:
        return "floor"

    minutes = timestamps.dt.hour * 60 + timestamps.dt.minute
    floor_score = 0
    ceil_score = 0
    floor_terminal_score = 0
    ceil_terminal_score = 0
    # Looking at the first observed bar for each session avoids treating a shared grid point (for
    # example 10:00 in a 30-minute series) as evidence for both conventions.
    for session_start, session_end in ((9 * 60 + 30, 11 * 60 + 30), (13 * 60, 15 * 60)):
        session = timestamps[(minutes >= session_start) & (minutes <= session_end)]
        if session.empty:
            continue
        first_by_day = session.groupby(session.dt.date, sort=False).min()
        first_minutes = first_by_day.dt.hour * 60 + first_by_day.dt.minute
        floor_score += int(first_minutes.eq(session_start).sum())
        ceil_score += int(first_minutes.eq(session_start + bar_minutes).sum())
        last_by_day = session.groupby(session.dt.date, sort=False).max()
        last_minutes = last_by_day.dt.hour * 60 + last_by_day.dt.minute
        floor_terminal_score += int(last_minutes.eq(session_end - bar_minutes).sum())
        ceil_terminal_score += int(last_minutes.eq(session_end).sum())
    if ceil_score > floor_score:
        return "ceil"
    if floor_score > ceil_score:
        return "floor"

    # A response can begin after the first session bar.  Exact session-boundary labels still give
    # useful evidence in that case, while an otherwise ambiguous grid uses the fixture-safe floor
    # fallback documented above.
    floor_boundary = minutes.isin({9 * 60 + 30, 13 * 60}).any()
    ceil_boundary = minutes.isin({9 * 60 + 30 + bar_minutes, 13 * 60 + bar_minutes}).any()
    # Terminal labels are useful when the response starts in the middle of a session: a right
    # endpoint ends at 11:30/15:00, whereas a left endpoint ends one interval earlier.
    if (ceil_boundary and not floor_boundary) or (
        ceil_terminal_score > floor_terminal_score and ceil_terminal_score > 0
    ):
        return "ceil"
    if floor_boundary or floor_terminal_score > ceil_terminal_score:
        return "floor"
    return "floor"


def session_bar_mask(
    frame: pd.DataFrame,
    *,
    alignment: str,
) -> pd.Series:
    """Return target bars for the selected left/right endpoint convention."""

    if alignment not in {"floor", "ceil"}:
        raise ValueError("resolved transaction alignment must be floor or ceil")
    timestamps = pd.to_datetime(frame["timestamp"], errors="coerce")
    minutes = timestamps.dt.hour * 60 + timestamps.dt.minute
    if alignment == "ceil":
        # Right-endpoint bars cover (09:30, 11:30] and (13:00, 15:00).  The 15:00 record is the
        # closing auction/after-hours print and remains outside the continuous-session strategy.
        return ((minutes > 9 * 60 + 30) & (minutes <= 11 * 60 + 30)) | (
            (minutes > 13 * 60) & (minutes < 15 * 60)
        )
    if "is_session_bar" in frame.columns:
        return frame["is_session_bar"].fillna(False).astype(bool)
    return ((minutes >= 9 * 60 + 30) & (minutes < 11 * 60 + 30)) | (
        (minutes >= 13 * 60) & (minutes < 15 * 60)
    )


def _transaction_bar_timestamp(
    timestamps: pd.Series,
    *,
    bar_minutes: int,
    alignment: str,
) -> pd.Series:
    frequency = f"{bar_minutes}min"
    floored = timestamps.dt.floor(frequency)
    if alignment == "floor":
        return floored
    # Right-endpoint bars use half-open source intervals.  A print exactly on a boundary starts
    # the next interval, so plain pandas ``ceil`` needs the explicit exact-boundary adjustment.
    exact_boundary = timestamps.eq(floored)
    ceiled = timestamps.dt.ceil(frequency)
    interval = pd.to_timedelta(bar_minutes, unit="min")
    return ceiled.where(~exact_boundary, ceiled + interval)


def _empty_transaction_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for column in _TRANSACTION_FEATURE_COLUMNS:
        out[column] = np.nan
    out["transaction_observed"] = False
    out["transaction_coverage"] = np.nan
    out["volume_gap_shares"] = np.nan
    out["large_trade_share"] = np.nan
    return out


def aggregate_transactions_to_bars(
    bars: pd.DataFrame,
    transactions: pd.DataFrame,
    *,
    symbol: str,
    bar_minutes: int = 5,
    large_trade_lots: int = 100,
    transaction_alignment: TransactionAlignment = "auto",
) -> pd.DataFrame:
    """Join transaction-direction aggregates to canonical K-lines.

    The K-line remains the source of OHLCV.  Transaction fields are left missing when no print
    was observed for a bar; this is materially different from a true zero-volume bar and lets the
    strategy skip incomplete coverage rather than manufacture neutral flow.
    """

    if not isinstance(bars, pd.DataFrame) or not isinstance(transactions, pd.DataFrame):
        raise TypeError("bars and transactions must be pandas DataFrames")
    _validate_bar_minutes(bar_minutes)
    if isinstance(large_trade_lots, bool) or not isinstance(large_trade_lots, int):
        raise TypeError("large_trade_lots must be a non-negative integer")
    if large_trade_lots < 0:
        raise ValueError("large_trade_lots must be non-negative")
    _, _, qualified = parse_symbol(symbol)

    canonical = bars.copy()
    if "timestamp" not in canonical.columns or "volume" not in canonical.columns:
        canonical = normalize_bar_frame(canonical, symbol=qualified, bar_minutes=bar_minutes)
    else:
        canonical["timestamp"] = pd.to_datetime(canonical["timestamp"], errors="coerce")
        if canonical["timestamp"].isna().any():
            raise ValueError("bars contain invalid timestamps")
    resolved_alignment = resolve_transaction_alignment(
        canonical,
        bar_minutes=bar_minutes,
        alignment=transaction_alignment,
    )
    session_mask = session_bar_mask(
        canonical,
        alignment=resolved_alignment,
    )
    session_mask = session_mask.fillna(False).astype(bool)
    canonical["is_session_bar"] = session_mask
    if "session" not in canonical.columns:
        canonical["session"] = np.where(session_mask, "continuous", "out_of_session")
    elif resolved_alignment == "ceil":
        canonical.loc[session_mask, "session"] = "continuous"
    minutes = canonical["timestamp"].dt.hour * 60 + canonical["timestamp"].dt.minute
    end_minutes = minutes + bar_minutes
    if resolved_alignment == "ceil":
        canonical["is_session_last"] = session_mask & (
            minutes.eq(11 * 60 + 30) | end_minutes.ge(15 * 60)
        )
    elif "is_session_last" not in canonical.columns:
        canonical["is_session_last"] = session_mask & end_minutes.ge(15 * 60)
    canonical = canonical.loc[session_mask].copy()
    if canonical.empty:
        result = _empty_transaction_columns(canonical)
        result["delta"] = np.nan
        result["delta_ratio"] = np.nan
        result["transaction_share_of_bar"] = np.nan
        if "session_date_key" not in result.columns:
            result["session_date_key"] = pd.Series(dtype="int64")
        if "slot_key" not in result.columns:
            result["slot_key"] = pd.Series(dtype="int64")
        result["transaction_alignment"] = resolved_alignment
        result.attrs["transaction_alignment"] = resolved_alignment
        return result
    canonical["symbol"] = canonical.get("symbol", qualified)
    canonical["symbol"] = canonical["symbol"].astype(str)

    if transactions.empty:
        result = _empty_transaction_columns(canonical)
    else:
        required = {"timestamp", "volume_shares", "price", "raw_volume", "trade_count", "direction"}
        missing = sorted(required.difference(transactions.columns))
        if missing:
            raise ValueError("normalized transactions are missing columns: " + ", ".join(missing))
        tx = transactions.copy()
        if "symbol" not in tx.columns:
            tx["symbol"] = qualified
        if "amount" not in tx.columns:
            tx["amount"] = tx["price"] * tx["volume_shares"]
        tx["timestamp"] = pd.to_datetime(tx["timestamp"], errors="coerce")
        if tx["timestamp"].isna().any():
            raise ValueError("transactions contain invalid timestamps")
        if "included" in tx.columns:
            tx = tx.loc[tx["included"].astype(bool)].copy()
        tx = tx.loc[tx["symbol"].astype(str).eq(qualified)].copy()
        if tx.empty:
            result = _empty_transaction_columns(canonical)
        else:
            tx["bar_timestamp"] = _transaction_bar_timestamp(
                tx["timestamp"],
                bar_minutes=bar_minutes,
                alignment=resolved_alignment,
            )
            tx["buy_volume"] = tx["volume_shares"].where(tx["direction"].eq(1), 0.0)
            tx["sell_volume"] = tx["volume_shares"].where(tx["direction"].eq(-1), 0.0)
            tx["neutral_volume"] = tx["volume_shares"].where(tx["direction"].eq(0), 0.0)
            tx["buy_amount"] = tx["amount"].where(tx["direction"].eq(1), 0.0)
            tx["sell_amount"] = tx["amount"].where(tx["direction"].eq(-1), 0.0)
            tx["neutral_amount"] = tx["amount"].where(tx["direction"].eq(0), 0.0)
            tx["buy_trade_count"] = tx["trade_count"].where(tx["direction"].eq(1), 0.0)
            tx["sell_trade_count"] = tx["trade_count"].where(tx["direction"].eq(-1), 0.0)
            tx["neutral_trade_count"] = tx["trade_count"].where(tx["direction"].eq(0), 0.0)
            tx["trade_volume_squared"] = tx["volume_shares"].pow(2)
            tx["large_trade_volume"] = tx["volume_shares"].where(
                tx["raw_volume"].ge(large_trade_lots), 0.0
            )
            large_trade = tx["raw_volume"].ge(large_trade_lots)
            tx["large_buy_volume"] = tx["volume_shares"].where(
                large_trade & tx["direction"].eq(1), 0.0
            )
            tx["large_sell_volume"] = tx["volume_shares"].where(
                large_trade & tx["direction"].eq(-1), 0.0
            )
            tx["large_neutral_volume"] = tx["volume_shares"].where(
                large_trade & tx["direction"].eq(0), 0.0
            )
            tx["large_trade_rows"] = large_trade.astype(float)
            grouped = (
                tx.groupby("bar_timestamp", sort=True)
                .agg(
                    buy_volume=("buy_volume", "sum"),
                    sell_volume=("sell_volume", "sum"),
                    neutral_volume=("neutral_volume", "sum"),
                    total_transaction_volume=("volume_shares", "sum"),
                    buy_amount=("buy_amount", "sum"),
                    sell_amount=("sell_amount", "sum"),
                    neutral_amount=("neutral_amount", "sum"),
                    transaction_amount=("amount", "sum"),
                    trade_count=("trade_count", "sum"),
                    transaction_rows=("volume_shares", "size"),
                    buy_trade_count=("buy_trade_count", "sum"),
                    sell_trade_count=("sell_trade_count", "sum"),
                    neutral_trade_count=("neutral_trade_count", "sum"),
                    trade_volume_squared=("trade_volume_squared", "sum"),
                    max_trade_volume=("volume_shares", "max"),
                    large_trade_volume=("large_trade_volume", "sum"),
                    large_buy_volume=("large_buy_volume", "sum"),
                    large_sell_volume=("large_sell_volume", "sum"),
                    large_neutral_volume=("large_neutral_volume", "sum"),
                    large_trade_rows=("large_trade_rows", "sum"),
                )
                .reset_index(names="timestamp")
            )
            grouped["vwap"] = grouped["transaction_amount"] / grouped[
                "total_transaction_volume"
            ].where(grouped["total_transaction_volume"] > 0)
            grouped["symbol"] = qualified
            result = canonical.merge(grouped, on=["symbol", "timestamp"], how="left", sort=False)
            observed = result["total_transaction_volume"].notna()
            result["transaction_observed"] = observed
            result["transaction_coverage"] = result["total_transaction_volume"] / result[
                "volume"
            ].where(result["volume"] > 0)
            result["volume_gap_shares"] = result["volume"] - result["total_transaction_volume"]
            result["large_trade_share"] = result["large_trade_volume"] / result[
                "total_transaction_volume"
            ].where(result["total_transaction_volume"] > 0)

    # Derived fields are kept here because they describe the quality of the join, not a trading
    # signal.  Missing transaction aggregates remain missing throughout.
    for column in (
        "buy_volume",
        "sell_volume",
        "neutral_volume",
        "buy_trade_count",
        "sell_trade_count",
        "neutral_trade_count",
        "trade_volume_squared",
        "max_trade_volume",
        "large_buy_volume",
        "large_sell_volume",
        "large_neutral_volume",
    ):
        if column not in result:
            result[column] = np.nan
    result["delta"] = result["buy_volume"] - result["sell_volume"]
    result["delta_ratio"] = result["delta"] / result["total_transaction_volume"].where(
        result["total_transaction_volume"] > 0
    )
    result["transaction_share_of_bar"] = result["total_transaction_volume"] / result[
        "volume"
    ].where(result["volume"] > 0)
    result["large_delta"] = result["large_buy_volume"] - result["large_sell_volume"]
    result["large_delta_ratio"] = result["large_delta"] / result["large_trade_volume"].where(
        result["large_trade_volume"] > 0
    )
    result["average_trade_size"] = result["total_transaction_volume"] / result["trade_count"].where(
        result["trade_count"] > 0
    )
    result["average_trade_amount"] = result["transaction_amount"] / result["trade_count"].where(
        result["trade_count"] > 0
    )
    result["trade_size_hhi"] = result["trade_volume_squared"] / result[
        "total_transaction_volume"
    ].pow(2).where(result["total_transaction_volume"] > 0)
    result["max_trade_share"] = result["max_trade_volume"] / result[
        "total_transaction_volume"
    ].where(result["total_transaction_volume"] > 0)
    result["buy_trade_share"] = result["buy_trade_count"] / result["trade_count"].where(
        result["trade_count"] > 0
    )
    result["sell_trade_share"] = result["sell_trade_count"] / result["trade_count"].where(
        result["trade_count"] > 0
    )
    result["transaction_alignment"] = resolved_alignment
    result.attrs["transaction_alignment"] = resolved_alignment
    result.attrs["transaction_alignment_requested"] = transaction_alignment
    if "session_date_key" not in result.columns:
        result["session_date_key"] = result["timestamp"].dt.strftime("%Y%m%d").astype(int)
    if "slot_key" not in result.columns:
        result["slot_key"] = result["timestamp"].dt.hour * 60 + result["timestamp"].dt.minute
    result = result.sort_values(["symbol", "timestamp"], kind="mergesort").reset_index(drop=True)
    grouped_symbol = result.groupby("symbol", sort=False, dropna=False)
    result["bar_index_in_symbol"] = grouped_symbol.cumcount().astype(int)
    result["next_session_date_key"] = grouped_symbol["session_date_key"].shift(-1)
    result["next_session_date_key"] = result["next_session_date_key"].fillna(
        result["session_date_key"]
    )
    result["next_session_date_key"] = result["next_session_date_key"].astype(int)
    return result


__all__ = [
    "aggregate_transactions_to_bars",
    "resolve_transaction_alignment",
    "session_bar_mask",
]
