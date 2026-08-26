from __future__ import annotations

import zipfile
from pathlib import Path
from types import ModuleType

import pytest

pd = pytest.importorskip("pandas")
duckdb = pytest.importorskip("duckdb")

from forbiddenland.config import CompatibilityConfig, ConfigurationError
from forbiddenland.integrations.akshare_compat import (
    CONCEPT_INDEX_COLUMNS,
    CONCEPT_INFO_COLUMNS,
    CONCEPT_NAME_COLUMNS,
    CONCEPT_SUMMARY_COLUMNS,
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


def _write_parquet_zip(frames: dict[str, pd.DataFrame], path: Path) -> None:
    with zipfile.ZipFile(path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, frame in frames.items():
            archive.writestr(name, frame.to_parquet(index=False))


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
    ths = raw / "行业概念板块"
    ths.mkdir()
    _write_parquet(
        pd.DataFrame(
            [
                {
                    "代码": "885611.TI",
                    "名称": "阿里巴巴概念",
                    "成分个数": 1.0,
                    "交易所": "A股",
                    "上市日期": 20150203,
                    "指数类型": "概念指数",
                },
                {
                    "代码": "886009.TI",
                    "名称": "先进封装",
                    "成分个数": 2.0,
                    "交易所": "A股",
                    "上市日期": 20220808,
                    "指数类型": "概念指数",
                },
                {
                    "代码": "886112.TI",
                    "名称": "MLCC概念",
                    "成分个数": 2.0,
                    "交易所": "A股",
                    "上市日期": 20260731,
                    "指数类型": "概念指数",
                },
                {
                    "代码": "865174.TI",
                    "名称": "先进封装",
                    "成分个数": 5.0,
                    "交易所": "美股",
                    "上市日期": 20220809,
                    "指数类型": "概念指数",
                },
            ]
        ),
        ths / "行业概念板块_同花顺.parquet",
    )
    _write_parquet(
        pd.DataFrame(
            [
                {
                    "指数代码": "885611.TI",
                    "指数名称": "阿里巴巴概念",
                    "指数类型": "概念指数",
                    "股票代码": "688256.SH",
                    "股票名称": "寒武纪",
                },
                {
                    "指数代码": "886009.TI",
                    "指数名称": "先进封装",
                    "指数类型": "概念指数",
                    "股票代码": "688072.SH",
                    "股票名称": "拓荆科技",
                },
                {
                    "指数代码": "886009.TI",
                    "指数名称": "先进封装",
                    "指数类型": "概念指数",
                    "股票代码": "688256.SH",
                    "股票名称": "寒武纪",
                },
                {
                    "指数代码": "886112.TI",
                    "指数名称": "MLCC概念",
                    "指数类型": "概念指数",
                    "股票代码": "600183.SH",
                    "股票名称": "生益科技",
                },
                {
                    "指数代码": "886112.TI",
                    "指数名称": "MLCC概念",
                    "指数类型": "概念指数",
                    "股票代码": "688072.SH",
                    "股票名称": "拓荆科技",
                },
                {
                    "指数代码": "886112.TI",
                    "指数名称": "MLCC概念",
                    "指数类型": "概念指数",
                    "股票代码": "688256.SH",
                    "股票名称": "寒武纪",
                },
            ]
        ),
        ths / "概念板块成分汇总_同花顺.parquet",
    )
    quote_rows = pd.DataFrame(
        [
            {
                "指数代码": "886112.TI",
                "交易日期": "2026-07-30",
                "开盘点位": 1000.0,
                "最高点位": 1000.0,
                "最低点位": 1000.0,
                "收盘点位": 1000.0,
                "昨日收盘点": None,
                "平均价": None,
                "涨跌点位": None,
                "涨跌幅": None,
                "成交量": None,
                "换手率": None,
            },
            {
                "指数代码": "886112.TI",
                "交易日期": "2026-08-03",
                "开盘点位": 1006.261,
                "最高点位": 1033.239,
                "最低点位": 995.01,
                "收盘点位": 1018.25,
                "昨日收盘点": 1000.0,
                "平均价": 55.1862,
                "涨跌点位": 18.25,
                "涨跌幅": 1.825,
                "成交量": 6_971_085.6,
                "换手率": 8.457437,
            },
            {
                "指数代码": "886112.TI",
                "交易日期": "2026-08-14",
                "开盘点位": 1202.328,
                "最高点位": 1214.408,
                "最低点位": 1189.827,
                "收盘点位": 1212.77,
                "昨日收盘点": 1193.538,
                "平均价": 45.2069,
                "涨跌点位": 19.232,
                "涨跌幅": 1.6113,
                "成交量": 11_805_553.5,
                "换手率": 5.618843,
            },
        ]
    )
    _write_parquet_zip(
        {"886112.TI.parquet": quote_rows},
        ths / "板块指数行情_同花顺_parquet.zip",
    )
    return tmp_path


def test_config_reads_backend_aliases_and_paths(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.parquet"
    members_path = tmp_path / "members.parquet"
    quotes_path = tmp_path / "quotes.zip"
    config = CompatibilityConfig.from_env(
        {
            "FORBIDDENLAND_BACKEND": "remote",
            "FORBIDDENLAND_DATA_ROOT": str(tmp_path),
            "FORBIDDENLAND_ALLOW_REMOTE_FALLBACK": "yes",
            "FORBIDDENLAND_THS_CONCEPT_CATALOG_FILE": str(catalog_path),
            "FORBIDDENLAND_THS_CONCEPT_MEMBERS_FILE": str(members_path),
            "FORBIDDENLAND_THS_SECTOR_QUOTES_FILE": str(quotes_path),
        }
    )

    assert config.backend == "remote"
    assert config.data_root == tmp_path
    assert config.allow_remote_fallback is True
    assert config.resolved_daily_file() == tmp_path / "raw" / "stock_daily.parquet"
    assert config.resolved_ths_concept_catalog_file() == catalog_path
    assert config.resolved_ths_concept_members_file() == members_path
    assert config.resolved_ths_sector_quotes_file() == quotes_path


def test_config_defaults_to_remote_backend() -> None:
    config = CompatibilityConfig.from_env({})

    assert config.backend == "remote"
    assert config.allow_remote_fallback is False
    assert config.remote_retry_attempts == 3
    assert config.remote_retry_backoff_seconds == 0.5
    assert config.remote_request_timeout_seconds == 15.0
    assert config.remote_alternate_source is True
    assert config.resolved_ths_concept_catalog_file() == (
        Path("data") / "raw" / "行业概念板块" / "行业概念板块_同花顺.parquet"
    )


def test_config_reads_remote_recovery_settings() -> None:
    config = CompatibilityConfig.from_env(
        {
            "FORBIDDENLAND_REMOTE_RETRY_ATTEMPTS": "5",
            "FORBIDDENLAND_REMOTE_RETRY_BACKOFF_SECONDS": "0.25",
            "FORBIDDENLAND_REMOTE_REQUEST_TIMEOUT_SECONDS": "8",
            "FORBIDDENLAND_REMOTE_ALTERNATE_SOURCE": "no",
        }
    )

    assert config.remote_retry_attempts == 5
    assert config.remote_retry_backoff_seconds == 0.25
    assert config.remote_request_timeout_seconds == 8.0
    assert config.remote_alternate_source is False


@pytest.mark.parametrize(
    "field, value",
    [
        ("remote_retry_attempts", 0),
        ("remote_retry_backoff_seconds", -0.1),
        ("remote_request_timeout_seconds", 0),
    ],
)
def test_config_rejects_invalid_remote_recovery_settings(field: str, value: object) -> None:
    with pytest.raises(ConfigurationError, match=field):
        CompatibilityConfig(**{field: value})  # type: ignore[arg-type]


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


def test_local_ths_concept_names_use_a_share_ti_namespace(local_data: Path) -> None:
    api = AkShareCompat(CompatibilityConfig(backend="local", data_root=local_data))

    result = api.stock_board_concept_name_ths()

    assert list(result.columns) == CONCEPT_NAME_COLUMNS
    assert result.to_dict(orient="records") == [
        {"name": "阿里巴巴概念", "code": "885611.TI"},
        {"name": "先进封装", "code": "886009.TI"},
        {"name": "MLCC概念", "code": "886112.TI"},
    ]
    assert str(result["code"].dtype) == "string"


def test_local_ths_concept_index_accepts_name_or_ti_code(local_data: Path) -> None:
    api = AkShareCompat(CompatibilityConfig(backend="local", data_root=local_data))

    by_name = api.stock_board_concept_index_ths(
        symbol="MLCC概念",
        start_date="20260801",
        end_date="20260814",
    )
    by_code = api.stock_board_concept_index_ths(
        symbol="886112.TI",
        start_date="2026-08-01",
        end_date="2026-08-14",
    )

    assert list(by_name.columns) == CONCEPT_INDEX_COLUMNS
    pd.testing.assert_frame_equal(by_name, by_code)
    assert by_name["日期"].tolist() == [
        pd.Timestamp("2026-08-03").date(),
        pd.Timestamp("2026-08-14").date(),
    ]
    assert by_name["收盘价"].tolist() == [1018.25, 1212.77]
    assert by_name["成交额"].isna().all()


def test_local_ths_concept_index_validates_range_and_symbol(local_data: Path) -> None:
    api = AkShareCompat(CompatibilityConfig(backend="local", data_root=local_data))

    with pytest.raises(ValueError, match="start_date"):
        api.stock_board_concept_index_ths(
            symbol="MLCC概念", start_date="20260815", end_date="20260814"
        )
    with pytest.raises(LocalDataUnavailableError, match="local A-share 885/886 snapshot"):
        api.stock_board_concept_index_ths(symbol="不存在的概念")


def test_local_ths_concept_info_preserves_unavailable_fields(local_data: Path) -> None:
    api = AkShareCompat(CompatibilityConfig(backend="local", data_root=local_data))

    result = api.stock_board_concept_info_ths(symbol="MLCC概念")

    assert list(result.columns) == CONCEPT_INFO_COLUMNS
    values = result.set_index("项目")["值"]
    assert values["今开"] == "1202.33"
    assert values["昨收"] == "1193.54"
    assert values["最低"] == "1189.83"
    assert values["最高"] == "1214.41"
    assert values["板块涨幅"] == "1.61%"
    assert pd.isna(values["成交量(万手)"])
    assert pd.isna(values["涨幅排名"])
    assert pd.isna(values["涨跌家数"])
    assert pd.isna(values["资金净流入(亿)"])
    assert pd.isna(values["成交额(亿)"])


def test_local_ths_concept_summary_is_snapshot_approximation(local_data: Path) -> None:
    api = AkShareCompat(CompatibilityConfig(backend="local", data_root=local_data))

    result = api.stock_board_concept_summary_ths()

    assert list(result.columns) == CONCEPT_SUMMARY_COLUMNS
    assert result["概念名称"].tolist() == ["MLCC概念", "先进封装", "阿里巴巴概念"]
    assert result["日期"].tolist() == [
        pd.Timestamp("2026-07-31").date(),
        pd.Timestamp("2022-08-08").date(),
        pd.Timestamp("2015-02-03").date(),
    ]
    # The catalog declares two MLCC members, while the member fact table has three.
    assert result["成分股数量"].tolist() == [3, 2, 1]
    assert result["驱动事件"].isna().all()
    assert result["龙头股"].isna().all()


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


def test_local_ths_malformed_quote_schema_is_reported(local_data: Path) -> None:
    malformed_archive = local_data / "malformed-quotes.zip"
    _write_parquet_zip(
        {
            "886112.TI.parquet": pd.DataFrame(
                {"指数代码": ["886112.TI"], "交易日期": ["2026-08-14"]}
            )
        },
        malformed_archive,
    )
    config = CompatibilityConfig(
        backend="local",
        data_root=local_data,
        ths_sector_quotes_file=malformed_archive,
    )
    api = AkShareCompat(config)

    with pytest.raises(LocalDataError, match="missing required columns"):
        api.stock_board_concept_index_ths(symbol="MLCC概念")


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


def test_remote_tencent_history_interface_forwards_exact_arguments() -> None:
    calls: list[dict[str, object]] = []
    remote = ModuleType("fake_akshare")

    def stock_zh_a_hist_tx(**kwargs: object) -> str:
        calls.append(kwargs)
        return "remote-tencent-hist"

    remote.stock_zh_a_hist_tx = stock_zh_a_hist_tx  # type: ignore[attr-defined]
    api = AkShareCompat(CompatibilityConfig(backend="remote"), remote_module=remote)

    result = api.stock_zh_a_hist_tx(
        symbol="688256",
        start_date="20240101",
        end_date="20240131",
        adjust="qfq",
        timeout=8,
    )

    assert result == "remote-tencent-hist"
    assert calls == [
        {
            "symbol": "688256",
            "start_date": "20240101",
            "end_date": "20240131",
            "adjust": "qfq",
            "timeout": 8,
        }
    ]


def test_remote_ths_concept_interface_forwards_exact_arguments() -> None:
    calls: list[dict[str, object]] = []
    remote = ModuleType("fake_akshare")

    def stock_board_concept_index_ths(**kwargs: object) -> str:
        calls.append(kwargs)
        return "remote-concept-index"

    remote.stock_board_concept_index_ths = stock_board_concept_index_ths  # type: ignore[attr-defined]
    api = AkShareCompat(CompatibilityConfig(backend="remote"), remote_module=remote)

    result = api.stock_board_concept_index_ths(
        symbol="MLCC概念", start_date="20260801", end_date="20260814"
    )

    assert result == "remote-concept-index"
    assert calls == [{"symbol": "MLCC概念", "start_date": "20260801", "end_date": "20260814"}]


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


def test_hybrid_ths_endpoint_falls_back_when_local_concept_quote_is_absent(
    local_data: Path,
) -> None:
    remote = ModuleType("fake_akshare")
    remote.stock_board_concept_index_ths = lambda **_: "remote-concept"  # type: ignore[attr-defined]
    config = CompatibilityConfig(
        backend="hybrid",
        data_root=local_data,
        allow_remote_fallback=True,
    )
    api = AkShareCompat(config, remote_module=remote)

    assert api.stock_board_concept_index_ths(symbol="先进封装") == "remote-concept"


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
        concepts = module.stock_board_concept_name_ths()  # type: ignore[attr-defined]
        assert concepts["code"].tolist() == ["885611.TI", "886009.TI", "886112.TI"]
        with pytest.raises(UnsupportedEndpointError):
            module.stock_zh_a_spot_em()  # type: ignore[attr-defined]
        with pytest.raises(UnsupportedEndpointError):
            module.stock_zh_a_hist_tx(symbol="688256")  # type: ignore[attr-defined]
    finally:
        uninstall_backend(module=module)

    assert module.stock_zh_a_hist is original_hist  # type: ignore[attr-defined]
    assert module.stock_zh_a_spot_em is original_spot  # type: ignore[attr-defined]
    assert not hasattr(module, "stock_board_concept_name_ths")
    assert not hasattr(module, "stock_zh_a_hist_tx")


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
