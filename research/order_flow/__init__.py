"""Standalone easy-tdx transaction-direction order-flow research direction."""

from typing import Any

from .aggregate import (
    aggregate_transactions_to_bars,
    resolve_transaction_alignment,
    session_bar_mask,
)
from .backtest import OrderFlowBacktestResult, prepare_backtest_frame, run_order_flow_backtest
from .collector import EasyTdxCollector, EasyTdxOrderFlowSnapshot, TransactionPageAudit
from .config import (
    ORDER_FLOW_VERSION,
    ExecutionMode,
    OrderFlowConfig,
    PositionMode,
    RejectPolicy,
    SignalPath,
    TransactionAlignment,
    UnknownDirectionPolicy,
)
from .features import compute_order_flow_features, summarize_order_flow
from .normalize import (
    BS_FLAG_DIRECTION,
    BS_FLAG_LABEL,
    classify_session,
    normalize_bar_frame,
    normalize_transaction_frame,
    parse_symbol,
)
from .strategy import OrderFlowStrategy, make_order_flow_strategy

_LAZY_FACTOR_EXPORTS = {
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
}


def __getattr__(name: str) -> Any:
    """Load the optional easy-tdx factor API only when a caller requests it."""

    if name in _LAZY_FACTOR_EXPORTS:
        from . import easy_tdx_factor

        value = getattr(easy_tdx_factor, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BS_FLAG_DIRECTION",
    "BS_FLAG_LABEL",
    "EASY_TDX_FACTOR_NAME",
    "EASY_TDX_FACTOR_VERSION",
    "ORDER_FLOW_VERSION",
    "EasyTdxCollector",
    "EasyTdxOrderFlowSnapshot",
    "ExecutionMode",
    "FactorBundle",
    "FactorOutputFrequency",
    "OrderFlowBacktestResult",
    "OrderFlowConfig",
    "OrderFlowDeltaRatio",
    "OrderFlowStrategy",
    "PositionMode",
    "RejectPolicy",
    "SignalPath",
    "TransactionAlignment",
    "TransactionPageAudit",
    "UnknownDirectionPolicy",
    "aggregate_transactions_to_bars",
    "build_easy_tdx_factor_frame",
    "classify_session",
    "compute_order_flow_factor",
    "compute_order_flow_features",
    "ensure_order_flow_factor_registered",
    "factor_definition",
    "make_order_flow_strategy",
    "normalize_bar_frame",
    "normalize_transaction_frame",
    "parse_symbol",
    "prepare_backtest_frame",
    "resolve_transaction_alignment",
    "run_order_flow_backtest",
    "save_easy_tdx_factor_bundle",
    "session_bar_mask",
    "summarize_order_flow",
]
