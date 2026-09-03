from __future__ import annotations

import json
from datetime import time

import numpy as np
import pandas as pd
import pytest

from research.order_flow.config import OrderFlowConfig
from research.order_flow.predict import (
    OrderFlowPredictionConfig,
    RidgeReturnModel,
    _config_from_args,
    _parser,
    apply_prediction_signals,
    build_prediction_frame,
    factor_event_study,
    fit_predict_latest,
    main,
    run_prediction_backtest,
    walk_forward_predict,
)


def _prediction_bars(days: int = 12, bars_per_segment: int = 8) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for day_index, day in enumerate(pd.bdate_range("2026-01-05", periods=days)):
        for session_start in (time(9, 30), time(13, 0)):
            for slot in range(bars_per_segment):
                timestamp = pd.Timestamp(day) + pd.to_timedelta(
                    int(session_start.hour * 60 + session_start.minute + slot * 5),
                    unit="min",
                )
                open_price = 10.0 + day_index * 0.02 + slot * 0.01
                close = open_price + 0.002 * (1 if slot % 2 == 0 else -1)
                factor = 0.25 if (day_index + slot) % 3 else -0.25
                rows.append(
                    {
                        "timestamp": timestamp,
                        "symbol": "SH:688183",
                        "session_date_key": int(day.strftime("%Y%m%d")),
                        "is_session_bar": True,
                        "open": open_price,
                        "high": max(open_price, close) + 0.01,
                        "low": min(open_price, close) - 0.01,
                        "close": close,
                        "volume": 1_000.0 + slot,
                        "of_data_valid": True,
                        "of_history_ready": True,
                        "order_flow_delta_ratio": factor,
                        "delta_ratio_zscore": factor * 2.0,
                        "relative_transaction_volume": 1.0 + slot / 10.0,
                        "clv": 0.5 if slot % 2 == 0 else -0.5,
                        "bar_return": (close - open_price) / open_price,
                        "vwap_distance": 0.001,
                        "flow_price_divergence": 0,
                    }
                )
    return pd.DataFrame(rows)


def test_prediction_labels_are_next_open_and_do_not_cross_lunch() -> None:
    frame = _prediction_bars(days=1, bars_per_segment=5)
    config = OrderFlowPredictionConfig(horizon_bars=1, factor_lags=(1,))
    result = build_prediction_frame(frame, config=config)

    morning = result.loc[result["timestamp"].dt.time.eq(time(9, 30))].iloc[0]
    expected_entry = result.loc[result["timestamp"].dt.time.eq(time(9, 35)), "open"].iloc[0]
    expected_exit = result.loc[result["timestamp"].dt.time.eq(time(9, 40)), "open"].iloc[0]
    assert morning["prediction_entry_open"] == pytest.approx(expected_entry)
    assert morning["prediction_exit_open"] == pytest.approx(expected_exit)
    assert morning["future_return"] == pytest.approx(expected_exit / expected_entry - 1.0)

    morning_terminal = result.loc[result["timestamp"].dt.time.eq(time(9, 50))].iloc[0]
    assert bool(morning_terminal["prediction_target_available"]) is False
    assert morning_terminal["prediction_invalid_reason"] == "future_window_missing"

    afternoon_first = result.loc[result["timestamp"].dt.time.eq(time(13, 0))].iloc[0]
    assert pd.isna(afternoon_first["order_flow_delta_ratio_lag_1"])


def test_prediction_target_rejects_missing_intermediate_bar() -> None:
    frame = _prediction_bars(days=1, bars_per_segment=6)
    remove = frame.index[frame["timestamp"].dt.time.eq(time(9, 40))]
    frame = frame.drop(remove)
    result = build_prediction_frame(
        frame,
        config=OrderFlowPredictionConfig(horizon_bars=1, factor_lags=()),
    )
    row = result.loc[result["timestamp"].dt.time.eq(time(9, 35))].iloc[0]
    assert bool(row["prediction_target_available"]) is False
    assert row["prediction_invalid_reason"] == "bar_interval_or_gap"


def test_prediction_keeps_invalid_rows_visible() -> None:
    frame = _prediction_bars(days=2, bars_per_segment=5)
    frame.loc[0, "of_data_valid"] = False
    frame.loc[1, "order_flow_delta_ratio"] = np.nan
    result = build_prediction_frame(
        frame,
        config=OrderFlowPredictionConfig(horizon_bars=1, factor_lags=()),
    )
    assert bool(result.iloc[0]["prediction_feature_eligible"]) is False
    assert result.iloc[0]["prediction_invalid_reason"] == "invalid_order_flow_data"
    assert bool(result.iloc[1]["prediction_feature_eligible"]) is False
    assert result.iloc[1]["prediction_invalid_reason"] == "factor_missing"


def test_prediction_rejects_non_finite_factor_and_invalid_open() -> None:
    frame = _prediction_bars(days=1, bars_per_segment=6)
    frame.loc[0, "order_flow_delta_ratio"] = np.inf
    frame.loc[1, "open"] = np.nan
    result = build_prediction_frame(
        frame,
        config=OrderFlowPredictionConfig(horizon_bars=1, factor_lags=()),
    )
    assert bool(result.iloc[0]["prediction_feature_eligible"]) is False
    assert result.iloc[0]["prediction_invalid_reason"] == "factor_non_finite"
    assert bool(result.iloc[1]["prediction_feature_eligible"]) is False
    assert result.iloc[1]["prediction_invalid_reason"] == "current_open_invalid"


def test_prediction_parses_csv_boolean_strings_explicitly() -> None:
    frame = _prediction_bars(days=1, bars_per_segment=5)
    frame["is_session_bar"] = frame["is_session_bar"].astype("string")
    frame.loc[0, "is_session_bar"] = "False"
    result = build_prediction_frame(
        frame,
        config=OrderFlowPredictionConfig(horizon_bars=1, factor_lags=()),
    )
    assert bool(result.iloc[0]["prediction_feature_eligible"]) is False
    assert result.iloc[0]["prediction_invalid_reason"] == "out_of_session"

    frame.loc[0, "is_session_bar"] = "not-a-boolean"
    with pytest.raises(ValueError, match="is_session_bar.*invalid boolean"):
        build_prediction_frame(
            frame,
            config=OrderFlowPredictionConfig(horizon_bars=1, factor_lags=()),
        )


def test_event_study_returns_factor_buckets_and_net_return() -> None:
    result = factor_event_study(
        _prediction_bars(days=8, bars_per_segment=6),
        config=OrderFlowPredictionConfig(horizon_bars=1, factor_lags=(), round_trip_cost=0.001),
        bins=3,
    )
    assert list(result.columns) == [
        "factor_bucket",
        "n",
        "factor_min",
        "factor_max",
        "factor_mean",
        "mean_return",
        "median_return",
        "mean_net_return",
        "hit_rate",
    ]
    assert result["factor_bucket"].tolist() == [1, 2, 3]
    assert (result["mean_net_return"] == result["mean_return"] - 0.001).all()


def test_ridge_model_uses_train_medians_and_drops_all_missing_columns() -> None:
    features = pd.DataFrame(
        {
            "factor": [0.0, 1.0, 2.0, np.nan],
            "context": [1.0, np.nan, 3.0, 4.0],
            "empty": [np.nan, np.nan, np.nan, np.nan],
        }
    )
    target = pd.Series([0.0, 0.1, 0.2, 0.3])
    model = RidgeReturnModel(alpha=1.0).fit(features, target)
    prediction = model.predict(pd.DataFrame({"factor": [3.0], "context": [2.0]}))
    assert np.isfinite(prediction).all()
    assert "empty" not in model.feature_columns_
    assert model.n_samples_ == 4


def test_walk_forward_predictions_start_only_after_training_window() -> None:
    source = _prediction_bars(days=12, bars_per_segment=6)
    config = OrderFlowPredictionConfig(
        horizon_bars=1,
        train_sessions=3,
        validation_sessions=1,
        test_sessions=2,
        min_train_rows=4,
        factor_lags=(1,),
    )
    result = walk_forward_predict(source, config=config)
    predicted = result.frame.loc[result.frame["predicted_return"].notna()]
    assert not predicted.empty
    first_predicted_date = int(predicted["prediction_session_date"].min())
    all_dates = sorted(result.frame["prediction_session_date"].unique())
    assert first_predicted_date == all_dates[4]
    assert result.metrics["successful_fold_count"] > 0
    assert (
        result.metrics["folds_with_predictions"]
        == result.frame["prediction_fold"].dropna().nunique()
    )
    assert (result.folds["train_rows"] >= config.min_train_rows).all()


def test_latest_prediction_can_score_rows_without_future_labels() -> None:
    source = _prediction_bars(days=6, bars_per_segment=4)
    config = OrderFlowPredictionConfig(
        horizon_bars=2,
        train_sessions=3,
        min_train_rows=3,
        factor_lags=(1,),
    )
    result = fit_predict_latest(source, config=config)
    latest_date = result["prediction_session_date"].max()
    latest = result.loc[result["prediction_session_date"].eq(latest_date)]
    assert latest["prediction_target_available"].eq(False).any()
    assert latest["predicted_return"].notna().any()


def test_apply_prediction_signals_requires_target_for_historical_backtest() -> None:
    source = _prediction_bars(days=1, bars_per_segment=5)
    source["predicted_return"] = [0.01] * len(source)
    source["prediction_signal"] = True
    source["prediction_target_available"] = [True] * 4 + [False] * (len(source) - 4)
    result = apply_prediction_signals(source, require_target_available=True)
    assert result["of_entry_signal"].sum() == 4
    assert result["of_exit_signal"].sum() == 0
    assert result["prediction_execution_signal"].eq(1).sum() == 4


def test_apply_prediction_signals_can_score_without_a_future_label() -> None:
    source = _prediction_bars(days=1, bars_per_segment=2)
    source["predicted_return"] = 0.01
    source["prediction_signal"] = True
    source["prediction_feature_eligible"] = True
    source["prediction_target_available"] = False
    result = apply_prediction_signals(source)
    assert result["of_entry_signal"].all()


def test_prediction_backtest_masks_labels_before_engine() -> None:
    source = _prediction_bars(days=8, bars_per_segment=6)
    prediction = walk_forward_predict(
        source,
        config=OrderFlowPredictionConfig(
            horizon_bars=1,
            train_sessions=2,
            validation_sessions=1,
            test_sessions=1,
            min_train_rows=3,
            factor_lags=(1,),
        ),
    )
    result = run_prediction_backtest(
        prediction.frame,
        config=OrderFlowConfig(
            bar_minutes=5,
            min_history_sessions=2,
            volume_baseline_sessions=2,
            entry_persistence=1,
        ),
    )
    assert "future_return" not in result.execution_frame.columns
    assert "of_entry_signal" in result.execution_frame.columns
    assert result.execution_frame["datetime"].is_unique


def test_prediction_config_json_and_cli_overrides(tmp_path) -> None:
    config_path = tmp_path / "prediction.json"
    config_path.write_text(
        '{"horizon_bars": 6, "train_sessions": 12, "factor_lags": [2], "round_trip_cost": 0.001}',
        encoding="utf-8",
    )
    args = _parser().parse_args(
        [
            "--config",
            str(config_path),
            "--features",
            "features.csv",
            "--output",
            "prediction.csv",
            "--report",
            "prediction.json",
            "--horizon-bars",
            "3",
        ]
    )
    config = _config_from_args(args)
    assert config.horizon_bars == 3
    assert config.train_sessions == 12
    assert config.factor_lags == (2,)
    assert config.round_trip_cost == pytest.approx(0.001)


def test_prediction_cli_writes_walk_forward_metrics_and_provenance(tmp_path) -> None:
    features_path = tmp_path / "features.csv"
    report_path = tmp_path / "source.json"
    output_path = tmp_path / "predictions.csv"
    prediction_report_path = tmp_path / "predictions.json"
    _prediction_bars(days=8, bars_per_segment=6).to_csv(features_path, index=False)
    report_path.write_text(
        '{"data": {"source": "fixture", "retrieved_at_utc": "2026-01-01T00:00:00Z"}}',
        encoding="utf-8",
    )
    assert (
        main(
            [
                "--features",
                str(features_path),
                "--source-report",
                str(report_path),
                "--output",
                str(output_path),
                "--report",
                str(prediction_report_path),
                "--horizon-bars",
                "1",
                "--train-sessions",
                "3",
                "--validation-sessions",
                "1",
                "--test-sessions",
                "1",
                "--min-train-rows",
                "3",
                "--factor-lags",
                "",
            ]
        )
        == 0
    )
    payload = json.loads(prediction_report_path.read_text(encoding="utf-8"))
    assert payload["metrics"]["successful_fold_count"] > 0
    assert payload["source_provenance"]["source"] == "fixture"
    assert output_path.exists()
