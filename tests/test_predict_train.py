from __future__ import annotations

from datetime import date

from predict_fixtures import make_bars

from research.predict import features as feat
from research.predict import labels as lbl
from research.predict import train as tr


def test_walk_forward_split_is_chronological() -> None:
    labeled = lbl.build_labels(feat.compute_features(make_bars(days=60)))
    split = tr.SplitDates(train_end=date(2024, 2, 1), valid_end=date(2024, 3, 1))
    train, valid, test = tr.split_by_date(labeled, split)
    dates = labeled["timestamp"].dt.date
    assert train.any() and valid.any() and test.any()
    assert dates[train].max() <= split.train_end
    assert split.train_end < dates[valid].min()
    assert dates[valid].max() <= split.valid_end
    assert dates[test].min() > split.valid_end


def test_fit_evaluate_and_threshold_selection() -> None:
    labeled = lbl.build_labels(feat.compute_features(make_bars(days=260)))
    features = list(feat.FEATURE_COLUMNS)
    split = tr.SplitDates(train_end=date(2024, 5, 1), valid_end=date(2024, 8, 1))
    train_mask, valid_mask, test_mask = tr.split_by_date(labeled, split)
    train_df = labeled[train_mask & labeled["eligible"]].dropna(subset=features)
    valid_df = labeled[valid_mask & labeled["eligible"]].dropna(subset=features)
    test_df = labeled[test_mask & labeled["eligible"]].dropna(subset=features)
    assert len(train_df) > 50 and len(valid_df) > 20 and len(test_df) > 20

    model, params = tr.fit_classifier(
        train_df,
        valid_df,
        features,
        seed=7,
        params={"n_estimators": 100, "early_stopping_rounds": 10},
    )
    assert params["seed"] == 7
    valid_scored = tr.predict_probabilities(model, valid_df, features)
    threshold = tr.select_threshold(valid_scored)
    assert 0.5 <= threshold <= 0.7

    test_scored = tr.predict_probabilities(model, test_df, features)
    metrics = tr.evaluate(test_scored, threshold)
    assert metrics["auc"] is not None and metrics["auc"] > 0.8
    assert metrics["rank_ic"] is not None
    assert metrics["accuracy"] > metrics["yesterday_direction_accuracy"]


def test_predict_keeps_missing_rows_missing() -> None:
    labeled = lbl.build_labels(feat.compute_features(make_bars(days=30)))
    features = list(feat.FEATURE_COLUMNS)
    clean = labeled[labeled["eligible"]].dropna(subset=features)
    subset = clean.head(10).copy()
    subset.loc[subset.index[0], "ret_1"] = float("nan")
    model, _ = tr.fit_classifier(
        clean.head(20),
        clean.tail(10),
        features,
        params={"n_estimators": 20, "early_stopping_rounds": 5},
    )
    scored = tr.predict_probabilities(model, subset, features)
    assert scored.loc[scored.index[0], "prob"] != scored.loc[scored.index[0], "prob"]
    assert scored.loc[scored.index[1], "prob"] == scored.loc[scored.index[1], "prob"]
