"""Register and persist an order-flow factor using easy-tdx's factor contract.

``easy_tdx.factor`` provides an in-process registry rather than a factor database.  This module
keeps the registration class small and deterministic, then offers an explicit export format that
can be consumed by ``FactorAnalyzer`` or another easy-tdx caller after a new process starts.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

try:
    from easy_tdx.factor import FACTORY_REGISTRY, Factor, register_factor
except ImportError as exc:  # pragma: no cover - exercised in a minimal installation.
    raise ImportError(
        "easy-tdx is required for the custom order-flow factor; "
        "install the project's data profile first"
    ) from exc

from .normalize import parse_symbol

EASY_TDX_FACTOR_NAME = "order_flow_delta_ratio"
EASY_TDX_FACTOR_VERSION = "order-flow-delta-ratio-1"
FactorOutputFrequency = Literal["bar", "daily"]

_FACTOR_INPUTS = ("buy_volume", "sell_volume", "total_transaction_volume")
_FACTOR_FORMULA = "(buy_volume - sell_volume) / total_transaction_volume"


@register_factor
class OrderFlowDeltaRatio(Factor):
    """Active buy/sell volume imbalance from normalized transaction aggregates.

    The factor is intentionally a direct Delta ratio rather than a weighted score.  This keeps its
    meaning stable across symbols and lets callers tune thresholds in a strategy without changing
    the factor definition.  Rows marked ``of_data_valid=False`` remain missing.
    """

    name = EASY_TDX_FACTOR_NAME
    category = "order_flow"
    description = "主动买卖成交量差占比（Delta / transaction volume）"
    inputs = _FACTOR_INPUTS

    def compute(self, df: pd.DataFrame) -> pd.Series:
        if not isinstance(df, pd.DataFrame):
            raise TypeError("order-flow factor input must be a pandas DataFrame")
        missing = sorted(set(self.inputs).difference(df.columns))
        if missing:
            raise ValueError("order-flow factor input is missing columns: " + ", ".join(missing))

        values = {column: pd.to_numeric(df[column], errors="coerce") for column in self.inputs}
        for column in self.inputs:
            negative = values[column].notna() & values[column].lt(0.0)
            if negative.any():
                raise ValueError(f"order-flow factor input contains negative {column}")

        total = values["total_transaction_volume"]
        denominator = total.where(total.gt(0.0))
        result = (values["buy_volume"] - values["sell_volume"]) / denominator
        result = result.replace([np.inf, -np.inf], np.nan).astype("float64")
        if "of_data_valid" in df.columns:
            valid = df["of_data_valid"].fillna(False).astype(bool)
            result = result.where(valid)
        result.name = self.name
        return result


@dataclass(frozen=True, slots=True)
class FactorBundle:
    """Paths and metadata for one persisted easy-tdx factor export."""

    data_path: Path
    manifest_path: Path
    manifest: dict[str, Any]


def ensure_order_flow_factor_registered() -> type[Factor]:
    """Return the registered factor class and fail clearly on a registry collision."""

    registered = FACTORY_REGISTRY.get(EASY_TDX_FACTOR_NAME)
    if registered is None:
        # This should only be reachable if a caller manually removed the registry entry after
        # importing this module.  Re-register through the public easy-tdx protocol in that case.
        registered = register_factor(OrderFlowDeltaRatio)
    if registered is not OrderFlowDeltaRatio:
        raise RuntimeError(
            f"easy-tdx factor name collision for {EASY_TDX_FACTOR_NAME!r}: "
            f"{registered.__module__}.{registered.__name__}"
        )
    return registered


def factor_definition() -> dict[str, Any]:
    """Return the serializable metadata contract for the registered factor."""

    factor = ensure_order_flow_factor_registered()
    return {
        "protocol": "easy_tdx.factor",
        "name": factor.name,
        "version": EASY_TDX_FACTOR_VERSION,
        "class": f"{factor.__module__}.{factor.__name__}",
        "category": factor.category,
        "description": factor.description,
        "inputs": list(factor.inputs),
        "formula": _FACTOR_FORMULA,
        "range": [-1.0, 1.0],
        "missing_policy": "missing transaction or invalid quality rows remain NaN",
    }


def compute_order_flow_factor(frame: pd.DataFrame) -> pd.Series:
    """Compute the factor through easy-tdx's ``FactorEngine`` contract."""

    ensure_order_flow_factor_registered()
    from easy_tdx.factor import FactorEngine

    result = FactorEngine().compute_single(frame, [EASY_TDX_FACTOR_NAME])
    return result[EASY_TDX_FACTOR_NAME]


def _resolve_code(value: Any) -> tuple[str, str]:
    """Return a six-digit code and its exchange-qualified symbol."""

    text = str(value).strip()
    try:
        _exchange, code, qualified = parse_symbol(text)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"factor export contains invalid A-share symbol: {value!r}") from exc
    return code, qualified


def _prepare_export_frame(frame: pd.DataFrame, symbol: str | None) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("factor export input must be a pandas DataFrame")
    if "timestamp" in frame.columns:
        timestamp = pd.to_datetime(frame["timestamp"], errors="coerce")
    elif "datetime" in frame.columns:
        timestamp = pd.to_datetime(frame["datetime"], errors="coerce")
    else:
        raise ValueError("factor export input must contain timestamp or datetime")
    if timestamp.isna().any():
        raise ValueError("factor export input contains invalid timestamps")

    data = frame.copy()
    data["datetime"] = timestamp
    if "symbol" in data.columns:
        raw_symbols = data["symbol"].astype("string").str.strip()
    elif symbol is not None:
        raw_symbols = pd.Series(symbol, index=data.index, dtype="string")
    else:
        raise ValueError("factor export needs a symbol column or an explicit symbol")
    if raw_symbols.isna().any() or raw_symbols.eq("").any():
        raise ValueError("factor export contains an empty symbol")

    parsed = raw_symbols.map(_resolve_code)
    data["code"] = parsed.map(lambda value: value[0])
    data["symbol"] = parsed.map(lambda value: value[1])
    data["date"] = data["datetime"].dt.strftime("%Y%m%d").astype("int64")
    data["_factor_value"] = compute_order_flow_factor(data)
    data = data.sort_values(["code", "datetime"], kind="mergesort").reset_index(drop=True)
    if data.duplicated(["code", "datetime"]).any():
        raise ValueError("factor export contains duplicate code/datetime rows")
    return data


def build_easy_tdx_factor_frame(
    frame: pd.DataFrame,
    *,
    frequency: FactorOutputFrequency = "daily",
    symbol: str | None = None,
) -> pd.DataFrame:
    """Build a factor table compatible with easy-tdx research consumers.

    ``daily`` emits one row per ``date``/``code`` and is directly suitable for
    ``FactorAnalyzer``.  ``bar`` preserves every input timestamp for intraday inspection; because
    easy-tdx's cross-sectional analyzer uses integer dates, bar output is not a daily analyzer
    input until the caller applies an explicit intraday-to-session aggregation.
    """

    if frequency not in {"bar", "daily"}:
        raise ValueError("factor export frequency must be bar or daily")
    data = _prepare_export_frame(frame, symbol)
    if frequency == "bar":
        return data[["date", "code", "symbol", "datetime", "_factor_value"]].rename(
            columns={"_factor_value": EASY_TDX_FACTOR_NAME}
        )

    valid = data["_factor_value"].notna()
    data["_buy_for_factor"] = pd.to_numeric(data["buy_volume"], errors="coerce").where(valid)
    data["_sell_for_factor"] = pd.to_numeric(data["sell_volume"], errors="coerce").where(valid)
    data["_total_for_factor"] = pd.to_numeric(
        data["total_transaction_volume"], errors="coerce"
    ).where(valid)
    grouped = data.groupby(["date", "code"], sort=True, dropna=False)
    totals = grouped[["_buy_for_factor", "_sell_for_factor", "_total_for_factor"]].sum(min_count=1)
    timestamps = grouped["datetime"].max().rename("datetime")
    symbols = grouped["symbol"].first().rename("symbol")
    result = pd.concat([timestamps, symbols, totals], axis=1).reset_index()
    denominator = result["_total_for_factor"].where(result["_total_for_factor"].gt(0.0))
    result[EASY_TDX_FACTOR_NAME] = (
        result["_buy_for_factor"] - result["_sell_for_factor"]
    ) / denominator
    result[EASY_TDX_FACTOR_NAME] = result[EASY_TDX_FACTOR_NAME].replace([np.inf, -np.inf], np.nan)
    return (
        result[["date", "code", "symbol", "datetime", EASY_TDX_FACTOR_NAME]]
        .sort_values(["date", "code"], kind="mergesort")
        .reset_index(drop=True)
    )


def _json_value(value: Any) -> Any:
    """Convert numpy/pandas values into strict JSON values."""

    if value is None:
        return None
    if isinstance(value, (np.integer, np.floating, np.bool_)):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if pd.isna(value) if not isinstance(value, (list, tuple, dict)) else False:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


def save_easy_tdx_factor_bundle(
    factor_frame: pd.DataFrame,
    data_path: str | Path,
    *,
    manifest_path: str | Path | None = None,
    provenance: Mapping[str, Any] | None = None,
    frequency: FactorOutputFrequency = "daily",
) -> FactorBundle:
    """Persist factor values and a provenance/contract manifest.

    The data file uses Parquet when requested and available, with an explicit CSV fallback.  The
    manifest records the actual path and never relies on easy-tdx having a cross-process registry.
    """

    if not isinstance(factor_frame, pd.DataFrame):
        raise TypeError("factor_frame must be a pandas DataFrame")
    required = {"date", "code", EASY_TDX_FACTOR_NAME}
    missing = sorted(required.difference(factor_frame.columns))
    if missing:
        raise ValueError("factor frame is missing columns: " + ", ".join(missing))
    if frequency not in {"bar", "daily"}:
        raise ValueError("factor export frequency must be bar or daily")

    destination = Path(data_path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    actual_path = destination
    if destination.suffix.casefold() == ".parquet":
        try:
            factor_frame.to_parquet(destination, index=False)
        except (ImportError, ModuleNotFoundError):
            actual_path = destination.with_suffix(".csv")
            factor_frame.to_csv(actual_path, index=False, encoding="utf-8")
    else:
        factor_frame.to_csv(destination, index=False, encoding="utf-8")

    manifest_destination = (
        Path(manifest_path).expanduser()
        if manifest_path is not None
        else actual_path.with_name(actual_path.stem + ".manifest.json")
    )
    manifest_destination.parent.mkdir(parents=True, exist_ok=True)
    factor_values = pd.to_numeric(factor_frame[EASY_TDX_FACTOR_NAME], errors="coerce")
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "protocol": "easy_tdx.factor",
        "factor": factor_definition(),
        "data": {
            "path": str(actual_path),
            "format": actual_path.suffix.casefold().lstrip("."),
            "frequency": frequency,
            "rows": len(factor_frame),
            "columns": [str(column) for column in factor_frame.columns],
            "missing_values": int(factor_values.isna().sum()),
        },
        "provenance": _json_value(dict(provenance or {})),
    }
    manifest_destination.write_text(
        json.dumps(_json_value(manifest), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return FactorBundle(
        data_path=actual_path,
        manifest_path=manifest_destination,
        manifest=manifest,
    )


__all__ = [
    "EASY_TDX_FACTOR_NAME",
    "EASY_TDX_FACTOR_VERSION",
    "FactorBundle",
    "FactorOutputFrequency",
    "OrderFlowDeltaRatio",
    "build_easy_tdx_factor_frame",
    "compute_order_flow_factor",
    "ensure_order_flow_factor_registered",
    "factor_definition",
    "save_easy_tdx_factor_bundle",
]
