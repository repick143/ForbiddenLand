"""easy-tdx MAC collector and data-quality audit for order-flow research."""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .aggregate import resolve_transaction_alignment, session_bar_mask
from .config import OrderFlowConfig
from .normalize import normalize_bar_frame, normalize_transaction_frame, parse_symbol, parse_ymd


@dataclass(frozen=True, slots=True)
class TransactionPageAudit:
    """One paginated MAC transaction request."""

    trade_date: str
    offset: int
    requested: int
    returned: int
    first_time: str | None
    last_time: str | None
    repeated_page: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "offset": self.offset,
            "requested": self.requested,
            "returned": self.returned,
            "first_time": self.first_time,
            "last_time": self.last_time,
            "repeated_page": self.repeated_page,
        }


@dataclass(frozen=True, slots=True)
class TransactionFetch:
    """Normalized transactions plus pagination metadata for one trading day."""

    frame: pd.DataFrame
    pages: tuple[TransactionPageAudit, ...]
    requested_max_rows: int
    truncated: bool
    unknown_flags: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class EasyTdxOrderFlowSnapshot:
    """All source frames and provenance needed to reproduce one order-flow run."""

    symbol: str
    bars: pd.DataFrame
    transactions: pd.DataFrame
    daily_bars: pd.DataFrame
    validation_minute_bars: pd.DataFrame
    quote: pd.DataFrame
    auction: pd.DataFrame
    provenance: dict[str, Any]
    quality: dict[str, Any]
    page_audits: tuple[TransactionPageAudit, ...]


def _ymd(value: date | datetime | int | str) -> str:
    return parse_ymd(value).strftime("%Y%m%d")


def _date_filter(frame: pd.DataFrame, start: date | None, end: date | None) -> pd.DataFrame:
    if frame.empty or (start is None and end is None):
        return frame
    timestamps = pd.to_datetime(frame["timestamp"], errors="coerce")
    mask = pd.Series(True, index=frame.index)
    if start is not None:
        mask &= timestamps.dt.date >= start
    if end is not None:
        mask &= timestamps.dt.date <= end
    return frame.loc[mask].copy()


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return str(value)


def _frame_from_client_result(result: Any) -> pd.DataFrame:
    if isinstance(result, pd.DataFrame):
        return result.copy()
    if result is None:
        return pd.DataFrame()
    if isinstance(result, list):
        rows: list[dict[str, Any]] = []
        for item in result:
            if hasattr(item, "__dataclass_fields__"):
                from dataclasses import asdict

                rows.append(asdict(item))
            elif isinstance(item, dict):
                rows.append(dict(item))
            else:
                rows.append(dict(vars(item)))
        return pd.DataFrame(rows)
    raise TypeError(f"easy-tdx response must be a DataFrame or list, got {type(result)!r}")


class EasyTdxCollector:
    """Collect one symbol through a single explicit MAC client.

    ``MacClient.from_best_host`` is intentionally not called once per request: its config-file
    update can race in parallel jobs.  The default path uses the cached best MAC host, while a
    caller may pass a host selected by one serialized ping step.
    """

    def __init__(
        self,
        *,
        host: str | None = None,
        timeout: float = 20.0,
        client: Any | None = None,
        client_factory: Callable[[str, float], Any] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.host = host
        self.timeout = float(timeout)
        self.client = client
        self.client_factory = client_factory
        self.clock = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def _easy_tdx_types() -> tuple[Any, Any, Any]:
        try:
            from easy_tdx import Adjust, Market, Period
        except ImportError as exc:  # pragma: no cover - depends on installation profile.
            raise RuntimeError(
                "easy-tdx is required for live order-flow collection; install "
                'with `python -m pip install "easy-tdx==1.28.1"`'
            ) from exc
        return Market, Period, Adjust

    def _resolved_host(self) -> str:
        if self.host:
            return self.host
        try:
            from easy_tdx.config import get_best_mac_host

            return str(get_best_mac_host())
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("easy-tdx is required to resolve a MAC host") from exc

    @contextmanager
    def _open_client(self) -> Iterator[Any]:
        if self.client is not None:
            yield self.client
            return
        host = self._resolved_host()
        if self.client_factory is not None:
            created = self.client_factory(host, self.timeout)
        else:
            try:
                from easy_tdx import MacClient
            except ImportError as exc:  # pragma: no cover
                raise RuntimeError(
                    "easy-tdx is required for live order-flow collection; install easy-tdx==1.28.1"
                ) from exc
            created = MacClient(host=host, timeout=self.timeout)
        if hasattr(created, "__enter__"):
            with created as active:
                yield active
        else:
            yield created
            close = getattr(created, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _market_code(exchange: str, market_type: Any) -> Any:
        return getattr(market_type, exchange)

    def fetch_transactions_for_date(
        self,
        client: Any,
        market: Any,
        code: str,
        trade_date: date | datetime | int | str,
        *,
        config: OrderFlowConfig,
        symbol: str | None = None,
        max_rows: int = 20_000,
        page_size: int = 1_000,
    ) -> TransactionFetch:
        """Fetch all available pages for one date and audit offsets/repetitions."""

        if max_rows <= 0:
            raise ValueError("max_rows must be positive")
        if not 1 <= page_size <= 1_000:
            raise ValueError("page_size must be between 1 and 1000")
        day = parse_ymd(trade_date)
        day_text = _ymd(trade_date)
        day_int = int(day_text)
        raw_pages: list[pd.DataFrame] = []
        audits: list[TransactionPageAudit] = []
        seen_page_keys: set[tuple[str, ...]] = set()
        unknown_flags: set[int] = set()
        offset = 0
        truncated = False
        while offset < max_rows:
            requested = min(page_size, max_rows - offset)
            raw = _frame_from_client_result(
                client.get_transactions(
                    market,
                    code,
                    count=requested,
                    start=offset,
                    date=day_int,
                )
            )
            returned = len(raw)
            if "bs_flag" in raw.columns and not raw.empty:
                flags = pd.to_numeric(raw["bs_flag"], errors="coerce").dropna().astype(int)
                unknown_flags.update(
                    int(value) for value in flags.unique() if int(value) not in {0, 1, 2, 5}
                )
            first_time = _as_text(raw.iloc[0]["time"]) if returned and "time" in raw else None
            last_time = _as_text(raw.iloc[-1]["time"]) if returned and "time" in raw else None
            if returned:
                key_columns = [
                    column
                    for column in ("time", "price", "vol", "trade_count", "bs_flag")
                    if column in raw
                ]
                key = tuple(
                    str(raw.iloc[position][column])
                    for position in (0, -1)
                    for column in key_columns
                )
            else:
                key = ()
            repeated = bool(key) and key in seen_page_keys
            audits.append(
                TransactionPageAudit(
                    trade_date=day_text,
                    offset=offset,
                    requested=requested,
                    returned=returned,
                    first_time=first_time,
                    last_time=last_time,
                    repeated_page=repeated,
                )
            )
            if repeated:
                truncated = True
                break
            if not returned:
                break
            seen_page_keys.add(key)
            raw_pages.append(raw)
            offset += returned
            if returned < requested:
                break
        else:
            truncated = True

        raw_all = pd.concat(raw_pages, ignore_index=True) if raw_pages else pd.DataFrame()
        normalized = normalize_transaction_frame(
            raw_all,
            trade_date=day,
            symbol=symbol
            or (f"{getattr(market, 'name', '')}:{code}" if hasattr(market, "name") else code),
            transaction_lot_size=config.transaction_lot_size,
            include_auction=config.include_auction,
            include_after_hours=config.include_after_hours,
            unknown_direction_policy=config.unknown_direction_policy,
        )
        return TransactionFetch(
            frame=normalized,
            pages=tuple(audits),
            requested_max_rows=max_rows,
            truncated=truncated,
            unknown_flags=tuple(sorted(unknown_flags)),
        )

    def collect(
        self,
        symbol: str,
        *,
        config: OrderFlowConfig | None = None,
        start_date: date | datetime | int | str | None = None,
        end_date: date | datetime | int | str | None = None,
        bar_count: int = 30_000,
        daily_count: int = 2_000,
        transaction_days: int | None = 120,
        transaction_max_rows: int = 20_000,
        transaction_page_size: int = 1_000,
        validation_days: int = 3,
        warmup_sessions: int | None = None,
        fetch_quote: bool = True,
        fetch_auction: bool = True,
    ) -> EasyTdxOrderFlowSnapshot:
        """Collect bars, transactions, and cross-check data from easy-tdx.

        ``transaction_days`` limits network work to the latest N available trading dates after the
        requested date filter.  ``None`` or ``0`` means all dates returned by the K-line endpoint;
        it is a performance control, not a strategy lookback.  The default 120 sessions gives the
        same-time baseline enough history while remaining practical for repeated research runs.
        """

        settings = config or OrderFlowConfig()
        exchange, code, qualified = parse_symbol(symbol)
        start = parse_ymd(start_date) if start_date is not None else None
        end = parse_ymd(end_date) if end_date is not None else None
        if start is not None and end is not None and start > end:
            raise ValueError("start_date must not be later than end_date")
        if bar_count <= 0 or daily_count <= 0:
            raise ValueError("bar_count and daily_count must be positive")
        if transaction_days is not None and transaction_days < 0:
            raise ValueError("transaction_days must be non-negative or None")
        if not 1 <= transaction_page_size <= 1_000:
            raise ValueError("transaction_page_size must be between 1 and 1000")
        if transaction_max_rows <= 0:
            raise ValueError("transaction_max_rows must be positive")
        if validation_days < 0:
            raise ValueError("validation_days must be non-negative")
        if warmup_sessions is not None and warmup_sessions < 0:
            raise ValueError("warmup_sessions must be non-negative or None")

        retrieved_at = self.clock()
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=UTC)
        market_type, period_type, adjust_type = self._easy_tdx_types()
        market = self._market_code(exchange, market_type)
        host = self._resolved_host() if self.client is None else (self.host or "injected-client")

        with self._open_client() as client:
            raw_bars = _frame_from_client_result(
                client.get_stock_kline(
                    market,
                    code,
                    period=getattr(period_type, f"MIN_{settings.bar_minutes}"),
                    count=bar_count,
                    adjust=adjust_type.NONE,
                    bar_time="start",
                )
            )
            bars_all = normalize_bar_frame(
                raw_bars, symbol=qualified, bar_minutes=settings.bar_minutes
            )
            raw_timestamp_column = next(
                (column for column in ("datetime", "timestamp", "date") if column in raw_bars),
                None,
            )
            raw_bar_timestamps = (
                pd.to_datetime(raw_bars[raw_timestamp_column], errors="coerce")
                if raw_timestamp_column is not None
                else pd.Series(dtype="datetime64[ns]")
            )
            transaction_alignment = resolve_transaction_alignment(
                bars_all,
                bar_minutes=settings.bar_minutes,
                alignment=settings.transaction_alignment,
            )
            bars_all = bars_all.loc[
                session_bar_mask(bars_all, alignment=transaction_alignment)
            ].copy()
            # The endpoint detector may retain the 11:30 right-endpoint bar, which the generic
            # timestamp classifier intentionally treats as outside a point-in-time session.
            bars_all["is_session_bar"] = True
            if transaction_alignment == "ceil":
                morning_boundary = bars_all["timestamp"].dt.hour.eq(11) & bars_all[
                    "timestamp"
                ].dt.minute.eq(30)
                bars_all.loc[morning_boundary, "session"] = "continuous"
                bars_all.loc[morning_boundary, "is_session_last"] = True
            if bars_all.empty:
                raise ValueError(f"easy-tdx returned no continuous-session bars for {qualified}")

            latest_raw_bar = raw_bar_timestamps.max()
            market_today = retrieved_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
            market_now = retrieved_at.astimezone(ZoneInfo("Asia/Shanghai"))
            incomplete_current_day = bool(
                pd.notna(latest_raw_bar)
                and latest_raw_bar.date() == market_today
                and market_now.time() < time(15, 0)
            )

            available_dates = sorted(
                pd.to_datetime(bars_all["timestamp"]).dt.date.drop_duplicates().tolist()
            )
            requested_dates = [
                current
                for current in available_dates
                if (start is None or current >= start) and (end is None or current <= end)
            ]
            if not requested_dates:
                raise ValueError(
                    f"easy-tdx returned no bars in the requested date window for {qualified}"
                )
            if transaction_days and transaction_days > 0:
                analysis_dates = requested_dates[-transaction_days:]
            else:
                analysis_dates = requested_dates
            use_warmup = start is not None or end is not None
            effective_warmup = (
                max(settings.volume_baseline_sessions, settings.min_history_sessions)
                if warmup_sessions is None
                else warmup_sessions
            )
            warmup_dates = (
                [current for current in available_dates if current < analysis_dates[0]][
                    -effective_warmup:
                ]
                if use_warmup and effective_warmup > 0
                else []
            )
            transaction_dates = warmup_dates + analysis_dates
            bars = bars_all.loc[bars_all["timestamp"].dt.date.isin(transaction_dates)].copy()
            bars["is_warmup"] = bars["timestamp"].dt.date.isin(warmup_dates)
            bars["is_incomplete_session"] = incomplete_current_day & bars["timestamp"].dt.date.eq(
                market_today
            )

            raw_daily = _frame_from_client_result(
                client.get_stock_kline(
                    market,
                    code,
                    period=period_type.DAILY,
                    count=daily_count,
                    adjust=adjust_type.NONE,
                    bar_time="start",
                )
            )
            daily_bars = normalize_bar_frame(raw_daily, symbol=qualified, bar_minutes=1)

            tx_frames: list[pd.DataFrame] = []
            all_audits: list[TransactionPageAudit] = []
            all_unknown: set[int] = set()
            truncated_dates: list[str] = []
            for day in transaction_dates:
                fetched = self.fetch_transactions_for_date(
                    client,
                    market,
                    code,
                    day,
                    config=settings,
                    symbol=qualified,
                    max_rows=transaction_max_rows,
                    page_size=transaction_page_size,
                )
                if not fetched.frame.empty:
                    tx_frames.append(fetched.frame)
                all_audits.extend(fetched.pages)
                all_unknown.update(fetched.unknown_flags)
                if fetched.truncated:
                    truncated_dates.append(_ymd(day))
            transactions = (
                pd.concat(tx_frames, ignore_index=True)
                if tx_frames
                else normalize_transaction_frame(
                    pd.DataFrame(),
                    trade_date=transaction_dates[0]
                    if transaction_dates
                    else self.clock().astimezone(UTC).date(),
                    symbol=qualified,
                    transaction_lot_size=settings.transaction_lot_size,
                    include_auction=settings.include_auction,
                    include_after_hours=settings.include_after_hours,
                    unknown_direction_policy=settings.unknown_direction_policy,
                )
            )

            validation_minute_bars = pd.DataFrame()
            if validation_days > 0:
                minute_count = max(500, validation_days * 242 + 30)
                try:
                    raw_minute = _frame_from_client_result(
                        client.get_stock_kline(
                            market,
                            code,
                            period=period_type.MIN_1,
                            count=minute_count,
                            adjust=adjust_type.NONE,
                            bar_time="start",
                        )
                    )
                    validation_minute_bars = normalize_bar_frame(
                        raw_minute, symbol=qualified, bar_minutes=1
                    )
                    validation_mask = session_bar_mask(
                        validation_minute_bars,
                        alignment=transaction_alignment,
                    )
                    validation_minute_bars = validation_minute_bars.loc[
                        validation_minute_bars["timestamp"].dt.date.isin(
                            transaction_dates[-validation_days:]
                        )
                        & validation_mask
                    ].copy()
                    validation_minute_bars["is_session_bar"] = True
                    if transaction_alignment == "ceil":
                        morning_boundary = validation_minute_bars["timestamp"].dt.hour.eq(11) & (
                            validation_minute_bars["timestamp"].dt.minute.eq(30)
                        )
                        validation_minute_bars.loc[morning_boundary, "session"] = "continuous"
                        validation_minute_bars.loc[morning_boundary, "is_session_last"] = True
                except (OSError, TimeoutError, ValueError) as exc:
                    # Cross-check failure should remain visible while the validated target bars and
                    # transactions are retained.
                    validation_minute_bars = pd.DataFrame()
                    validation_error = f"{type(exc).__name__}: {exc}"
                else:
                    validation_error = None
            else:
                validation_error = None

            quote = pd.DataFrame()
            if fetch_quote:
                quote = _frame_from_client_result(client.get_stock_quotes([(market, code)]))
            auction = pd.DataFrame()
            if fetch_auction:
                auction = _frame_from_client_result(client.get_auction(market, code))

        quality = self._build_quality(
            bars=bars,
            daily_bars=daily_bars,
            transactions=transactions,
            validation_minute_bars=validation_minute_bars,
            quote=quote,
            auction=auction,
            transaction_dates=transaction_dates,
            page_audits=tuple(all_audits),
            unknown_flags=tuple(sorted(all_unknown)),
            truncated_dates=truncated_dates,
            validation_error=validation_error,
            transaction_lot_size=settings.transaction_lot_size,
            transaction_page_size=transaction_page_size,
            transaction_max_rows=transaction_max_rows,
            validation_requested=validation_days > 0,
            incomplete_current_day=incomplete_current_day,
        )
        finished_at = self.clock()
        if finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=UTC)
        provenance = {
            "source": "easy_tdx",
            "protocol": "MAC",
            "package_version": self._package_version(),
            "host": host,
            "retrieved_at_utc": retrieved_at.isoformat(),
            "completed_at_utc": finished_at.isoformat(),
            "market": exchange,
            "symbol": qualified,
            "period": f"{settings.bar_minutes}MIN",
            "adjustment": "NONE",
            "bar_time": "start",
            "transaction_alignment_requested": settings.transaction_alignment,
            "transaction_alignment": transaction_alignment,
            "bar_label_semantics": (
                "right_endpoint" if transaction_alignment == "ceil" else "left_endpoint"
            ),
            "incomplete_current_day": incomplete_current_day,
            "latest_raw_bar": latest_raw_bar.isoformat() if pd.notna(latest_raw_bar) else None,
            "kline_volume_unit": "shares",
            "transaction_volume_unit": "protocol_lots",
            "transaction_lot_size": settings.transaction_lot_size,
            "transaction_page_size": transaction_page_size,
            "transaction_max_rows": transaction_max_rows,
            "transaction_dates": [_ymd(day) for day in transaction_dates],
            "analysis_dates": [_ymd(day) for day in analysis_dates],
            "warmup_dates": [_ymd(day) for day in warmup_dates],
            "warmup_sessions": effective_warmup if use_warmup else 0,
            "date_filter": {
                "start_date": _ymd(start) if start is not None else None,
                "end_date": _ymd(end) if end is not None else None,
            },
        }
        return EasyTdxOrderFlowSnapshot(
            symbol=qualified,
            bars=bars.reset_index(drop=True),
            transactions=transactions.sort_values("timestamp", kind="mergesort").reset_index(
                drop=True
            ),
            daily_bars=daily_bars.reset_index(drop=True),
            validation_minute_bars=validation_minute_bars.reset_index(drop=True),
            quote=quote,
            auction=auction,
            provenance=provenance,
            quality=quality,
            page_audits=tuple(all_audits),
        )

    @staticmethod
    def _package_version() -> str:
        try:
            import easy_tdx

            return str(getattr(easy_tdx, "__version__", "unknown"))
        except ImportError:  # pragma: no cover
            return "unavailable"

    @staticmethod
    def _build_quality(
        *,
        bars: pd.DataFrame,
        daily_bars: pd.DataFrame,
        transactions: pd.DataFrame,
        validation_minute_bars: pd.DataFrame,
        quote: pd.DataFrame,
        auction: pd.DataFrame,
        transaction_dates: list[date],
        page_audits: tuple[TransactionPageAudit, ...],
        unknown_flags: tuple[int, ...],
        truncated_dates: list[str],
        validation_error: str | None,
        transaction_lot_size: int,
        transaction_page_size: int,
        transaction_max_rows: int,
        validation_requested: bool,
        incomplete_current_day: bool,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        daily_lookup = {
            pd.Timestamp(row["timestamp"]).date(): float(row["volume"])
            for _, row in daily_bars.iterrows()
            if pd.notna(row.get("volume"))
        }
        by_date: list[dict[str, Any]] = []
        for day in transaction_dates:
            tx_day = transactions.loc[transactions["trade_date"].dt.date.eq(day)]
            all_shares = float(tx_day["volume_shares"].sum()) if not tx_day.empty else 0.0
            continuous_shares = (
                float(tx_day.loc[tx_day["session"].eq("continuous"), "volume_shares"].sum())
                if not tx_day.empty
                else 0.0
            )
            bar_day = bars.loc[bars["timestamp"].dt.date.eq(day)]
            bar_shares = float(bar_day["volume"].sum()) if not bar_day.empty else 0.0
            daily_shares = daily_lookup.get(day)
            ratio = all_shares / daily_shares if daily_shares and daily_shares > 0 else None
            continuous_ratio = (
                continuous_shares / daily_shares if daily_shares and daily_shares > 0 else None
            )
            continuous_to_target = continuous_shares / bar_shares if bar_shares > 0 else None
            if ratio is not None and abs(ratio - 1.0) > 0.05:
                warnings.append(
                    f"{_ymd(day)} all transaction volume differs from daily K-line by >5%"
                )
            if continuous_to_target is not None and abs(continuous_to_target - 1.0) > 0.05:
                warnings.append(
                    f"{_ymd(day)} continuous transaction volume differs from target bars by >5%"
                )
            by_date.append(
                {
                    "date": _ymd(day),
                    "transaction_rows": len(tx_day),
                    "transaction_shares_all_sessions": all_shares,
                    "transaction_shares_continuous": continuous_shares,
                    "target_bar_shares": bar_shares,
                    "daily_kline_shares": daily_shares,
                    "all_to_daily_ratio": ratio,
                    "continuous_to_daily_ratio": continuous_ratio,
                    "continuous_to_target_bar_ratio": continuous_to_target,
                    "excluded_session_shares": all_shares - continuous_shares,
                }
            )
        if unknown_flags:
            warnings.append(f"unknown bs_flag values observed: {list(unknown_flags)}")
        if truncated_dates:
            warnings.append(
                "transaction pagination reached max_rows on: " + ", ".join(truncated_dates)
            )
        repeated_pages = [audit for audit in page_audits if audit.repeated_page]
        if repeated_pages:
            warnings.append("repeated transaction page detected; affected dates are incomplete")
        if validation_error:
            warnings.append("1-minute cross-check failed: " + validation_error)
        if validation_requested and validation_minute_bars.empty:
            warnings.append("no 1-minute validation bars remained after date filtering")
        if incomplete_current_day:
            warnings.append(
                "latest market date is still in progress; current-day bars and transactions are provisional"
            )
        if bars["volume"].eq(0).any():
            warnings.append(
                "target K-line contains zero-volume bars; they are not treated as zero transactions"
            )

        tx_flags = (
            transactions["bs_flag"].value_counts().sort_index().astype(int).to_dict()
            if not transactions.empty
            else {}
        )
        minute_crosscheck: list[dict[str, Any]] = []
        if {"timestamp", "is_session_bar", "volume"}.issubset(validation_minute_bars.columns):
            for day in transaction_dates[-3:]:
                minute_day = validation_minute_bars.loc[
                    validation_minute_bars["timestamp"].dt.date.eq(day)
                    & validation_minute_bars["is_session_bar"].astype(bool)
                ]
                bar_day = bars.loc[bars["timestamp"].dt.date.eq(day)]
                if minute_day.empty or bar_day.empty:
                    continue
                minute_volume = float(minute_day["volume"].sum())
                target_volume = float(bar_day["volume"].sum())
                minute_crosscheck.append(
                    {
                        "date": _ymd(day),
                        "minute_bar_rows": len(minute_day),
                        "minute_shares": minute_volume,
                        "target_bar_shares": target_volume,
                        "minute_to_target_ratio": minute_volume / target_volume
                        if target_volume > 0
                        else None,
                    }
                )
        quote_quality: dict[str, Any] = {"rows": len(quote)}
        if not quote.empty:
            for field in ("close", "pre_close", "vol", "amount", "lot_size"):
                if field in quote.columns:
                    value = quote.iloc[0][field]
                    quote_quality[field] = (
                        float(value) if pd.notna(value) and np.isscalar(value) else None
                    )
            if "close" in quote.columns and float(quote.iloc[0]["close"]) <= 0:
                warnings.append("quote close is non-positive")
        auction_quality = {
            "rows": len(auction),
            "columns": list(auction.columns),
        }
        return {
            "warnings": list(dict.fromkeys(warnings)),
            "bars": {
                "rows": len(bars),
                "dates": int(bars["timestamp"].dt.date.nunique()) if not bars.empty else 0,
                "zero_volume_rows": int(bars["volume"].eq(0).sum()) if not bars.empty else 0,
            },
            "transactions": {
                "rows": len(transactions),
                "dates_with_rows": int(transactions["trade_date"].dt.date.nunique())
                if not transactions.empty
                else 0,
                "bs_flag_counts": {str(key): value for key, value in tx_flags.items()},
                "page_count": len(page_audits),
                "repeated_page_count": len(repeated_pages),
                "truncated_dates": list(truncated_dates),
                "page_audits": [audit.as_dict() for audit in page_audits],
                "unknown_flags": list(unknown_flags),
                "transaction_lot_size": transaction_lot_size,
                "transaction_page_size": transaction_page_size,
                "transaction_max_rows": transaction_max_rows,
                "by_date": by_date,
            },
            "minute_crosscheck": minute_crosscheck,
            "incomplete_current_day": incomplete_current_day,
            "quote": quote_quality,
            "auction": auction_quality,
        }


__all__ = [
    "EasyTdxCollector",
    "EasyTdxOrderFlowSnapshot",
    "TransactionFetch",
    "TransactionPageAudit",
]
