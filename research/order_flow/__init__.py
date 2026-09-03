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
from .participation import (
    PARTICIPATION_COMPONENT_COLUMNS,
    PARTICIPATION_DAILY_QUANTILE,
    PARTICIPATION_FACTOR_NAME,
    PARTICIPATION_FACTOR_VERSION,
    compute_participation_features,
    participation_score_from_components,
    summarize_participation_sessions,
)
from .strategy import OrderFlowStrategy, make_order_flow_strategy

_LAZY_FACTOR_EXPORTS = {
    "EASY_TDX_FACTOR_NAME",
    "EASY_TDX_FACTOR_VERSION",
    "FactorBundle",
    "FactorOutputFrequency",
    "OrderFlowDeltaRatio",
    "OrderFlowParticipationScore",
    "build_easy_tdx_factor_frame",
    "build_easy_tdx_participation_factor_frame",
    "compute_order_flow_factor",
    "compute_participation_factor",
    "ensure_order_flow_factor_registered",
    "ensure_participation_factor_registered",
    "factor_definition",
    "participation_factor_definition",
    "save_easy_tdx_factor_bundle",
}
_LAZY_PREDICTION_EXPORTS = {
    "DEFAULT_FACTOR_LAGS",
    "DEFAULT_PREDICTION_FEATURES",
    "PREDICTION_VERSION",
    "OrderFlowPredictionConfig",
    "OrderFlowPredictionResult",
    "PredictionTarget",
    "RidgeReturnModel",
    "apply_prediction_signals",
    "build_prediction_frame",
    "factor_event_study",
    "fit_predict_latest",
    "run_prediction_backtest",
    "summarize_predictions",
    "walk_forward_predict",
}


def __getattr__(name: str) -> Any:
    """Load optional factor and prediction APIs only when a caller requests them."""

    if name in _LAZY_FACTOR_EXPORTS:
        from . import easy_tdx_factor

        value = getattr(easy_tdx_factor, name)
        globals()[name] = value
        return value
    if name in _LAZY_PREDICTION_EXPORTS:
        from . import predict

        value = getattr(predict, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "BS_FLAG_DIRECTION",
    "BS_FLAG_LABEL",
    "DEFAULT_FACTOR_LAGS",
    "DEFAULT_PREDICTION_FEATURES",
    "EASY_TDX_FACTOR_NAME",
    "EASY_TDX_FACTOR_VERSION",
    "ORDER_FLOW_VERSION",
    "PARTICIPATION_COMPONENT_COLUMNS",
    "PARTICIPATION_DAILY_QUANTILE",
    "PARTICIPATION_FACTOR_NAME",
    "PARTICIPATION_FACTOR_VERSION",
    "PREDICTION_VERSION",
    "EasyTdxCollector",
    "EasyTdxOrderFlowSnapshot",
    "ExecutionMode",
    "FactorBundle",
    "FactorOutputFrequency",
    "OrderFlowBacktestResult",
    "OrderFlowConfig",
    "OrderFlowDeltaRatio",
    "OrderFlowParticipationScore",
    "OrderFlowPredictionConfig",
    "OrderFlowPredictionResult",
    "OrderFlowStrategy",
    "PositionMode",
    "PredictionTarget",
    "RejectPolicy",
    "RidgeReturnModel",
    "SignalPath",
    "TransactionAlignment",
    "TransactionPageAudit",
    "UnknownDirectionPolicy",
    "aggregate_transactions_to_bars",
    "apply_prediction_signals",
    "build_easy_tdx_factor_frame",
    "build_easy_tdx_participation_factor_frame",
    "build_prediction_frame",
    "classify_session",
    "compute_order_flow_factor",
    "compute_order_flow_features",
    "compute_participation_factor",
    "compute_participation_features",
    "ensure_order_flow_factor_registered",
    "ensure_participation_factor_registered",
    "factor_definition",
    "factor_event_study",
    "fit_predict_latest",
    "make_order_flow_strategy",
    "normalize_bar_frame",
    "normalize_transaction_frame",
    "parse_symbol",
    "participation_factor_definition",
    "participation_score_from_components",
    "prepare_backtest_frame",
    "resolve_transaction_alignment",
    "run_order_flow_backtest",
    "run_prediction_backtest",
    "save_easy_tdx_factor_bundle",
    "session_bar_mask",
    "summarize_order_flow",
    "summarize_participation_sessions",
    "summarize_predictions",
    "walk_forward_predict",
]
