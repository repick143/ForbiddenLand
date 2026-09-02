"""Normalization helpers for easy-tdx bars and transaction prints.

The MAC transaction endpoint returns a compact, aggregated print record.  This module keeps the
raw protocol fields, adds an explicit share conversion, and classifies auction/continuous/after-
hours records before any aggregation.  No missing transaction row is converted to a zero print.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any, Literal

import numpy as np
import pandas as pd

SessionLabel = Literal["auction", "continuous", "after_hours", "out_of_session"]

_TRANSACTION_ALIASES: dict[str, tuple[str, ...]] = {
    "time": ("time", "成交时间", "时间"),
    "price": ("price", "成交价", "价格"),
    "raw_volume": ("raw_volume", "vol", "volume", "成交量", "数量"),
    "trade_count": ("trade_count", "成交笔数", "笔数"),
    "bs_flag": ("bs_flag", "方向", "买卖标志"),
}
_BAR_ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp": ("timestamp", "datetime", "date", "日期", "时间"),
    "open": ("open", "开盘", "开盘价"),
    "high": ("high", "最高", "最高价"),
    "low": ("low", "最低", "最低价"),
    "close": ("close", "收盘", "收盘价"),
    "volume": ("volume", "vol", "成交量"),
    "amount": ("amount", "成交额", "金额"),
    "symbol": ("symbol", "code", "股票代码", "代码", "ticker"),
}

BS_FLAG_DIRECTION: dict[int, int] = {0: 1, 1: -1, 2: 0, 5: 0}
BS_FLAG_LABEL: dict[int, str] = {0: "buy", 1: "sell", 2: "neutral", 5: "after_hours"}


def parse_symbol(value: str) -> tuple[str, str, str]:
    """Return ``(exchange, six_digit_code, exchange:code)`` for an A-share symbol."""

    if not isinstance(value, str):
        raise TypeError("symbol must be a string")
    raw = value.strip().upper().replace("：", ":")
    raw = raw.replace(" ", ":")
    if ":" in raw:
        exchange, code = raw.split(":", 1)
    else:
        exchange, code = "", raw
    code = code.strip()
    if not code.isdigit() or len(code) > 6 or not code:
        raise ValueError(f"invalid A-share symbol: {value!r}")
    code = code.zfill(6)
    if exchange:
        if exchange not in {"SH", "SZ", "BJ"}:
            raise ValueError(f"unsupported A-share exchange: {exchange!r}")
    elif code.startswith(("60", "68", "688")):
        exchange = "SH"
    elif code.startswith(("00", "30", "20")):
        exchange = "SZ"
    elif code.startswith(("4", "8")):
        exchange = "BJ"
    else:
        raise ValueError(f"cannot infer exchange for symbol {code!r}; use EXCHANGE:CODE")
    return exchange, code, f"{exchange}:{code}"


def parse_ymd(value: date | datetime | int | str) -> date:
    """Parse a date-like value without silently accepting an ambiguous locale format."""

    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        try:
            return date.fromisoformat(f"{text[:4]}-{text[4:6]}-{text[6:]}")
        except ValueError as exc:
            raise ValueError(f"invalid YYYYMMDD date: {value!r}") from exc
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        raise ValueError(f"invalid date: {value!r}")
    return parsed.date()


def _find_column(frame: pd.DataFrame, aliases: tuple[str, ...]) -> str | None:
    lower = {str(column).casefold(): column for column in frame.columns}
    for alias in aliases:
        if alias in frame.columns:
            return str(alias)
        found = lower.get(alias.casefold())
        if found is not None:
            return str(found)
    return None


def _parse_time(value: Any) -> time | None:
    """Parse MAC ``time`` values (time objects, HH:MM[:SS], or numeric HHMMSS)."""

    if value is None or (not isinstance(value, (datetime, time)) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value.time().replace(microsecond=0)
    if isinstance(value, time):
        return value.replace(microsecond=0)
    if isinstance(value, (int, np.integer)) or (
        isinstance(value, float) and np.isfinite(value) and float(value).is_integer()
    ):
        number = int(value)
        if number < 0:
            return None
        text = str(number)
        # MAC's wire value is seconds after midnight; CLI DataFrames expose HH:MM:SS, but accepting
        # both representations makes the normalizer useful with captured protocol fixtures.
        if number < 86_400:
            hours, remainder = divmod(number, 3_600)
            minutes, seconds = divmod(remainder, 60)
            if hours < 24:
                return time(hours, minutes, seconds)
        text = text.zfill(6)
        if len(text) == 6:
            hours, minutes, seconds = int(text[:2]), int(text[2:4]), int(text[4:])
            if hours < 24 and minutes < 60 and seconds < 60:
                return time(hours, minutes, seconds)
        return None
    text = str(value).strip()
    if not text:
        return None
    if "T" in text or " " in text:
        parsed = pd.to_datetime(text, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.time().replace(microsecond=0)
    parts = text.split(":")
    if len(parts) in {2, 3} and all(part.isdigit() for part in parts):
        numbers = [int(part) for part in parts]
        if len(numbers) == 2:
            numbers.append(0)
        hours, minutes, seconds = numbers
        if hours < 24 and minutes < 60 and seconds < 60:
            return time(hours, minutes, seconds)
    return _parse_time(int(text)) if text.isdigit() else None


def classify_session(value: time | pd.Timestamp | datetime) -> SessionLabel:
    """Classify a print/bar timestamp using the Shanghai A-share session boundaries."""

    if isinstance(value, (pd.Timestamp, datetime)):
        current = value.time()
    else:
        current = value
    seconds = current.hour * 3_600 + current.minute * 60 + current.second
    if 9 * 3_600 + 15 * 60 <= seconds < 9 * 3_600 + 30 * 60:
        return "auction"
    if (9 * 3_600 + 30 * 60 <= seconds < 11 * 3_600 + 30 * 60) or (
        13 * 3_600 <= seconds < 15 * 3_600
    ):
        return "continuous"
    if seconds >= 15 * 3_600:
        return "after_hours"
    return "out_of_session"


def normalize_transaction_frame(
    frame: pd.DataFrame,
    *,
    trade_date: date | datetime | int | str,
    symbol: str,
    transaction_lot_size: int = 100,
    include_auction: bool = False,
    include_after_hours: bool = False,
    unknown_direction_policy: str = "neutral",
) -> pd.DataFrame:
    """Normalize one easy-tdx transaction response.

    ``raw_volume`` is retained in protocol units and ``volume_shares`` is the explicit converted
    value.  The default conversion is 100 shares per unit, verified against the K-line volume by
    the collector; callers can override it only when they have an independent unit audit.
    """

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("transaction frame must be a pandas DataFrame")
    if isinstance(transaction_lot_size, bool) or not isinstance(transaction_lot_size, int):
        raise TypeError("transaction_lot_size must be a positive integer")
    if transaction_lot_size <= 0:
        raise ValueError("transaction_lot_size must be positive")
    if unknown_direction_policy not in {"neutral", "drop", "error"}:
        raise ValueError("unknown_direction_policy must be neutral, drop, or error")
    exchange, _, qualified = parse_symbol(symbol)
    day = parse_ymd(trade_date)
    output_columns = [
        "transaction_id",
        "symbol",
        "exchange",
        "trade_date",
        "timestamp",
        "time",
        "price",
        "raw_volume",
        "volume_shares",
        "trade_count",
        "bs_flag",
        "direction",
        "direction_label",
        "session",
        "included",
        "amount",
    ]
    if frame.empty:
        return pd.DataFrame(columns=output_columns)

    resolved: dict[str, str] = {}
    for field, aliases in _TRANSACTION_ALIASES.items():
        column = _find_column(frame, aliases)
        if column is None:
            raise ValueError(f"transaction response is missing column: {field}")
        resolved[field] = column

    data = pd.DataFrame(index=frame.index)
    data["symbol"] = qualified
    data["exchange"] = exchange
    data["trade_date"] = pd.Timestamp(day)
    times = frame[resolved["time"]].map(_parse_time)
    if times.isna().any():
        bad = frame.index[times.isna()].tolist()[:3]
        raise ValueError(f"transaction response contains invalid time values at rows {bad}")
    data["time"] = times
    data["timestamp"] = times.map(lambda current: pd.Timestamp.combine(day, current))
    for field in ("price", "raw_volume", "trade_count", "bs_flag"):
        data[field] = pd.to_numeric(frame[resolved[field]], errors="coerce")
    numeric = data[["price", "raw_volume", "trade_count", "bs_flag"]]
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise ValueError("transaction response contains missing/non-numeric fields")
    if not data["bs_flag"].mod(1).eq(0).all():
        raise ValueError("transaction response contains non-integer bs_flag values")
    data["bs_flag"] = data["bs_flag"].astype(int)
    if (
        (data["price"] <= 0).any()
        or (data["raw_volume"] < 0).any()
        or (data["trade_count"] < 0).any()
    ):
        raise ValueError("transaction response contains non-positive price or negative counts")

    unknown = ~data["bs_flag"].isin(BS_FLAG_DIRECTION)
    if unknown.any() and unknown_direction_policy == "error":
        values = sorted(data.loc[unknown, "bs_flag"].unique().tolist())
        raise ValueError(f"transaction response contains unknown bs_flag values: {values}")
    if unknown.any() and unknown_direction_policy == "drop":
        data = data.loc[~unknown].copy()
        unknown = pd.Series(False, index=data.index)

    data["direction"] = data["bs_flag"].map(BS_FLAG_DIRECTION).fillna(0).astype(int)
    data["direction_label"] = data["bs_flag"].map(BS_FLAG_LABEL).fillna("unknown")
    data["session"] = data["time"].map(classify_session)
    data["included"] = data["session"].eq("continuous")
    if include_auction:
        data.loc[data["session"].eq("auction"), "included"] = True
    if include_after_hours:
        data.loc[data["session"].eq("after_hours"), "included"] = True
    # Flag 5 is explicitly post-session in the MAC protocol, even if a server reports a borderline
    # timestamp.  It is retained for reconciliation but excluded by default from trading features.
    if not include_after_hours:
        data.loc[data["bs_flag"].eq(5), "included"] = False
    data["volume_shares"] = data["raw_volume"] * float(transaction_lot_size)
    data["amount"] = data["price"] * data["volume_shares"]
    data = data.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    data["transaction_id"] = np.arange(len(data), dtype=np.int64)
    # Keep a stable schema even when an unknown-direction row was dropped.
    return data[output_columns]


def normalize_bar_frame(
    frame: pd.DataFrame,
    *,
    symbol: str,
    bar_minutes: int = 5,
) -> pd.DataFrame:
    """Normalize an easy-tdx K-line response and add session alignment keys."""

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("bar frame must be a pandas DataFrame")
    exchange, code, qualified = parse_symbol(symbol)
    if (
        isinstance(bar_minutes, bool)
        or not isinstance(bar_minutes, int)
        or bar_minutes not in {1, 5, 15, 30, 60}
    ):
        raise ValueError("bar_minutes must be one of 1, 5, 15, 30, or 60")
    required = {field: _find_column(frame, aliases) for field, aliases in _BAR_ALIASES.items()}
    missing = [
        field
        for field in ("timestamp", "open", "high", "low", "close", "volume")
        if not required[field]
    ]
    if missing:
        raise ValueError("bar response is missing columns: " + ", ".join(missing))
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "symbol",
                "exchange",
                "code",
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "session",
                "session_date_key",
                "slot_key",
                "bar_key",
                "is_session_bar",
                "is_session_last",
            ]
        )
    data = pd.DataFrame(index=frame.index)
    data["timestamp"] = pd.to_datetime(frame[required["timestamp"]], errors="coerce")
    if data["timestamp"].isna().any():
        raise ValueError("bar response contains invalid timestamps")
    for field in ("open", "high", "low", "close", "volume"):
        data[field] = pd.to_numeric(frame[required[field]], errors="coerce")
    amount_column = required["amount"]
    data["amount"] = (
        pd.to_numeric(frame[amount_column], errors="coerce")
        if amount_column is not None
        else np.nan
    )
    numeric = data[["open", "high", "low", "close", "volume"]]
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise ValueError("bar response contains missing/non-finite OHLCV values")
    if (data[["open", "high", "low", "close"]] <= 0).any().any() or (data["volume"] < 0).any():
        raise ValueError("bar response contains non-positive prices or negative volume")
    if (
        (data["high"] < data[["open", "close"]].max(axis=1)).any()
        or (data["low"] > data[["open", "close"]].min(axis=1)).any()
        or (data["high"] < data["low"]).any()
    ):
        raise ValueError("bar response contains invalid OHLC ordering")
    data["symbol"] = qualified
    data["exchange"] = exchange
    data["code"] = code
    data = data.sort_values("timestamp", kind="mergesort").reset_index(drop=True)
    if data.duplicated(["symbol", "timestamp"]).any():
        raise ValueError("bar response contains duplicate symbol/timestamp rows")
    data["session"] = data["timestamp"].map(classify_session)
    data["session_date_key"] = data["timestamp"].dt.strftime("%Y%m%d").astype(int)
    data["slot_key"] = data["timestamp"].dt.hour * 60 + data["timestamp"].dt.minute
    data["bar_key"] = data["timestamp"].dt.strftime("%Y%m%d%H%M").astype(np.int64)
    data["is_session_bar"] = data["session"].eq("continuous")
    end_minutes = data["timestamp"].dt.hour * 60 + data["timestamp"].dt.minute + bar_minutes
    data["is_session_last"] = data["is_session_bar"] & end_minutes.ge(15 * 60)
    return data


__all__ = [
    "BS_FLAG_DIRECTION",
    "BS_FLAG_LABEL",
    "classify_session",
    "normalize_bar_frame",
    "normalize_transaction_frame",
    "parse_symbol",
    "parse_ymd",
]
