"""AkShare-compatible access backed by either remote AkShare or local Parquet snapshots.

The public methods intentionally mirror the subset of AkShare used by this project. The local
backend is deliberately explicit about unsupported endpoints: a missing local dataset must not
silently turn a reproducible research run into a network request.
"""

from __future__ import annotations

import functools
import importlib
import zipfile
from collections.abc import Callable, Mapping
from datetime import date, datetime
from io import BytesIO
from pathlib import Path, PurePosixPath
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

CONCEPT_NAME_COLUMNS = ["name", "code"]
CONCEPT_INFO_COLUMNS = ["项目", "值"]
CONCEPT_INDEX_COLUMNS = ["日期", "开盘价", "最高价", "最低价", "收盘价", "成交量", "成交额"]
CONCEPT_SUMMARY_COLUMNS = ["日期", "概念名称", "驱动事件", "龙头股", "成分股数量"]

LOCAL_ENDPOINTS = frozenset(
    {
        "stock_zh_a_hist",
        "stock_info_a_code_name",
        "stock_board_concept_name_ths",
        "stock_board_concept_info_ths",
        "stock_board_concept_index_ths",
        "stock_board_concept_summary_ths",
    }
)
_MISSING_ORIGINAL = object()

__all__ = [
    "CONCEPT_INDEX_COLUMNS",
    "CONCEPT_INFO_COLUMNS",
    "CONCEPT_NAME_COLUMNS",
    "CONCEPT_SUMMARY_COLUMNS",
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
_REQUIRED_THS_CONCEPT_CATALOG_COLUMNS = frozenset(
    {"代码", "名称", "成分个数", "交易所", "上市日期", "指数类型"}
)
_REQUIRED_THS_CONCEPT_MEMBER_COLUMNS = frozenset(
    {"指数代码", "指数名称", "指数类型", "股票代码", "股票名称"}
)
_REQUIRED_THS_QUOTE_COLUMNS = frozenset(
    {
        "指数代码",
        "交易日期",
        "开盘点位",
        "最高点位",
        "最低点位",
        "收盘点位",
        "昨日收盘点",
        "平均价",
        "涨跌点位",
        "涨跌幅",
        "成交量",
        "换手率",
    }
)
_THS_QUOTE_NUMERIC_COLUMNS = (
    "开盘点位",
    "最高点位",
    "最低点位",
    "收盘点位",
    "昨日收盘点",
    "平均价",
    "涨跌点位",
    "涨跌幅",
    "成交量",
    "换手率",
)
_THS_CONCEPT_INFO_ITEMS = (
    "今开",
    "昨收",
    "最低",
    "最高",
    "成交量(万手)",
    "板块涨幅",
    "涨幅排名",
    "涨跌家数",
    "资金净流入(亿)",
    "成交额(亿)",
)


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


def _validate_concept_index_request(
    start_date: str | date | datetime,
    end_date: str | date | datetime,
) -> tuple[date, date]:
    start = _parse_date(start_date, "start_date")
    end = _parse_date(end_date, "end_date")
    if start > end:
        raise InvalidRequestError("start_date must not be later than end_date")
    return start, end


def _empty_hist_frame(pandas: Any) -> Any:
    frame = pandas.DataFrame(columns=HIST_COLUMNS)
    frame["股票代码"] = frame["股票代码"].astype("string")
    return frame


def _empty_concept_index_frame(pandas: Any) -> Any:
    return pandas.DataFrame(columns=CONCEPT_INDEX_COLUMNS)


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

    @property
    def ths_concept_catalog_path(self) -> Path:
        return _as_path(self.config.resolved_ths_concept_catalog_file())

    @property
    def ths_concept_members_path(self) -> Path:
        return _as_path(self.config.resolved_ths_concept_members_file())

    @property
    def ths_sector_quotes_path(self) -> Path:
        return _as_path(self.config.resolved_ths_sector_quotes_file())

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

    def _concept_catalog(self, duckdb: Any, pandas: Any) -> Any:
        path = self.ths_concept_catalog_path
        self._require_file(path, "Tonghuashun concept catalog")
        columns = self._schema(path, "Tonghuashun concept catalog", duckdb)
        missing = sorted(_REQUIRED_THS_CONCEPT_CATALOG_COLUMNS - columns)
        if missing:
            raise LocalDataError(
                f"Local Tonghuashun concept catalog {path} is missing required columns: "
                f"{', '.join(missing)}"
            )
        frame = self._query(
            path,
            """
            SELECT
                CAST("代码" AS VARCHAR) AS code,
                CAST("名称" AS VARCHAR) AS name,
                CAST("上市日期" AS VARCHAR) AS listing_date
            FROM read_parquet(?)
            WHERE CAST("交易所" AS VARCHAR) = 'A股'
              AND CAST("指数类型" AS VARCHAR) = '概念指数'
              AND LEFT(CAST("代码" AS VARCHAR), 3) IN ('885', '886')
              AND RIGHT(CAST("代码" AS VARCHAR), 3) = '.TI'
            ORDER BY code
            """,
            [str(path)],
            "Tonghuashun concept catalog",
            duckdb,
            pandas,
        )
        if frame.empty:
            raise LocalDataError(
                f"Local Tonghuashun concept catalog {path} contains no A-share 885/886 concepts"
            )
        frame["code"] = frame["code"].astype("string").str.strip().str.upper()
        frame["name"] = frame["name"].astype("string").str.strip()
        invalid = (
            frame["code"].isna()
            | frame["name"].isna()
            | frame["code"].eq("")
            | frame["name"].eq("")
        )
        if invalid.any():
            raise LocalDataError(
                f"Local Tonghuashun concept catalog {path} contains empty concept codes or names"
            )
        if frame["code"].duplicated().any():
            raise LocalDataError(
                f"Local Tonghuashun concept catalog {path} contains duplicate concept codes"
            )
        if frame["name"].duplicated().any():
            raise LocalDataError(
                f"Local Tonghuashun concept catalog {path} contains duplicate concept names"
            )
        return frame.reset_index(drop=True)

    def _resolve_concept(
        self,
        symbol: str,
        duckdb: Any,
        pandas: Any,
    ) -> tuple[str, str, Any]:
        if not isinstance(symbol, str) or not symbol.strip():
            raise InvalidRequestError("symbol must be a non-empty Tonghuashun concept name or code")
        value = symbol.strip()
        catalog = self._concept_catalog(duckdb, pandas)
        if value.upper().endswith(".TI"):
            matches = catalog.loc[catalog["code"] == value.upper()]
        else:
            matches = catalog.loc[catalog["name"] == value]
        if matches.empty:
            raise LocalDataUnavailableError(
                f"Tonghuashun concept {symbol!r} is not present in the local A-share 885/886 snapshot; "
                "use a concept name or local six-digit .TI code"
            )
        row = matches.iloc[0]
        return str(row["code"]), str(row["name"]), row["listing_date"]

    def _concept_member_counts(self, duckdb: Any, pandas: Any) -> Any:
        path = self.ths_concept_members_path
        self._require_file(path, "Tonghuashun concept members")
        columns = self._schema(path, "Tonghuashun concept members", duckdb)
        missing = sorted(_REQUIRED_THS_CONCEPT_MEMBER_COLUMNS - columns)
        if missing:
            raise LocalDataError(
                f"Local Tonghuashun concept members snapshot {path} is missing required columns: "
                f"{', '.join(missing)}"
            )
        frame = self._query(
            path,
            """
            SELECT
                UPPER(TRIM(CAST("指数代码" AS VARCHAR))) AS code,
                COUNT(*) AS actual_count,
                COUNT(DISTINCT CAST("股票代码" AS VARCHAR)) AS unique_count,
                SUM(
                    CASE
                        WHEN "股票代码" IS NULL
                          OR TRIM(CAST("股票代码" AS VARCHAR)) = ''
                        THEN 1 ELSE 0
                    END
                ) AS invalid_count
            FROM read_parquet(?)
            WHERE CAST("指数类型" AS VARCHAR) = '概念指数'
              AND LEFT(CAST("指数代码" AS VARCHAR), 3) IN ('885', '886')
            GROUP BY code
            ORDER BY code
            """,
            [str(path)],
            "Tonghuashun concept members",
            duckdb,
            pandas,
        )
        if frame.empty:
            raise LocalDataError(
                f"Local Tonghuashun concept members snapshot {path} contains no 885/886 concepts"
            )
        if (frame["invalid_count"] > 0).any():
            raise LocalDataError(
                f"Local Tonghuashun concept members snapshot {path} contains empty stock codes"
            )
        if (frame["actual_count"] != frame["unique_count"]).any():
            raise LocalDataError(
                f"Local Tonghuashun concept members snapshot {path} contains duplicate "
                "(concept code, stock code) relationships"
            )
        frame["code"] = frame["code"].astype("string")
        frame["actual_count"] = frame["actual_count"].astype("int64")
        return frame[["code", "actual_count"]].reset_index(drop=True)

    def _read_concept_quotes(self, code: str, pandas: Any) -> Any:
        path = self.ths_sector_quotes_path
        self._require_file(path, "Tonghuashun sector quote archive")
        expected_name = f"{code}.parquet"
        try:
            with zipfile.ZipFile(path) as archive:
                matches = [
                    name for name in archive.namelist() if PurePosixPath(name).name == expected_name
                ]
                if not matches:
                    raise LocalDataUnavailableError(
                        f"Local Tonghuashun sector quote archive {path} has no member "
                        f"{expected_name}"
                    )
                if len(matches) > 1:
                    raise LocalDataError(
                        f"Local Tonghuashun sector quote archive {path} contains multiple "
                        f"members named {expected_name}"
                    )
                payload = archive.read(matches[0])
        except LocalDataError:
            raise
        except (OSError, zipfile.BadZipFile) as exc:
            raise LocalDataError(
                f"Unable to read local Tonghuashun sector quote archive {path}: {exc}"
            ) from exc

        try:
            frame = pandas.read_parquet(BytesIO(payload))
        except ImportError as exc:
            raise LocalDataUnavailableError(
                "Reading zipped Tonghuashun Parquet quotes requires a pandas Parquet engine. "
                "Install the data profile with `python -m pip install -e '.[data]'`."
            ) from exc
        except Exception as exc:
            raise LocalDataError(
                f"Unable to read {expected_name} from local Tonghuashun archive {path}: {exc}"
            ) from exc

        missing = sorted(_REQUIRED_THS_QUOTE_COLUMNS - set(frame.columns))
        if missing:
            raise LocalDataError(
                f"Local Tonghuashun quote member {expected_name} is missing required columns: "
                f"{', '.join(missing)}"
            )
        if frame.empty:
            return frame
        source_codes = frame["指数代码"].astype("string").str.strip().str.upper()
        if source_codes.isna().any() or set(source_codes.tolist()) != {code}:
            raise LocalDataError(
                f"Local Tonghuashun quote member {expected_name} contains a mismatched index code"
            )
        parsed_dates = pandas.to_datetime(frame["交易日期"], errors="coerce")
        if parsed_dates.isna().any():
            raise LocalDataError(
                f"Local Tonghuashun quote member {expected_name} contains an invalid trade date"
            )
        if parsed_dates.duplicated().any():
            raise LocalDataError(
                f"Local Tonghuashun quote member {expected_name} contains duplicate trade dates"
            )
        frame = frame.copy()
        frame["交易日期"] = parsed_dates
        for column in _THS_QUOTE_NUMERIC_COLUMNS:
            source = frame[column]
            converted = pandas.to_numeric(source, errors="coerce")
            if (source.notna() & converted.isna()).any():
                raise LocalDataError(
                    f"Local Tonghuashun quote member {expected_name} contains a non-numeric "
                    f"{column} value"
                )
            frame[column] = converted
        return frame.sort_values("交易日期").reset_index(drop=True)

    @staticmethod
    def _format_ths_info_value(value: Any, pandas: Any, *, percent: bool = False) -> Any:
        if pandas.isna(value):
            return pandas.NA
        suffix = "%" if percent else ""
        return f"{float(value):.2f}{suffix}"

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
        # Read only the requested interval plus its immediately preceding bar.  The preceding
        # bar is needed to calculate the first return in a range and the opening value of a
        # partial week/month, while keeping the large Parquet scan out of pandas.
        sql = f"""
            WITH requested AS (
                SELECT {select_columns}
                FROM read_parquet(?)
                WHERE ts_code = ?
                  AND trade_date BETWEEN CAST(? AS DATE) AND CAST(? AS DATE)
            ), previous AS (
                SELECT {select_columns}
                FROM read_parquet(?)
                WHERE ts_code = ?
                  AND trade_date < CAST(? AS DATE)
                ORDER BY trade_date DESC
                LIMIT 1
            )
            SELECT * FROM previous
            UNION ALL
            SELECT * FROM requested
            ORDER BY trade_date
        """
        frame = self._query(
            self.daily_path,
            sql,
            [
                str(self.daily_path),
                ts_code,
                start.isoformat(),
                end.isoformat(),
                str(self.daily_path),
                ts_code,
                start.isoformat(),
            ],
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

    def _latest_factor(self, ts_code: str, duckdb: Any, pandas: Any) -> float:
        sql = """
            SELECT adj_factor
            FROM read_parquet(?)
            WHERE ts_code = ? AND adj_factor IS NOT NULL
            ORDER BY trade_date DESC
            LIMIT 1
        """
        result = self._query(
            self.daily_path,
            sql,
            [str(self.daily_path), ts_code],
            "daily",
            duckdb,
            pandas,
        )
        if result.empty:
            raise LocalDataError(f"No adjustment factor is available for {ts_code}")
        latest_factor = float(result.iloc[0]["adj_factor"])
        if latest_factor == 0:
            raise LocalDataError(f"Adjustment factor for {ts_code} contains a zero value")
        return latest_factor

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
            # This snapshot uses a cumulative factor convention: hfq prices multiply by the
            # factor directly, while qfq prices normalize against the latest available factor.
            ratio = frame["adj_factor"]
            if adjust == "qfq":
                ratio = ratio / self._latest_factor(ts_code, duckdb, pandas)
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

    def stock_board_concept_name_ths(self) -> Any:
        duckdb, pandas = _load_data_dependencies()
        catalog = self._concept_catalog(duckdb, pandas)
        return catalog[["name", "code"]].reset_index(drop=True)

    def stock_board_concept_info_ths(self, symbol: str = "阿里巴巴概念") -> Any:
        duckdb, pandas = _load_data_dependencies()
        code, _, _ = self._resolve_concept(symbol, duckdb, pandas)
        quotes = self._read_concept_quotes(code, pandas)
        values: dict[str, Any] = {item: pandas.NA for item in _THS_CONCEPT_INFO_ITEMS}
        if not quotes.empty:
            latest = quotes.iloc[-1]
            values.update(
                {
                    "今开": self._format_ths_info_value(latest["开盘点位"], pandas),
                    "昨收": self._format_ths_info_value(latest["昨日收盘点"], pandas),
                    "最低": self._format_ths_info_value(latest["最低点位"], pandas),
                    "最高": self._format_ths_info_value(latest["最高点位"], pandas),
                    "板块涨幅": self._format_ths_info_value(latest["涨跌幅"], pandas, percent=True),
                }
            )
        return pandas.DataFrame(
            {
                "项目": list(_THS_CONCEPT_INFO_ITEMS),
                "值": [values[item] for item in _THS_CONCEPT_INFO_ITEMS],
            },
            columns=CONCEPT_INFO_COLUMNS,
        )

    def stock_board_concept_index_ths(
        self,
        symbol: str = "阿里巴巴概念",
        start_date: str = "20200101",
        end_date: str = "20250228",
    ) -> Any:
        start, end = _validate_concept_index_request(start_date, end_date)
        duckdb, pandas = _load_data_dependencies()
        code, _, _ = self._resolve_concept(symbol, duckdb, pandas)
        quotes = self._read_concept_quotes(code, pandas)
        if quotes.empty:
            return _empty_concept_index_frame(pandas)
        selected = quotes.loc[
            (quotes["交易日期"] >= pandas.Timestamp(start))
            & (quotes["交易日期"] <= pandas.Timestamp(end))
        ].copy()
        if selected.empty:
            return _empty_concept_index_frame(pandas)
        result = pandas.DataFrame(
            {
                "日期": selected["交易日期"].dt.date.to_numpy(),
                "开盘价": selected["开盘点位"].to_numpy(),
                "最高价": selected["最高点位"].to_numpy(),
                "最低价": selected["最低点位"].to_numpy(),
                "收盘价": selected["收盘点位"].to_numpy(),
                "成交量": selected["成交量"].to_numpy(),
                # The audited archive has no turnover-amount field. Missing is distinct from zero.
                "成交额": pandas.array([pandas.NA] * len(selected), dtype="Float64"),
            },
            columns=CONCEPT_INDEX_COLUMNS,
        )
        return result.reset_index(drop=True)

    def stock_board_concept_summary_ths(self) -> Any:
        duckdb, pandas = _load_data_dependencies()
        catalog = self._concept_catalog(duckdb, pandas)
        counts = self._concept_member_counts(duckdb, pandas)
        catalog_codes = set(catalog["code"].tolist())
        count_codes = set(counts["code"].tolist())
        missing_codes = sorted(catalog_codes - count_codes)
        extra_codes = sorted(count_codes - catalog_codes)
        if missing_codes or extra_codes:
            details = []
            if missing_codes:
                details.append(f"missing member snapshots for {', '.join(missing_codes)}")
            if extra_codes:
                details.append(
                    f"member snapshots without catalog entries for {', '.join(extra_codes)}"
                )
            raise LocalDataError(
                "Local Tonghuashun concept catalog and member snapshot disagree: "
                + "; ".join(details)
            )
        merged = catalog.merge(counts, on="code", how="left", validate="one_to_one")
        raw_dates = merged["listing_date"].astype("string").str.strip()
        parsed_dates = pandas.to_datetime(raw_dates, format="%Y%m%d", errors="coerce")
        invalid_dates = raw_dates.notna() & parsed_dates.isna()
        if invalid_dates.any():
            invalid_code = str(merged.loc[invalid_dates, "code"].iloc[0])
            raise LocalDataError(
                "Local Tonghuashun concept catalog contains an invalid listing date for "
                f"{invalid_code}"
            )
        merged["listing_timestamp"] = parsed_dates
        merged = merged.sort_values(
            ["listing_timestamp", "code"], ascending=[False, True], na_position="last"
        ).reset_index(drop=True)
        result = pandas.DataFrame(
            {
                "日期": merged["listing_timestamp"].dt.date.to_numpy(),
                "概念名称": merged["name"].to_numpy(),
                # These narrative fields are not present in the supplied local snapshot.
                "驱动事件": pandas.array([pandas.NA] * len(merged), dtype="string"),
                "龙头股": pandas.array([pandas.NA] * len(merged), dtype="string"),
                "成分股数量": merged["actual_count"].to_numpy(),
            },
            columns=CONCEPT_SUMMARY_COLUMNS,
        )
        return result.reset_index(drop=True)


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

    def stock_board_concept_name_ths(self) -> Any:
        return self._dispatch("stock_board_concept_name_ths")

    def stock_board_concept_info_ths(self, symbol: str = "阿里巴巴概念") -> Any:
        return self._dispatch("stock_board_concept_info_ths", symbol=symbol)

    def stock_board_concept_index_ths(
        self,
        symbol: str = "阿里巴巴概念",
        start_date: str = "20200101",
        end_date: str = "20250228",
    ) -> Any:
        return self._dispatch(
            "stock_board_concept_index_ths",
            symbol=symbol,
            start_date=start_date,
            end_date=end_date,
        )

    def stock_board_concept_summary_ths(self) -> Any:
        return self._dispatch("stock_board_concept_summary_ths")

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
