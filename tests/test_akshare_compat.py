from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest

pd = pytest.importorskip("pandas")
duckdb = pytest.importorskip("duckdb")

from forbiddenland.config import CompatibilityConfig, ConfigurationError
from forbiddenland.integrations.akshare_compat import (
    HIST_COLUMNS,
    AkShareCompat,
    LocalDataError,
    LocalDataUnavailableError,
    UnsupportedEndpointError,
    install_local_backend,
    uninstall_backend,
)


def _write_parquet(frame: pd.DataFrame, path: Path) -> None:
    connection = duckdb.connect()
    try:
        connection.register("fixture_frame", frame)
        connection.execute("COPY fixture_frame TO ? (FORMAT PARQUET)", [str(path)])
        connection.unregister("fixture_frame")
    finally:
        connection.close()


@pytest.fixture()
def local_data(tmp_path: Path) -> Path:
    raw = tmp_path / "raw"
    raw.mkdir()
    dates = pd.to_datetime(
        [
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
            "2024-01-05",
            "2024-01-08",
            "2024-01-31",
            "2024-02-01",
        ]
    )
    closes = [10.0, 11.0, 12.0, 13.0, 12.5, 14.0, 15.0]
    factors = [2.0, 2.4, 2.4, 3.0, 3.0, 3.6, 4.0]
    rows = []
    previous = 9.0
    for trade_date, close, factor in zip(dates, closes, factors, strict=True):
        rows.append(
            {
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "pre_close": previous,
                "change": close - previous,
                "pct_chg": (close / previous - 1) * 100,
                "vol": 100.0,
                "amount": 1000.0,
                "turnover_rate": 2.0,
                "adj_factor": factor,
                "trade_date": trade_date,
                "ts_code": "688256.SH",
            }
        )
        previous = close
    _write_parquet(pd.DataFrame(rows), raw / "stock_daily.parquet")
    _write_parquet(
        pd.DataFrame(
            {
                "ts_code": ["688256.SH", "688072.SH", "600183.SH"],
                "symbol": ["688256", "688072", "600183"],
                "name": ["寒武纪", "拓荆科技", "生益科技"],
            }
        ),
        raw / "stock_basic_data.parquet",
    )
    return tmp_path


def test_config_reads_backend_aliases_and_paths(tmp_path: Path) -> None:
    config = CompatibilityConfig.from_env(
        {
            "FORBIDDENLAND_BACKEND": "remote",
            "FORBIDDENLAND_DATA_ROOT": str(tmp_path),
            "FORBIDDENLAND_ALLOW_REMOTE_FALLBACK": "yes",
        }
    )

    assert config.backend == "remote"
    assert config.data_root == tmp_path
    assert config.allow_remote_fallback is True
    assert config.resolved_daily_file() == tmp_path / "raw" / "stock_daily.parquet"


def test_config_defaults_to_remote_backend() -> None:
    config = CompatibilityConfig.from_env({})

    assert config.backend == "remote"
    assert config.allow_remote_fallback is False


def test_facade_defaults_to_remote_even_when_local_snapshot_exists(
    local_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("FORBIDDENLAND_MARKET_BACKEND", raising=False)
    monkeypatch.delenv("FORBIDDENLAND_BACKEND", raising=False)
    monkeypatch.delenv("FORBIDDENLAND_DATA_BACKEND", raising=False)
    remote = ModuleType("fake_akshare")
    remote.stock_zh_a_hist = lambda **_: "remote-hist"  # type: ignore[attr-defined]

    api = AkShareCompat(remote_module=remote)

    assert api.stock_zh_a_hist(symbol="688256") == "remote-hist"


def test_config_rejects_invalid_values() -> None:
    with pytest.raises(ConfigurationError):
        CompatibilityConfig(backend="network")  # type: ignore[arg-type]
    with pytest.raises(ConfigurationError):
        CompatibilityConfig.from_env({"FORBIDDENLAND_ALLOW_REMOTE_FALLBACK": "sometimes"})


def test_local_daily_result_matches_akshare_shape(local_data: Path) -> None:
    api = AkShareCompat(CompatibilityConfig(backend="local", data_root=local_data))

    result = api.stock_zh_a_hist(
        symbol="688256",
        start_date="20240103",
        end_date="20240104",
    )

    assert list(result.columns) == HIST_COLUMNS
    assert result["股票代码"].tolist() == ["688256", "688256"]
    assert result["日期"].tolist() == [
        pd.Timestamp("2024-01-03").date(),
        pd.Timestamp("2024-01-04").date(),
    ]
    assert result["收盘"].tolist() == [11.0, 12.0]
    assert result["振幅"].iloc[0] == pytest.approx((12 - 10) / 10 * 100)
    assert str(result["股票代码"].dtype) == "string"


def test_local_daily_accepts_exchange_qualified_symbol_and_empty_range(local_data: Path) -> None:
    api = AkShareCompat(CompatibilityConfig(backend="local", data_root=local_data))

    qualified = api.stock_zh_a_hist(
        symbol="688256.SH",
        start_date="20240102",
        end_date="20240102",
    )
    empty = api.stock_zh_a_hist(
        symbol="999999",
        start_date="20240102",
        end_date="20240102",
    )

    assert qualified["股票代码"].tolist() == ["688256"]
    assert empty.empty
    assert list(empty.columns) == HIST_COLUMNS


def test_local_adjustment_factor_produces_qfq_and_hfq(local_data: Path) -> None:
    api = AkShareCompat(CompatibilityConfig(backend="local", data_root=local_data))

    qfq = api.stock_zh_a_hist(
        symbol="688256",
        start_date="20240102",
        end_date="20240105",
        adjust="qfq",
    )
    hfq = api.stock_zh_a_hist(
        symbol="688256",
        start_date="20240102",
        end_date="20240105",
        adjust="hfq",
    )

    assert qfq["收盘"].iloc[0] == pytest.approx(10 / 2.0)
    assert qfq["收盘"].iloc[-1] == pytest.approx(13 * 3.0 / 4.0)
    assert hfq["收盘"].iloc[0] == pytest.approx(10 * 2.0)
    assert hfq["收盘"].iloc[-1] == pytest.approx(13 * 3.0)
    assert qfq["涨跌额"].iloc[1] == pytest.approx(qfq["收盘"].iloc[1] - qfq["收盘"].iloc[0])


def test_local_weekly_and_monthly_aggregation(local_data: Path) -> None:
    api = AkShareCompat(CompatibilityConfig(backend="local", data_root=local_data))

    weekly = api.stock_zh_a_hist(
        symbol="688256",
        period="weekly",
        start_date="20240102",
        end_date="20240201",
    )
    monthly = api.stock_zh_a_hist(
        symbol="688256",
        period="monthly",
        start_date="20240102",
        end_date="20240201",
    )

    assert weekly["日期"].tolist() == [
        pd.Timestamp("2024-01-05").date(),
        pd.Timestamp("2024-01-08").date(),
        pd.Timestamp("2024-02-01").date(),
    ]
    assert monthly["日期"].tolist() == [
        pd.Timestamp("2024-01-31").date(),
        pd.Timestamp("2024-02-01").date(),
    ]
    assert monthly["开盘"].iloc[0] == pytest.approx(9.5)
    assert monthly["收盘"].iloc[0] == pytest.approx(14.0)
    assert monthly["成交量"].iloc[0] == pytest.approx(600.0)


def test_local_request_validation(local_data: Path) -> None:
    api = AkShareCompat(CompatibilityConfig(backend="local", data_root=local_data))

    with pytest.raises(ValueError, match="period"):
        api.stock_zh_a_hist(period="minute")
    with pytest.raises(ValueError, match="start_date"):
        api.stock_zh_a_hist(start_date="20240105", end_date="20240101")
    with pytest.raises(ValueError, match="adjust"):
        api.stock_zh_a_hist(adjust="split")


def test_local_stock_list_is_sorted_and_preserves_codes(local_data: Path) -> None:
    api = AkShareCompat(CompatibilityConfig(backend="local", data_root=local_data))

    result = api.stock_info_a_code_name()

    assert list(result.columns) == ["code", "name"]
    assert result["code"].tolist() == ["600183", "688072", "688256"]
    assert result["name"].tolist() == ["生益科技", "拓荆科技", "寒武纪"]


def test_missing_local_file_fails_without_network(local_data: Path) -> None:
    config = CompatibilityConfig(backend="local", data_root=local_data / "missing")
    api = AkShareCompat(config)

    with pytest.raises(LocalDataUnavailableError, match="daily snapshot"):
        api.stock_zh_a_hist(symbol="688256")


def test_local_malformed_schema_is_reported(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    malformed = pd.DataFrame(
        {
            "ts_code": ["688256.SH"],
            "trade_date": [pd.Timestamp("2024-01-01")],
            "close": [10.0],
        }
    )
    _write_parquet(malformed, raw / "stock_daily.parquet")
    api = AkShareCompat(CompatibilityConfig(backend="local", data_root=tmp_path))

    with pytest.raises(LocalDataError, match="missing required columns"):
        api.stock_zh_a_hist(symbol="688256")


def test_unadjusted_query_does_not_require_adjustment_factor(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    raw.mkdir()
    row = {
        "open": 9.5,
        "high": 11.0,
        "low": 9.0,
        "close": 10.0,
        "pre_close": 9.0,
        "change": 1.0,
        "pct_chg": 100 / 9,
        "vol": 100.0,
        "amount": 1000.0,
        "turnover_rate": 2.0,
        "trade_date": pd.Timestamp("2024-01-02"),
        "ts_code": "688256.SH",
    }
    _write_parquet(pd.DataFrame([row]), raw / "stock_daily.parquet")
    api = AkShareCompat(CompatibilityConfig(backend="local", data_root=tmp_path))

    result = api.stock_zh_a_hist(symbol="688256.SH", start_date="20240102", end_date="20240102")

    assert result["收盘"].tolist() == [10.0]
    with pytest.raises(LocalDataError, match="adjustment factor"):
        api.stock_zh_a_hist(
            symbol="688256.SH", start_date="20240102", end_date="20240102", adjust="qfq"
        )


def test_remote_backend_is_selected_without_touching_local_files() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    remote = ModuleType("fake_akshare")

    def stock_zh_a_hist(**kwargs: object) -> str:
        calls.append(("hist", kwargs))
        return "remote-hist"

    remote.stock_zh_a_hist = stock_zh_a_hist  # type: ignore[attr-defined]
    api = AkShareCompat(CompatibilityConfig(backend="remote"), remote_module=remote)

    assert api.stock_zh_a_hist(symbol="688256") == "remote-hist"
    assert calls == [
        (
            "hist",
            {
                "symbol": "688256",
                "period": "daily",
                "start_date": "19700101",
                "end_date": "20500101",
                "adjust": "",
                "timeout": None,
            },
        )
    ]


def test_one_facade_switches_backend_from_environment(
    local_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = ModuleType("fake_akshare")
    remote.stock_zh_a_hist = lambda **_: "remote-hist"  # type: ignore[attr-defined]
    api = AkShareCompat(remote_module=remote)

    monkeypatch.setenv("FORBIDDENLAND_MARKET_BACKEND", "local")
    monkeypatch.setenv("FORBIDDENLAND_DATA_ROOT", str(local_data))
    local_result = api.stock_zh_a_hist(symbol="688256", start_date="20240102", end_date="20240102")
    assert local_result["收盘"].tolist() == [10.0]

    monkeypatch.setenv("FORBIDDENLAND_MARKET_BACKEND", "remote")
    assert api.stock_zh_a_hist(symbol="688256") == "remote-hist"


def test_hybrid_can_explicitly_fall_back_to_remote() -> None:
    remote = ModuleType("fake_akshare")
    remote.stock_zh_a_hist = lambda **_: "remote-hist"  # type: ignore[attr-defined]
    config = CompatibilityConfig(
        backend="hybrid",
        data_root=Path("does-not-exist"),
        allow_remote_fallback=True,
    )
    api = AkShareCompat(config, remote_module=remote)

    assert api.stock_zh_a_hist(symbol="688256") == "remote-hist"


def test_local_mode_rejects_unavailable_endpoint() -> None:
    api = AkShareCompat(CompatibilityConfig(backend="local"))

    with pytest.raises(UnsupportedEndpointError, match="no local implementation"):
        api.stock_zh_a_spot_em()


def test_install_and_uninstall_backend(local_data: Path) -> None:
    module = ModuleType("fake_akshare")
    original_hist = lambda **_: "original-hist"
    original_spot = lambda: "original-spot"
    module.stock_zh_a_hist = original_hist  # type: ignore[attr-defined]
    module.stock_zh_a_spot_em = original_spot  # type: ignore[attr-defined]
    config = CompatibilityConfig(backend="local", data_root=local_data)

    install_local_backend(config, module=module)
    try:
        result = module.stock_zh_a_hist(  # type: ignore[attr-defined]
            symbol="688256", start_date="20240102", end_date="20240102"
        )
        assert result["股票代码"].tolist() == ["688256"]
        with pytest.raises(UnsupportedEndpointError):
            module.stock_zh_a_spot_em()  # type: ignore[attr-defined]
    finally:
        uninstall_backend(module=module)

    assert module.stock_zh_a_hist is original_hist  # type: ignore[attr-defined]
    assert module.stock_zh_a_spot_em is original_spot  # type: ignore[attr-defined]


def test_installed_backend_can_switch_back_to_remote(local_data: Path) -> None:
    module = ModuleType("fake_akshare")
    calls: list[str] = []

    def original_hist(**_: object) -> str:
        calls.append("remote")
        return "original-hist"

    module.stock_zh_a_hist = original_hist  # type: ignore[attr-defined]
    module.stock_zh_a_spot_em = lambda: "original-spot"  # type: ignore[attr-defined]

    install_local_backend(
        CompatibilityConfig(backend="remote", data_root=local_data), module=module
    )
    try:
        assert module.stock_zh_a_hist(symbol="688256") == "original-hist"  # type: ignore[attr-defined]
    finally:
        uninstall_backend(module=module)

    assert calls == ["remote"]


def test_install_and_uninstall_removes_new_endpoint() -> None:
    module = ModuleType("fake_akshare")

    install_local_backend(
        CompatibilityConfig(backend="local", data_root=Path("missing")), module=module
    )
    assert hasattr(module, "stock_zh_a_spot_em")
    uninstall_backend(module=module)

    assert not hasattr(module, "stock_zh_a_spot_em")
