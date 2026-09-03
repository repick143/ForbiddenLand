# Research, Indicators, Factors, and VSA

The calculation layer in `easy_tdx` is mostly pandas/NumPy code. It can operate on daily or
intraday bars, but a period named `20d` means 20 input rows unless the caller supplies daily data.
Always state the input frequency and the warmup window.

## Technical Indicators

The installed 1.30.3 registry contains 50 indicators:

`MACD`, `RSI`, `BOLL`, `BIAS`, `PSY`, `TRIX`, `DPO`, `MTM`, `ROC`, `EXPMA`, `BBI`, `DFMA`,
`KDJ`, `DMI`, `ATR`, `WR`, `CCI`, `CR`, `KTN`, `XSII`, `OBV`, `VR`, `EMV`, `MASS`, `MFI`,
`BRAR`, `ASI`, `ZHUOYAO`, `BIAS_SIGNAL`, `TAQ`, `SAR`, `VWAP`, `AROON`, `FK`, `SUPERTREND`,
`CHANDELIER`, `HMA`, `KAMA`, `ICHIMOKU`, `UOS`, `CMO`, `TSI`, `FISHER`, `SQUEEZE`, `CHOP`,
`BBP`, `BBW`, `AD`, `CMF`, and `EFI`.

Use the binary for ordinary requests:

```bash
easy-tdx indicator-list --table
easy-tdx indicator MACD,KDJ,RSI -m SH -c 600183 --period DAILY --count 30
easy-tdx indicator VWAP,OBV -m SH -c 600183 --period 5MIN --count 120
```

The Python calculation path is useful when the data is already normalized:

```python
from easy_tdx.indicator import compute_indicators

result = compute_indicators(
    bars,
    indicators=["MACD", "RSI"],
    params={"MACD": {"SHORT": 10, "LONG": 22, "M": 9}},
    tail=30,
)
```

Request at least 120-200 warmup bars for EMA-derived indicators. A value of zero is different
from an unavailable or not-yet-warmed value; retain NaN and explain it.

## Built-in Factors

`easy_tdx.factor.builtin.list_factors()` currently registers 19 factors:

| Category | Factors | Interpretation |
|---|---|---|
| Momentum | `momentum_20d`, `momentum_60d`, `reversal_5d` | Rolling returns; reversal is negative 5-row return |
| Quality | `sharpe_20d`, `max_drawdown_20d`, `win_rate_20d` | Rolling return statistics |
| Technical | `macd_hist_signal`, `rsi_14`, `boll_position` | Normalized indicator signals |
| Volatility | `volatility_20d`, `atr_14d`, `turnover_rate` | Dispersion, ATR, and an amount-ratio proxy |
| Volume | `obv_trend`, `vol_surge`, `amount_ma_ratio` | OBV slope and volume/amount ratios |
| Chanlun | `chanlun_bi_dir`, `chanlun_mmd` | Current pen direction and buy/sell-point code |
| Value | `pe_ratio`, `pb_ratio` | Placeholders unless financial inputs are wired in |

The factor engine supports single-stock and cross-sectional computation, forward returns,
preprocessing (winsorization/standardization/rank/fill/orthogonalization), and custom factor
registration:

```python
from easy_tdx.factor import Factor, FactorEngine, register_factor


@register_factor
class ThreeBarRange(Factor):
    name = "three_bar_range"
    category = "technical"
    description = "Three-bar high-low range"
    inputs = ("high", "low")

    def compute(self, df):
        return df["high"].rolling(3).max() - df["low"].rolling(3).min()


engine = FactorEngine()
single = engine.compute_single(bars, ["three_bar_range", "rsi_14"])
cross = engine.compute_cross_section(
    {"600183": bars, "688256": other_bars},
    ["momentum_20d", "rsi_14"],
    date=None,  # latest row for each symbol
)
```

For factor validity, calculate forward returns from the same time-aligned dataset, then inspect
IC, quantile returns, top-minus-bottom, turnover, and decay. The default Spearman IC path needs
SciPy (`easy-tdx[science]`); use Pearson only when that methodological change is intentional.

The documentation example is ahead of the installed API in two places. In 1.30.3 use:

```python
from easy_tdx.factor import FactorAnalyzer

report = FactorAnalyzer(
    clean,
    forward_returns,
    factor_col="momentum_20d",
    return_col="forward_5d",
).full_report()
print(report.ic_mean, report.ir)
```

Do not interpret `pe_ratio` or `pb_ratio` as real valuation data: their current implementation
returns all-NaN. `turnover_rate` is documented as a proxy (`amount / 20-row amount mean`), not
exchange turnover. Factor windows and `compute_forward_returns(period=5)` are row-based, so
intraday use requires an explicit bar-to-time conversion and overnight handling.

## TongdaXin Formula

The formula parser is a restricted tokenizer/parser, not Python `eval`. It accepts intermediate
assignments (`:=`), named outputs (`:`), Chinese identifiers, arithmetic/comparison/logic, and a
whitelist of backward-looking functions such as `MA`, `EMA`, `SMA`, `HHV`, `LLV`, `REF`, `SUM`,
`COUNT`, `CROSS`, `LONGCROSS`, `BARSLAST`, `MACD`, `KDJ`, `RSI`, `BOLL`, `CCI`, `ATR`, and `DMI`.

Example:

```bash
easy-tdx formula compute SH 600183 --formula '金叉: CROSS(MA(C,5), MA(C,20));' --count 240 --adjust QFQ
easy-tdx formula screen --symbols SH:600183,SZ:000001 --formula '买入: C > MA(C,20) AND V > MA(V,20);' --signal 买入
easy-tdx formula backtest SH 600183 --file strategy.tdx --auto-fees
```

Named boolean outputs become signal columns; numeric outputs are values for ranking or thresholds.
The supported dialect is a subset, so report parser errors with the formula position. Never run
untrusted formula text through a general-purpose evaluator.

## Chanlun

The `chanlun` command and module pipeline perform:

`K-line inclusion merge -> fractals -> pens (bi) -> centers (zs) -> segments -> buy/sell points -> divergence`.

```bash
easy-tdx chanlun SH 600183 --adjust QFQ --period DAILY --table
easy-tdx chanlun SH 600183 --period 30MIN --multi-level 5MIN
```

The two built-in Chanlun factors catch broad exceptions and can return a zero-filled series when
the analyser fails. Treat an all-zero result as a quality warning and rerun with a smaller sample
or the Python analyser before drawing a “no signal” conclusion.

## VSA Workflow

`easy_tdx` supplies data for VSA but does not ship a verified VSA strategy. Use the following
causal sequence:

1. Fetch daily or intraday OHLCV with a declared adjustment mode.
2. Derive spread (`high-low`), relative volume, close-location/value (CLV), and effort/result.
3. Compare each observation with a trailing baseline; do not use future bars in the feature.
4. Label a candidate (climactic volume, stopping volume, no-demand/no-supply, or test) with the
   raw evidence retained.
5. Require next-bar or next-session follow-through before treating it as confirmed.
6. If backtesting, execute on the next available bar and include fees, lot size, slippage, and
   suspension/limit rules.

For 5-minute VSA, normalize volume units and session boundaries first. A transaction record is not
automatically a full Level-2 order stream, and a missing minute must not be converted to zero.

## Interpretation Rules

- Indicators describe the selected bar series; they do not predict the next bar by themselves.
- A single-stock factor value is not an IC result. IC requires a date-aligned cross-section and a
  declared forward horizon.
- Avoid mixing adjusted prices with raw transaction prices in the same spread or CLV calculation.
- Preserve the exact input window and source in any saved analysis history.
- Report research output as educational analysis, not investment advice.
