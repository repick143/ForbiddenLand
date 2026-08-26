"""AkShare-compatible access backed by either remote AkShare or local Parquet snapshots.

The public methods intentionally mirror the subset of AkShare used by this project. The local
backend is deliberately explicit about unsupported endpoints: a missing local dataset must not
silently turn a reproducible research run into a network request.
"""

from __future__ import annotations

import functools
import importlib
from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

from ..config import CompatibilityConfig, ConfigurationError


class CompatibilityError(RuntimeError):
    """Base class for compatibility-layer failures."""


class LocalDataError(CompatibilityError):
    """Raised when a local snapshot cannot satisfy a request."""


class LocalDataUnavailableError(LocalDataError):
    """Raised when a configured local file or required dependency is unavailable."""


class UnsupportedEndpointError(CompatibilityError):
    """Raised when the local backend has no equivalent dataset or implementation."""


class InvalidRequestError(ValueError):
    """Raised when an AkShare-compatible request has invalid arguments."""


HIST_COLUMNS = [
    "日期",
    "股票代码",
    "开盘",
    "收盘",
    "最高",
    "最低",
    "成交量",
    "成交额",
    "振幅",
    "涨跌幅",
    "涨跌额",
    "换手率",
]

LOCAL_ENDPOINTS = frozenset({"stock_zh_a_hist", "stock_info_a_code_name"})
_MISSING_ORIGINAL = object()

__all__ = [
    "HIST_COLUMNS",
    "AkShareCompat",
    "CompatibilityConfig",
    "CompatibilityError",
    "ConfigurationError",
    "InvalidRequestError",
    "LocalDataError",
    "LocalDataUnavailableError",
    "UnsupportedEndpointError",
    "ak",
    "get_akshare",
    "install_local_backend",
    "uninstall_backend",
]

_DAILY_SOURCE_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "pre_close",
    "change",
    "pct_chg",
    "vol",
    "amount",
    "turnover_rate",
    "adj_factor",
    "trade_date",
    "ts_code",
)
_REQUIRED_DAILY_COLUMNS = frozenset(
    column for column in _DAILY_SOURCE_COLUMNS if column != "adj_factor"
)
_REQUIRED_BASIC_COLUMNS = frozenset({"ts_code", "symbol", "name"})


def _load_data_dependencies() -> tuple[Any, Any]:
    """Load optional data dependencies only when the local backend is actually used."""

    try:
        duckdb = importlib.import_module("duckdb")
        pandas = importlib.import_module("pandas")
    except ImportError as exc:  # pragma: no cover - exercised in dependency-less installations
        raise LocalDataUnavailableError(
            "The local backend requires duckdb and pandas. "
            "Install the data profile with `python -m pip install -e '.[data]'`."
        ) from exc
    return duckdb, pandas


def _parse_symbol(symbol: str) -> tuple[str, str | None]:
    if not isinstance(symbol, str) or not symbol.strip():
        raise InvalidRequestError("symbol must be a non-empty stock-code string")
    value = symbol.strip().upper()
    market: str | None = None
    if "." in value:
        code, suffix = value.rsplit(".", 1)
        market = suffix
    elif value[:2] in {"SH", "SZ", "BJ"}:
        market, code = value[:2], value[2:]
    else:
        code = value
    if not code.isdigit() or len(code) > 6:
        raise InvalidRequestError(f"Unsupported stock code {symbol!r}")
    code = code.zfill(6)
    if market is not None and market not in {"SH", "SZ", "BJ"}:
        raise InvalidRequestError(f"Unsupported exchange suffix in symbol {symbol!r}")
    return code, market


def _parse_date(value: str | date | datetime, name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequestError(f"{name} must be a date string or date object")
    text = value.strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    raise InvalidRequestError(
        f"{name}={value!r} is invalid; use YYYYMMDD, YYYY-MM-DD, or YYYY/MM/DD"
    )


def _validate_hist_request(
    period: str,
    start_date: str | date | datetime,
    end_date: str | date | datetime,
    adjust: str,
) -> tuple[str, date, date, str]:
    if not isinstance(period, str) or period.lower() not in {"daily", "weekly", "monthly"}:
        raise InvalidRequestError("period must be one of daily, weekly, or monthly")
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")
    if start > end:
        raise InvalidRequestError("start_date must not be later than end_date")
    if not isinstance(adjust, str) or adjust.lower() not in {"", "qfq", "hfq"}:
        raise InvalidRequestError("adjust must be one of '', qfq, or hfq")
    return period.lower(), start, end, adjust.lower()


def _empty_hist_frame(pandas: Any) -> Any:
    frame = pandas.DataFrame(columns=HIST_COLUMNS)
    frame["股票代码"] = frame["股票代码"].astype("string")
    return frame


def _as_path(path: Path) -> Path:
    return path.expanduser()


class LocalBackend:
    """Read the project's local Parquet snapshots with DuckDB."""

    def __init__(self, config: CompatibilityConfig):
        self.config = config

    @property
    def daily_path(self) -> Path:
        return _as_path(self.config.resolved_daily_file())

    @property
    def basic_path(self) -> Path:
        return _as_path(self.config.resolved_basic_file())

    @staticmethod
    def _require_file(path: Path, label: str) -> None:
        if not path.is_file():
            raise LocalDataUnavailableError(
                f"Local {label} snapshot was not found at {path}. "
                "Set FORBIDDENLAND_DATA_ROOT or the corresponding file variable."
            )

    @staticmethod
    def _schema(path: Path, label: str, duckdb: Any) -> set[str]:
        try:
            connection = duckdb.connect(database=":memory:")
            try:
                rows = connection.execute(
                    "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]
                ).fetchall()
            finally:
                connection.close()
        except Exception as exc:
            raise LocalDataError(f"Unable to inspect local {label} snapshot {path}: {exc}") from exc
        return {str(row[0]) for row in rows}

    @staticmethod
    def _query(
        path: Path,
        sql: str,
        params: list[Any],
        label: str,
        duckdb: Any,
        pandas: Any,
    ) -> Any:
        connection = None
        try:
            connection = duckdb.connect(database=":memory:")
            result = connection.execute(sql, params)
            return result.fetchdf()
        except Exception as exc:
            # DuckDB's pandas conversion can require an optional bridge in older releases. Fall
            # back to rows so the local adapter remains usable with the minimal data profile.
            if "pandas" not in str(exc).lower() and "arrow" not in str(exc).lower():
                raise LocalDataError(
                    f"Unable to query local {label} snapshot {path}: {exc}"
                ) from exc
            try:
                if connection is None:
                    connection = duckdb.connect(database=":memory:")
                result = connection.execute(sql, params)
                rows = result.fetchall()
                columns = [item[0] for item in result.description]
                return pandas.DataFrame(rows, columns=columns)
            except Exception as fallback_exc:
                raise LocalDataError(
                    f"Unable to query local {label} snapshot {path}: {fallback_exc}"
                ) from fallback_exc
        finally:
            if connection is not None:
                connection.close()

    def _resolve_ts_code(self, symbol: str, duckdb: Any, pandas: Any) -> tuple[str, str]:
        code, market = _parse_symbol(symbol)
        if market is None:
            # Prefer the security master because it handles unusual and legacy code prefixes.
            if self.basic_path.is_file():
                columns = self._schema(self.basic_path, "stock basic", duckdb)
                if _REQUIRED_BASIC_COLUMNS.issubset(columns):
                    frame = self._query(
                        self.basic_path,
                        """
                        SELECT ts_code, symbol
                        FROM read_parquet(?)
                        WHERE CAST(symbol AS VARCHAR) = ?
                        LIMIT 2
                        """,
                        [str(self.basic_path), code],
                        "stock basic",
                        duckdb,
                        pandas,
                    )
                    if not frame.empty:
                        candidates = [str(item) for item in frame["ts_code"].dropna().tolist()]
                        if len(candidates) == 1:
                            return code, candidates[0].upper()
            if code.startswith(("6",)):
                market = "SH"
            elif code.startswith(("4", "8", "9")):
                market = "BJ"
            else:
                market = "SZ"
        return code, f"{code}.{market}"

    def _query_daily_source(
        self,
        ts_code: str,
        start: date,
        end: date,
        duckdb: Any,
        pandas: Any,
    ) -> Any:
        self._require_file(self.daily_path, "daily")
        columns = self._schema(self.daily_path, "daily", duckdb)
        missing = sorted(_REQUIRED_DAILY_COLUMNS - columns)
        if missing:
            raise LocalDataError(
                f"Local daily snapshot {self.daily_path} is missing required columns: {', '.join(missing)}"
            )
        factor_expression = (
            "adj_factor" if "adj_factor" in columns else "NULL::DOUBLE AS adj_factor"
        )
        select_columns = ", ".join(
            factor_expression if column == "adj_factor" else column
            for column in _DAILY_SOURCE_COLUMNS
        )
        sql = f"""
            SELECT {select_columns}
            FROM read_parquet(?)
            WHERE ts_code = ?
              AND trade_date <= CAST(? AS DATE)
            ORDER BY trade_date
        """
        frame = self._query(
            self.daily_path,
            sql,
            [str(self.daily_path), ts_code, end.isoformat()],
            "daily",
            duckdb,
            pandas,
        )
        if frame.empty:
            return frame
        frame["trade_date"] = pandas.to_datetime(frame["trade_date"], errors="coerce")
        if frame["trade_date"].isna().any():
            raise LocalDataError("Local daily snapshot contains an invalid trade_date value")
        if frame["trade_date"].duplicated().any():
            raise LocalDataError(f"Local daily snapshot contains duplicate dates for {ts_code}")
        # Keep one row before the requested range so period aggregates have a correct previous
        # close. The output itself is trimmed later.
        start_timestamp = pandas.Timestamp(start)
        before = frame.loc[frame["trade_date"] < start_timestamp].tail(1)
        requested = frame.loc[
            (frame["trade_date"] >= start_timestamp)
            & (frame["trade_date"] <= pandas.Timestamp(end))
        ]
        return pandas.concat([before, requested], ignore_index=True)

    def _factor_bases(self, ts_code: str, duckdb: Any, pandas: Any) -> tuple[float, float]:
        sql = """
            SELECT adj_factor
            FROM read_parquet(?)
            WHERE ts_code = ? AND adj_factor IS NOT NULL
            ORDER BY trade_date ASC
            LIMIT 1
        """
        first = self._query(
            self.daily_path,
            sql,
            [str(self.daily_path), ts_code],
            "daily",
            duckdb,
            pandas,
        )
        last = self._query(
            self.daily_path,
            sql.replace("ASC", "DESC"),
            [str(self.daily_path), ts_code],
            "daily",
            duckdb,
            pandas,
        )
        if first.empty or last.empty:
            raise LocalDataError(f"No adjustment factor is available for {ts_code}")
        first_factor = float(first.iloc[0]["adj_factor"])
        last_factor = float(last.iloc[0]["adj_factor"])
        if first_factor == 0 or last_factor == 0:
            raise LocalDataError(f"Adjustment factor for {ts_code} contains a zero value")
        return first_factor, last_factor

    @staticmethod
    def _numeric(frame: Any, pandas: Any, column: str) -> Any:
        frame[column] = pandas.to_numeric(frame[column], errors="coerce")
        return frame[column]

    def _prepare_prices(
        self,
        frame: Any,
        ts_code: str,
        adjust: str,
        duckdb: Any,
        pandas: Any,
    ) -> Any:
        frame = frame.copy()
        for column in (
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "change",
            "pct_chg",
            "vol",
            "amount",
            "turnover_rate",
            "adj_factor",
        ):
            self._numeric(frame, pandas, column)
        frame = frame.sort_values("trade_date").reset_index(drop=True)
        if adjust:
            if frame["adj_factor"].isna().any():
                raise LocalDataError(f"adjustment factor is missing for one or more {ts_code} rows")
            _, last_factor = self._factor_bases(ts_code, duckdb, pandas)
            # This snapshot uses a cumulative factor convention: hfq prices multiply by the
            # factor directly, while qfq prices normalize against the latest available factor.
            ratio = frame["adj_factor"] / last_factor if adjust == "qfq" else frame["adj_factor"]
            for column in ("open", "high", "low", "close"):
                frame[column] = frame[column] * ratio
            # The adjusted series, rather than raw pre_close, defines the return around corporate
            # actions. This matches the usual AkShare qfq/hfq interpretation.
            frame["pre_close"] = frame["close"].shift(1)
            frame["change"] = frame["close"] - frame["pre_close"]
            frame["pct_chg"] = frame["change"] / frame["pre_close"] * 100
        else:
            missing_change = frame["change"].isna() & frame["pre_close"].notna()
            frame.loc[missing_change, "change"] = (
                frame.loc[missing_change, "close"] - frame.loc[missing_change, "pre_close"]
            )
            missing_pct = frame["pct_chg"].isna() & frame["pre_close"].notna()
            frame.loc[missing_pct, "pct_chg"] = (
                frame.loc[missing_pct, "close"] / frame.loc[missing_pct, "pre_close"] - 1
            ) * 100
        frame["振幅"] = (frame["high"] - frame["low"]) / frame["pre_close"] * 100
        frame.loc[frame["pre_close"] == 0, "振幅"] = pandas.NA
        return frame

    @staticmethod
    def _aggregate_periods(frame: Any, start: date, end: date, period: str, pandas: Any) -> Any:
        if frame.empty:
            return frame
        requested = frame.loc[
            (frame["trade_date"] >= pandas.Timestamp(start))
            & (frame["trade_date"] <= pandas.Timestamp(end))
        ].copy()
        if requested.empty:
            return requested
        requested["period_key"] = requested["trade_date"].dt.to_period(
            "W-FRI" if period == "weekly" else "M"
        )
        records: list[dict[str, Any]] = []
        for _, group in requested.groupby("period_key", sort=True):
            group = group.sort_values("trade_date")
            first_date = group.iloc[0]["trade_date"]
            prior = frame.loc[frame["trade_date"] < first_date].sort_values("trade_date")
            previous_close = (
                prior.iloc[-1]["close"] if not prior.empty else group.iloc[0]["pre_close"]
            )
            close = group.iloc[-1]["close"]
            high = group["high"].max()
            low = group["low"].min()
            change = close - previous_close if pandas.notna(previous_close) else pandas.NA
            pct = (
                change / previous_close * 100
                if pandas.notna(previous_close) and previous_close != 0
                else pandas.NA
            )
            amplitude = (
                (high - low) / previous_close * 100
                if pandas.notna(previous_close) and previous_close != 0
                else pandas.NA
            )
            records.append(
                {
                    "trade_date": group.iloc[-1]["trade_date"],
                    "open": group.iloc[0]["open"],
                    "close": close,
                    "high": high,
                    "low": low,
                    "vol": group["vol"].sum(min_count=1),
                    "amount": group["amount"].sum(min_count=1),
                    "振幅": amplitude,
                    "pct_chg": pct,
                    "change": change,
                    "turnover_rate": group["turnover_rate"].sum(min_count=1),
                }
            )
        return pandas.DataFrame.from_records(records)

    def stock_zh_a_hist(
        self,
        symbol: str = "000001",
        period: str = "daily",
        start_date: str = "19700101",
        end_date: str = "20500101",
        adjust: str = "",
        timeout: float | None = None,
    ) -> Any:
        del timeout  # The local backend has no network timeout; retain AkShare's signature.
        period, start, end, adjust = _validate_hist_request(period, start_date, end_date, adjust)
        duckdb, pandas = _load_data_dependencies()
        code, ts_code = self._resolve_ts_code(symbol, duckdb, pandas)
        source = self._query_daily_source(ts_code, start, end, duckdb, pandas)
        if source.empty:
            return _empty_hist_frame(pandas)
        prepared = self._prepare_prices(source, ts_code, adjust, duckdb, pandas)
        if period != "daily":
            prepared = self._aggregate_periods(prepared, start, end, period, pandas)
        else:
            prepared = prepared.loc[
                (prepared["trade_date"] >= pandas.Timestamp(start))
                & (prepared["trade_date"] <= pandas.Timestamp(end))
            ].copy()
        if prepared.empty:
            return _empty_hist_frame(pandas)
        result = pandas.DataFrame(
            {
                "日期": pandas.to_datetime(prepared["trade_date"]).dt.date,
                "股票代码": code,
                "开盘": prepared["open"].to_numpy(),
                "收盘": prepared["close"].to_numpy(),
                "最高": prepared["high"].to_numpy(),
                "最低": prepared["low"].to_numpy(),
                "成交量": prepared["vol"].to_numpy(),
                "成交额": prepared["amount"].to_numpy(),
                "振幅": prepared["振幅"].to_numpy(),
                "涨跌幅": prepared["pct_chg"].to_numpy(),
                "涨跌额": prepared["change"].to_numpy(),
                "换手率": prepared["turnover_rate"].to_numpy(),
            },
            columns=HIST_COLUMNS,
        )
        result["股票代码"] = result["股票代码"].astype("string")
        return result.reset_index(drop=True)

    def stock_info_a_code_name(self) -> Any:
        duckdb, pandas = _load_data_dependencies()
        self._require_file(self.basic_path, "stock basic")
        columns = self._schema(self.basic_path, "stock basic", duckdb)
        missing = sorted(_REQUIRED_BASIC_COLUMNS - columns)
        if missing:
            raise LocalDataError(
                f"Local stock basic snapshot {self.basic_path} is missing required columns: {', '.join(missing)}"
            )
        frame = self._query(
            self.basic_path,
            """
            SELECT CAST(symbol AS VARCHAR) AS code, CAST(name AS VARCHAR) AS name
            FROM read_parquet(?)
            WHERE symbol IS NOT NULL
            ORDER BY code
            """,
            [str(self.basic_path)],
            "stock basic",
            duckdb,
            pandas,
        )
        if frame.empty:
            return pandas.DataFrame(
                {"code": pandas.Series(dtype="string"), "name": pandas.Series(dtype="string")}
            )
        frame["code"] = frame["code"].astype("string").str.strip().str.zfill(6)
        frame["name"] = frame["name"].astype("string")
        return frame[["code", "name"]].reset_index(drop=True)


class RemoteBackend:
    """Thin lazy proxy around the installed AkShare module."""

    def __init__(
        self,
        module: ModuleType | None = None,
        *,
        overrides: Mapping[str, Callable[..., Any]] | None = None,
        missing_endpoints: set[str] | None = None,
    ):
        self._module = module
        self._overrides = dict(overrides or {})
        self._missing_endpoints = set(missing_endpoints or ())

    @property
    def module(self) -> ModuleType:
        if self._module is None:
            try:
                self._module = importlib.import_module("akshare")
            except ImportError as exc:
                raise CompatibilityError(
                    "The remote backend requires AkShare. Install it with `python -m pip install -e '.[data]'`."
                ) from exc
        return self._module

    def call(self, endpoint: str, *args: Any, **kwargs: Any) -> Any:
        function = self._overrides.get(endpoint)
        if function is None:
            if endpoint in self._missing_endpoints:
                raise UnsupportedEndpointError(f"Installed AkShare has no endpoint {endpoint!r}")
            try:
                function = getattr(self.module, endpoint)
            except AttributeError as exc:
                raise UnsupportedEndpointError(
                    f"Installed AkShare has no endpoint {endpoint!r}"
                ) from exc
        return function(*args, **kwargs)


class AkShareCompat:
    """Facade exposing AkShare-compatible functions with configurable data provenance."""

    def __init__(
        self,
        config: CompatibilityConfig | None = None,
        *,
        remote_module: ModuleType | None = None,
        remote_overrides: Mapping[str, Callable[..., Any]] | None = None,
        remote_missing_endpoints: set[str] | None = None,
    ):
        self._config_override = config
        self._remote = RemoteBackend(
            remote_module,
            overrides=remote_overrides,
            missing_endpoints=remote_missing_endpoints,
        )

    @property
    def config(self) -> CompatibilityConfig:
        return self._config_override or CompatibilityConfig.from_env()

    def _dispatch(self, endpoint: str, *args: Any, **kwargs: Any) -> Any:
        config = self.config
        if config.backend == "remote":
            return self._remote.call(endpoint, *args, **kwargs)
        if endpoint in LOCAL_ENDPOINTS:
            try:
                local = LocalBackend(config)
                return getattr(local, endpoint)(*args, **kwargs)
            except (UnsupportedEndpointError, LocalDataUnavailableError):
                if config.backend == "hybrid" and config.allow_remote_fallback:
                    return self._remote.call(endpoint, *args, **kwargs)
                raise
        if config.backend == "hybrid" and config.allow_remote_fallback:
            return self._remote.call(endpoint, *args, **kwargs)
        raise UnsupportedEndpointError(
            f"Endpoint {endpoint!r} has no local implementation. "
            "Use backend=remote or explicitly enable hybrid remote fallback."
        )

    def stock_zh_a_hist(
        self,
        symbol: str = "000001",
        period: str = "daily",
        start_date: str = "19700101",
        end_date: str = "20500101",
        adjust: str = "",
        timeout: float | None = None,
    ) -> Any:
        return self._dispatch(
            "stock_zh_a_hist",
            symbol=symbol,
            period=period,
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
            timeout=timeout,
        )

    def stock_info_a_code_name(self) -> Any:
        return self._dispatch("stock_info_a_code_name")

    def __getattr__(self, endpoint: str) -> Callable[..., Any]:
        if endpoint.startswith("_"):
            raise AttributeError(endpoint)

        def call(*args: Any, **kwargs: Any) -> Any:
            return self._dispatch(endpoint, *args, **kwargs)

        call.__name__ = endpoint
        call.__qualname__ = endpoint
        return call


def get_akshare(config: CompatibilityConfig | None = None) -> AkShareCompat:
    """Return a facade instance; useful when a caller wants an explicit config object."""

    return AkShareCompat(config)


ak = AkShareCompat()


def install_local_backend(
    config: CompatibilityConfig | None = None,
    *,
    module: ModuleType | None = None,
) -> AkShareCompat:
    """Patch selected functions on the real AkShare module with the configurable facade.

    This is an integration convenience for legacy code that already does ``import akshare as ak``.
    Call it once during process startup; changing the environment variables on the next process
    start changes the backend without changing business code.
    """

    target = module or importlib.import_module("akshare")
    originals = getattr(target, "_forbiddenland_originals", None)
    if originals is None:
        originals = {}
        target._forbiddenland_originals = originals  # type: ignore[attr-defined]
    patch_names = set(LOCAL_ENDPOINTS) | {"stock_zh_a_spot_em"}
    for endpoint in patch_names:
        if endpoint not in originals:
            originals[endpoint] = getattr(target, endpoint, _MISSING_ORIGINAL)
    remote_overrides = {
        endpoint: original
        for endpoint, original in originals.items()
        if original is not _MISSING_ORIGINAL and callable(original)
    }
    remote_missing_endpoints = {
        endpoint for endpoint, original in originals.items() if original is _MISSING_ORIGINAL
    }
    compat = AkShareCompat(
        config,
        remote_module=target,
        remote_overrides=remote_overrides,
        remote_missing_endpoints=remote_missing_endpoints,
    )
    for endpoint in patch_names:
        replacement = getattr(compat, endpoint)
        original = originals.get(endpoint)
        if original is not _MISSING_ORIGINAL and callable(original):
            # ``replacement`` is a bound method; wraps() needs a real function object to attach
            # metadata to, so create a small forwarding function first.
            wrapped = replacement

            @functools.wraps(original)
            def replacement_function(
                *args: Any, _wrapped: Callable[..., Any] = wrapped, **kwargs: Any
            ) -> Any:
                return _wrapped(*args, **kwargs)

            replacement = replacement_function
        setattr(target, endpoint, replacement)
    return compat


def uninstall_backend(*, module: ModuleType | None = None) -> None:
    """Restore functions changed by :func:`install_local_backend`."""

    target = module or importlib.import_module("akshare")
    originals = getattr(target, "_forbiddenland_originals", None)
    if not originals:
        return
    for endpoint, original in originals.items():
        if original is _MISSING_ORIGINAL:
            try:
                delattr(target, endpoint)
            except AttributeError:
                pass
        else:
            setattr(target, endpoint, original)
    delattr(target, "_forbiddenland_originals")
