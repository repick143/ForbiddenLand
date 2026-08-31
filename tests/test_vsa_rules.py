from __future__ import annotations

import pandas as pd

from research.vsa.features import VSAConfig, compute_vsa_features
from research.vsa.rules import (
    CANDIDATE_NO_DEMAND,
    CANDIDATE_NO_SUPPLY,
    CANDIDATE_STOPPING_VOLUME,
    CANDIDATE_TEST,
    CANDIDATE_UPTHRUST,
    apply_vsa_rules,
    summarize_vsa_events,
)
from research.vsa.run import build_fixture, generate_vsa_frame


def test_fixture_exposes_all_first_batch_vsa_candidates_and_confirmations() -> None:
    features = generate_vsa_frame(build_fixture())
    summary = summarize_vsa_events(features)

    expected = {"no_demand", "upthrust", "no_supply", "stopping_volume", "test"}
    observed = set(features.loc[features["vsa_candidate_code"].ne(0), "vsa_candidate"])
    assert observed == expected
    assert summary["confirmed_long_count"] == 3
    assert summary["confirmed_exit_count"] == 2
    assert summary["confirmation_status_counts"]["confirmed"] == 5

    candidates = features.loc[features["vsa_candidate_code"].ne(0)]
    signals = features.loc[features["vsa_confirmed_signal"].ne(0)]
    assert set(signals["vsa_candidate_code"]) == {0}
    assert all(signals["timestamp"] > signals["vsa_reference_timestamp"])
    assert set(candidates["vsa_candidate_code"]) == {
        CANDIDATE_NO_DEMAND,
        CANDIDATE_UPTHRUST,
        CANDIDATE_NO_SUPPLY,
        CANDIDATE_STOPPING_VOLUME,
        CANDIDATE_TEST,
    }


def test_candidate_without_a_following_bar_remains_pending() -> None:
    raw = build_fixture().iloc[:31].copy()
    config = VSAConfig()
    output = apply_vsa_rules(compute_vsa_features(raw, config), config)

    candidate = output.loc[output["vsa_candidate_code"].ne(0)]
    assert len(candidate) == 1
    assert candidate.iloc[0]["vsa_confirmation_status"] == "pending"
    assert int(output["vsa_confirmed_signal"].sum()) == 0


def test_failed_next_bar_invalidates_candidate_without_signal() -> None:
    raw = build_fixture().copy()
    # The no-supply candidate is at row 30; close below its low invalidates it on row 31.
    raw.loc[31, "open"] = 27.95
    raw.loc[31, "close"] = 27.80
    raw.loc[31, "high"] = 28.05
    raw.loc[31, "low"] = 27.70
    config = VSAConfig()
    output = apply_vsa_rules(compute_vsa_features(raw, config), config)

    candidate = output.loc[output["vsa_candidate"].eq("no_supply")].iloc[0]
    assert candidate["vsa_confirmation_status"] == "invalidated"
    assert int(output["vsa_confirmed_signal"].abs().sum()) < 5


def test_rules_skip_invalid_rows() -> None:
    raw = build_fixture().copy()
    raw.loc[30, "volume"] = -1.0
    config = VSAConfig()
    computed = compute_vsa_features(raw, config)
    output = apply_vsa_rules(computed, config)

    assert int(output.loc[30, "vsa_candidate_code"]) == 0
    assert output.loc[30, "vsa_candidate"] == "none"


def test_rules_preserve_symbol_alias_boundaries() -> None:
    first = build_fixture()
    second = first.copy()
    second["symbol"] = "600183"
    raw = pd.concat([first, second], ignore_index=True).rename(columns={"symbol": "TICKER"})
    computed = compute_vsa_features(raw)
    ordered = apply_vsa_rules(computed)

    output = apply_vsa_rules(computed.sample(frac=1.0, random_state=23))

    expected = ordered.set_index(["TICKER", "timestamp"]).sort_index()
    actual = output.set_index(["TICKER", "timestamp"]).sort_index()
    for column in ("vsa_candidate_code", "vsa_confirmation_status", "vsa_confirmed_signal"):
        pd.testing.assert_series_equal(
            expected[column],
            actual[column],
            check_names=False,
            check_dtype=False,
        )


def test_confirmation_uses_chronological_next_bar_for_shuffled_input() -> None:
    computed = compute_vsa_features(build_fixture())
    ordered = apply_vsa_rules(computed)
    shuffled = apply_vsa_rules(computed.sample(frac=1.0, random_state=17))

    columns = (
        "vsa_candidate_code",
        "vsa_confirmation_status",
        "vsa_confirmed_signal",
        "vsa_confirmed_code",
        "vsa_reference_timestamp",
    )
    expected = ordered.set_index("timestamp").sort_index()
    actual = shuffled.set_index("timestamp").sort_index()
    for column in columns:
        pd.testing.assert_series_equal(
            expected[column],
            actual[column],
            check_names=False,
            check_dtype=False,
        )


def test_empty_rule_frame_keeps_rule_version() -> None:
    computed = compute_vsa_features(build_fixture().iloc[:0])

    output = apply_vsa_rules(computed)

    assert output.empty
    assert "vsa_rule_version" in output.columns
