"""Calculate a compact, reviewable technical-analysis record from normalized daily bars.

The module deliberately stays independent of pandas and charting code.  A provider supplies
normalized bars and provenance; this module calculates indicators and writes no files.  The
history runner is responsible for persistence and for loading the prior record used by
``build_review``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from math import isfinite
from statistics import fmean
from typing import Any

from forbiddenland.domain.analysis import (
    AnalysisPattern,
    AnalysisProvenance,
    AnalysisRecord,
    AnalysisReview,
    AnalysisSetup,
    AnalysisValidation,
)
from forbiddenland.domain.market import MarketAsset, MarketBar, MarketDataResult

ANALYSIS_VERSION = "technical-analysis-v1"
MINIMUM_REFERENCE_TRADES = 30
DEFAULT_PARAMETERS: dict[str, Any] = {
    "daily_sma_periods": [20, 50, 200],
    "weekly_sma_periods": [10, 20, 40],
    "rsi_period": 14,
    "atr_period": 14,
    "volume_window": 20,
    "structure_window": 20,
    "range_window": 60,
    "breakout_volume_ratio": 1.2,
    "risk_multiple": 1.5,
    "reward_multiple": 2.0,
}


def _finite(value: float | None) -> float | None:
    if value is None:
        return None
    result = float(value)
    return result if isfinite(result) else None


def _rounded(value: float | None, digits: int = 4) -> float | None:
    finite = _finite(value)
    return None if finite is None else round(finite, digits)


def _fmt(value: float | None, digits: int = 2) -> str:
    finite = _finite(value)
    if finite is None:
        return "缺失"
    return f"{finite:.{digits}f}"


def _mean(values: Sequence[float]) -> float | None:
    return _rounded(fmean(values)) if values else None


def _sma(values: Sequence[float], period: int) -> float | None:
    if len(values) < period:
        return None
    return _mean(values[-period:])


def _sma_slope(values: Sequence[float], period: int, lookback: int = 5) -> float | None:
    if len(values) < period + lookback:
        return None
    current = _sma(values, period)
    previous = _sma(values[:-lookback], period)
    if current is None or previous is None:
        return None
    return _rounded(current - previous)


def _percent_change(values: Sequence[float], periods: int) -> float | None:
    if len(values) <= periods:
        return None
    previous = values[-periods - 1]
    latest = values[-1]
    if previous == 0 or not isfinite(previous) or not isfinite(latest):
        return None
    return _rounded((latest / previous - 1.0) * 100.0, 2)


def _rsi(closes: Sequence[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    recent = changes[-period:]
    gains = [max(change, 0.0) for change in recent]
    losses = [max(-change, 0.0) for change in recent]
    average_gain = fmean(gains)
    average_loss = fmean(losses)
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    return _rounded(100.0 - 100.0 / (1.0 + average_gain / average_loss), 2)


def _atr(bars: Sequence[MarketBar], period: int = 14) -> float | None:
    if len(bars) < period:
        return None
    true_ranges: list[float] = []
    for index, bar in enumerate(bars):
        if index == 0:
            true_ranges.append(bar.high - bar.low)
            continue
        previous_close = bars[index - 1].close
        true_ranges.append(
            max(
                bar.high - bar.low,
                abs(bar.high - previous_close),
                abs(bar.low - previous_close),
            )
        )
    return _mean(true_ranges[-period:])


def _volume_ratio(bars: Sequence[MarketBar], window: int = 20) -> float | None:
    if not bars or bars[-1].volume is None:
        return None
    prior = [
        float(bar.volume)
        for bar in bars[-window - 1 : -1]
        if bar.volume is not None and isfinite(float(bar.volume)) and float(bar.volume) >= 0
    ]
    baseline = _mean(prior)
    if baseline is None or baseline <= 0:
        return None
    return _rounded(float(bars[-1].volume) / baseline, 3)


def _relative_position(bars: Sequence[MarketBar], window: int = 60) -> float | None:
    sample = bars[-window:]
    if not sample:
        return None
    low = min(bar.low for bar in sample)
    high = max(bar.high for bar in sample)
    if high <= low:
        return None
    return _rounded((sample[-1].close - low) / (high - low) * 100.0, 2)


def _drawdown(closes: Sequence[float]) -> float | None:
    if not closes:
        return None
    peak = max(closes)
    if peak == 0:
        return None
    return _rounded((closes[-1] / peak - 1.0) * 100.0, 2)


def _trend(closes: Sequence[float], periods: tuple[int, ...]) -> str:
    if not closes:
        return "insufficient_data"
    latest = closes[-1]
    short = _sma(closes, periods[0])
    medium = _sma(closes, periods[1]) if len(periods) > 1 else None
    long = _sma(closes, periods[2]) if len(periods) > 2 else None
    if short is not None and medium is not None:
        if latest > short > medium and (long is None or latest > long):
            return "uptrend"
        if latest < short < medium and (long is None or latest < long):
            return "downtrend"
    if short is not None and latest > short:
        return "recovering"
    if short is not None and latest < short:
        return "weakening"
    return "range_or_transition"


def _weekly_bars(bars: Sequence[MarketBar]) -> tuple[MarketBar, ...]:
    """Aggregate daily bars without inventing a non-trading-day observation."""

    grouped: dict[tuple[int, int], list[MarketBar]] = {}
    for bar in bars:
        iso = bar.date.isocalendar()
        grouped.setdefault((iso.year, iso.week), []).append(bar)
    weekly: list[MarketBar] = []
    for values in grouped.values():
        values = sorted(values, key=lambda item: item.date)
        volumes = [bar.volume for bar in values if bar.volume is not None]
        amounts = [bar.amount for bar in values if bar.amount is not None]
        weekly.append(
            MarketBar(
                symbol=values[-1].symbol,
                date=values[-1].date,
                open=values[0].open,
                high=max(bar.high for bar in values),
                low=min(bar.low for bar in values),
                close=values[-1].close,
                volume=sum(volumes) if len(volumes) == len(values) else None,
                amount=sum(amounts) if len(amounts) == len(values) else None,
                change=values[-1].close - values[0].open,
                change_percent=(values[-1].close / values[0].open - 1.0) * 100
                if values[0].open
                else None,
            )
        )
    return tuple(sorted(weekly, key=lambda item: item.date))


def normalize_bars(bars: Sequence[MarketBar]) -> tuple[MarketBar, ...]:
    """Validate and sort provider bars before any indicator is calculated."""

    if not bars:
        raise ValueError("technical analysis requires at least one market bar")
    normalized = sorted(bars, key=lambda item: item.date)
    seen: set[date] = set()
    for bar in normalized:
        if bar.date in seen:
            raise ValueError(f"duplicate market bar date for {bar.symbol}: {bar.date}")
        seen.add(bar.date)
        required = (bar.open, bar.high, bar.low, bar.close)
        if not all(isfinite(float(value)) for value in required):
            raise ValueError(f"non-finite OHLC value for {bar.symbol} on {bar.date}")
        if bar.high < max(bar.open, bar.close) or bar.low > min(bar.open, bar.close):
            raise ValueError(f"invalid OHLC ordering for {bar.symbol} on {bar.date}")
        if bar.high < bar.low:
            raise ValueError(f"negative spread for {bar.symbol} on {bar.date}")
        if bar.volume is not None and (not isfinite(float(bar.volume)) or bar.volume < 0):
            raise ValueError(f"invalid volume for {bar.symbol} on {bar.date}")
    return tuple(normalized)


def _levels(
    bars: Sequence[MarketBar],
    *,
    support_window: int,
    resistance_window: int,
    atr: float | None,
) -> dict[str, Any]:
    support_sample = bars[-support_window:]
    resistance_sample = bars[-resistance_window:]
    support = min((bar.low for bar in support_sample), default=None)
    resistance = max((bar.high for bar in resistance_sample), default=None)
    buffer = max((atr or 0.0) * 0.5, (bars[-1].close if bars else 0.0) * 0.005)
    return {
        "support": {
            "lower": _rounded(support),
            "upper": _rounded(support + buffer if support is not None else None),
        },
        "resistance": {
            "lower": _rounded(resistance - buffer if resistance is not None else None),
            "upper": _rounded(resistance),
        },
    }


def _breakout(
    bars: Sequence[MarketBar],
    *,
    window: int,
    volume_ratio: float | None,
) -> dict[str, Any]:
    if len(bars) <= window:
        return {
            "direction": "none",
            "status": "insufficient_data",
            "prior_high": None,
            "prior_low": None,
            "volume_ratio": volume_ratio,
        }
    prior = bars[-window - 1 : -1]
    prior_high = max(bar.high for bar in prior)
    prior_low = min(bar.low for bar in prior)
    close = bars[-1].close
    if close > prior_high:
        direction = "up"
    elif close < prior_low:
        direction = "down"
    else:
        direction = "none"
    if direction == "none":
        status = "none"
    elif volume_ratio is not None and volume_ratio >= DEFAULT_PARAMETERS["breakout_volume_ratio"]:
        status = "confirmed"
    else:
        status = "needs_volume_confirmation"
    return {
        "direction": direction,
        "status": status,
        "prior_high": _rounded(prior_high),
        "prior_low": _rounded(prior_low),
        "volume_ratio": volume_ratio,
    }


def _indicator_bundle(bars: Sequence[MarketBar]) -> tuple[dict[str, Any], dict[str, Any]]:
    closes = [bar.close for bar in bars]
    volumes = [bar.volume for bar in bars if bar.volume is not None]
    daily_atr = _atr(bars, DEFAULT_PARAMETERS["atr_period"])
    daily = {
        "latest_close": _rounded(closes[-1]),
        "return_5d_pct": _percent_change(closes, 5),
        "return_20d_pct": _percent_change(closes, 20),
        "sma20": _sma(closes, 20),
        "sma50": _sma(closes, 50),
        "sma200": _sma(closes, 200),
        "sma20_slope_5d": _sma_slope(closes, 20),
        "rsi14": _rsi(closes, DEFAULT_PARAMETERS["rsi_period"]),
        "atr14": daily_atr,
        "atr14_pct": _rounded(
            daily_atr / closes[-1] * 100 if daily_atr and closes[-1] else None, 2
        ),
        "volume_latest": _rounded(bars[-1].volume, 0),
        "volume_average20": _mean(
            [
                float(bar.volume)
                for bar in bars[-DEFAULT_PARAMETERS["volume_window"] :]
                if bar.volume is not None
            ]
        ),
        "volume_ratio20": _volume_ratio(bars, DEFAULT_PARAMETERS["volume_window"]),
        "relative_position60_pct": _relative_position(bars, DEFAULT_PARAMETERS["range_window"]),
        "drawdown_from_window_high_pct": _drawdown(closes[-DEFAULT_PARAMETERS["range_window"] :]),
        "volume_observations": len(volumes),
    }
    weekly_bars = _weekly_bars(bars)
    weekly_closes = [bar.close for bar in weekly_bars]
    weekly_atr = _atr(weekly_bars, DEFAULT_PARAMETERS["atr_period"])
    weekly = {
        "latest_close": _rounded(weekly_closes[-1]) if weekly_closes else None,
        "return_4w_pct": _percent_change(weekly_closes, 4),
        "return_12w_pct": _percent_change(weekly_closes, 12),
        "sma10": _sma(weekly_closes, 10),
        "sma20": _sma(weekly_closes, 20),
        "sma40": _sma(weekly_closes, 40),
        "sma20_slope_4w": _sma_slope(weekly_closes, 20, lookback=4),
        "rsi14": _rsi(weekly_closes, DEFAULT_PARAMETERS["rsi_period"]),
        "atr14": weekly_atr,
        "relative_position20_pct": _relative_position(weekly_bars, 20),
        "bar_count": len(weekly_bars),
    }
    return {"daily": daily, "weekly": weekly}, {
        "daily": _trend(closes, (20, 50, 200)),
        "weekly": _trend(weekly_closes, (10, 20, 40)),
    }


def _patterns(
    *,
    trends: dict[str, str],
    daily: dict[str, Any],
    weekly: dict[str, Any],
    breakout: dict[str, Any],
) -> tuple[AnalysisPattern, ...]:
    volume_ratio = daily.get("volume_ratio20")
    if trends["weekly"] == trends["daily"] and trends["weekly"] in {"uptrend", "downtrend"}:
        trend_status = "aligned"
        trend_confidence = "medium"
    else:
        trend_status = "mixed"
        trend_confidence = "low"
    trend_evidence = (
        f"周线={trends['weekly']}，日线={trends['daily']}；"
        f"周线收盘 {_fmt(weekly.get('latest_close'))}，日线收盘 {_fmt(daily.get('latest_close'))}。"
    )
    patterns: list[AnalysisPattern] = [
        AnalysisPattern(
            name="多周期趋势",
            timeframe="weekly+daily",
            status=trend_status,
            evidence=trend_evidence,
            volume_confirmation="not_applicable",
            confidence=trend_confidence,
        )
    ]
    direction = breakout["direction"]
    if direction == "up":
        status = "confirmed" if breakout["status"] == "confirmed" else "needs_confirmation"
        evidence = (
            f"收盘 {_fmt(daily.get('latest_close'))} 高于前{DEFAULT_PARAMETERS['structure_window']}日高点 "
            f"{_fmt(breakout.get('prior_high'))}；量比 {_fmt(volume_ratio, 3)}。"
        )
        patterns.append(
            AnalysisPattern(
                name="向上结构突破",
                timeframe="daily",
                status=status,
                evidence=evidence,
                volume_confirmation=("confirmed" if status == "confirmed" else "weak_or_missing"),
                confidence=("medium" if status == "confirmed" else "low"),
            )
        )
    elif direction == "down":
        patterns.append(
            AnalysisPattern(
                name="向下结构突破",
                timeframe="daily",
                status="confirmed" if breakout["status"] == "confirmed" else "needs_confirmation",
                evidence=(
                    f"收盘 {_fmt(daily.get('latest_close'))} 低于前{DEFAULT_PARAMETERS['structure_window']}日低点 "
                    f"{_fmt(breakout.get('prior_low'))}；量比 {_fmt(volume_ratio, 3)}。"
                ),
                volume_confirmation=(
                    "confirmed" if breakout["status"] == "confirmed" else "weak_or_missing"
                ),
                confidence="medium" if breakout["status"] == "confirmed" else "low",
            )
        )
    else:
        patterns.append(
            AnalysisPattern(
                name="市场结构",
                timeframe="daily",
                status="range_or_transition",
                evidence=(
                    f"收盘仍在前{DEFAULT_PARAMETERS['structure_window']}日区间内；"
                    f"相对{DEFAULT_PARAMETERS['range_window']}日区间位置 "
                    f"{_fmt(daily.get('relative_position60_pct'))}%。"
                ),
                volume_confirmation="not_triggered",
                confidence="low",
            )
        )
    rsi = daily.get("rsi14")
    if rsi is None:
        rsi_status = "insufficient_data"
    elif rsi >= 70:
        rsi_status = "overbought"
    elif rsi <= 30:
        rsi_status = "oversold"
    else:
        rsi_status = "neutral"
    patterns.append(
        AnalysisPattern(
            name="RSI 动能",
            timeframe="daily",
            status=rsi_status,
            evidence=f"14日 RSI={_fmt(rsi)}；RSI 是滞后动量指标，不能单独作为入场依据。",
            volume_confirmation="not_applicable",
            confidence="low" if rsi_status in {"overbought", "oversold"} else "medium",
        )
    )
    return tuple(patterns)


def _setup(
    *,
    bars: Sequence[MarketBar],
    trends: dict[str, str],
    daily: dict[str, Any],
    levels: dict[str, Any],
    breakout: dict[str, Any],
) -> AnalysisSetup:
    close = bars[-1].close
    atr = daily.get("atr14") or max(close * 0.03, 0.01)
    resistance = levels["resistance"].get("upper")
    support = levels["support"].get("lower")
    trigger = close if breakout["direction"] == "up" else resistance
    if trigger is None or trigger <= close and breakout["direction"] != "up":
        trigger = close + atr
    trigger = float(trigger)
    stop = max(0.01, close - float(atr) * DEFAULT_PARAMETERS["risk_multiple"])
    if support is not None and support < trigger:
        stop = max(stop, min(float(support), trigger - 0.01))
    if stop >= trigger:
        stop = max(0.01, trigger - max(float(atr), trigger * 0.03))
    risk = max(trigger - stop, 0.01)
    target = trigger + risk * DEFAULT_PARAMETERS["reward_multiple"]
    if breakout["direction"] == "up":
        status = "breakout_wait_for_retest"
    elif trends["weekly"] == "downtrend" or trends["daily"] == "downtrend":
        status = "long_setup_suspended"
    else:
        status = "wait_for_volume_breakout"
    return AnalysisSetup(
        direction="long_conditional_observation",
        status=status,
        trigger_price=_rounded(trigger),
        entry_price=None,
        stop_loss=_rounded(stop),
        target_price=_rounded(target),
        risk_reward=_rounded((target - trigger) / risk, 2),
        invalidation=(
            f"日线收盘跌破止损位 {_fmt(stop)}，或突破后重新收回前{DEFAULT_PARAMETERS['structure_window']}日区间；"
            "盘中瞬时触及不单独视为确认。"
        ),
        risk_note="条件观察位仅用于复盘，不是成交指令；日线数据不能确认盘中触发顺序。",
    )


def _review(
    previous: AnalysisRecord | None,
    *,
    bars: Sequence[MarketBar],
    analysis_date: date,
    as_of_date: date,
    current_stance: str,
) -> AnalysisReview:
    if previous is None:
        return AnalysisReview(
            status="no_prior_analysis",
            previous_analysis_date=None,
            previous_stance=None,
            period_start=None,
            period_end=None,
            outcome="not_applicable",
            thesis_status="not_available",
            checks=(),
            summary="这是该标的的首份分析记录，暂无更早记录可复盘。",
        )
    interval = [
        bar
        for bar in bars
        if previous.as_of_date < bar.date <= as_of_date and bar.date <= analysis_date
    ]
    if not interval:
        return AnalysisReview(
            status="reviewed",
            previous_analysis_date=previous.analysis_date,
            previous_stance=previous.stance,
            period_start=None,
            period_end=None,
            outcome="no_new_bars",
            thesis_status="pending",
            checks=("上一份记录之后没有新的日线观察。",),
            summary="已找到上一份分析，但当前数据窗口没有覆盖其后的新交易日。",
        )
    lows = [bar.low for bar in interval]
    highs = [bar.high for bar in interval]
    closes = [bar.close for bar in interval]
    setup = previous.setup
    stop_touched = setup.stop_loss is not None and min(lows) <= setup.stop_loss
    target_touched = setup.target_price is not None and max(highs) >= setup.target_price
    trigger_touched = setup.trigger_price is not None and max(highs) >= setup.trigger_price
    if stop_touched and target_touched:
        outcome = "both_levels_touched_order_unknown"
        thesis_status = "ambiguous"
    elif stop_touched:
        outcome = "stop_loss_touched"
        thesis_status = "invalidated"
    elif target_touched:
        outcome = "target_touched"
        thesis_status = "confirmed"
    elif trigger_touched:
        outcome = "trigger_touched_pending"
        thesis_status = "pending"
    else:
        outcome = "not_triggered"
        thesis_status = "pending"
    checks = (
        (
            f"复盘区间 {interval[0].date.isoformat()} 至 {interval[-1].date.isoformat()}，"
            f"区间收盘变化 {_fmt((closes[-1] / closes[0] - 1) * 100, 2)}%。"
        ),
        (
            f"区间最高 {_fmt(max(highs))}，上一份目标 {_fmt(setup.target_price)}；"
            f"区间最低 {_fmt(min(lows))}，上一份止损 {_fmt(setup.stop_loss)}。"
        ),
        f"上一份立场 {previous.stance} -> 当前立场 {current_stance}。",
    )
    summary = (
        f"复盘上一份 {previous.analysis_date.isoformat()} 记录：{outcome}；"
        f"当前收盘 {_fmt(closes[-1])}，结论状态为 {thesis_status}。"
    )
    return AnalysisReview(
        status="reviewed",
        previous_analysis_date=previous.analysis_date,
        previous_stance=previous.stance,
        period_start=interval[0].date,
        period_end=interval[-1].date,
        outcome=outcome,
        thesis_status=thesis_status,
        checks=checks,
        summary=summary,
    )


def analyze_market_result(
    result: MarketDataResult,
    *,
    asset: MarketAsset,
    analysis_date: date,
    previous: AnalysisRecord | None = None,
    created_at_utc: datetime | None = None,
) -> AnalysisRecord:
    """Build one reproducible record from a provider result and optional prior record."""

    if asset.asset_type != "stock":
        raise ValueError("technical analysis currently supports stock assets only")
    bars = tuple(bar for bar in normalize_bars(result.bars) if bar.date <= analysis_date)
    if not bars:
        raise ValueError(f"no market bars for {asset.code} on or before {analysis_date}")
    as_of_date = bars[-1].date
    indicators, trends = _indicator_bundle(bars)
    daily = indicators["daily"]
    weekly = indicators["weekly"]
    levels = _levels(
        bars,
        support_window=DEFAULT_PARAMETERS["range_window"],
        resistance_window=DEFAULT_PARAMETERS["structure_window"],
        atr=daily.get("atr14"),
    )
    breakout = _breakout(
        bars,
        window=DEFAULT_PARAMETERS["structure_window"],
        volume_ratio=daily.get("volume_ratio20"),
    )
    structure = {
        "trend": trends,
        **levels,
        "breakout": breakout,
        "weekly_context": {
            "sma_alignment": trends["weekly"],
            "relative_position20_pct": weekly.get("relative_position20_pct"),
        },
    }
    patterns = _patterns(trends=trends, daily=daily, weekly=weekly, breakout=breakout)
    rsi = daily.get("rsi14")
    if breakout["direction"] == "up" and breakout["status"] == "confirmed":
        stance = "bullish"
        headline = f"{asset.name}：放量突破，等待回踩确认"
    elif trends["weekly"] == "downtrend" and trends["daily"] in {"downtrend", "weakening"}:
        stance = "bearish"
        headline = f"{asset.name}：周日线偏弱，暂不追随多头"
    elif rsi is not None and rsi >= 75:
        stance = "neutral"
        headline = f"{asset.name}：趋势偏强但动能过热，观察回撤"
    else:
        stance = "neutral"
        headline = f"{asset.name}：结构未完成，等待量价确认"
    setup = _setup(bars=bars, trends=trends, daily=daily, levels=levels, breakout=breakout)
    review = _review(
        previous,
        bars=bars,
        analysis_date=analysis_date,
        as_of_date=as_of_date,
        current_stance=stance,
    )
    return AnalysisRecord(
        schema_version=1,
        analysis_version=ANALYSIS_VERSION,
        analysis_id=f"{asset.code}/{analysis_date.isoformat()}",
        analysis_date=analysis_date,
        as_of_date=as_of_date,
        asset=asset,
        headline=headline,
        stance=stance,  # type: ignore[arg-type]
        summary=(
            f"截至 {as_of_date.isoformat()}，日线收盘 {_fmt(daily.get('latest_close'))}，"
            f"日线趋势 {trends['daily']}、周线趋势 {trends['weekly']}；"
            f"20日涨跌幅 {_fmt(daily.get('return_20d_pct'))}%、RSI14 {_fmt(rsi)}、"
            f"量比 {_fmt(daily.get('volume_ratio20'), 3)}。"
            "形态只作为概率假设，需用后续收盘和成交量确认。"
        ),
        latest_close=_rounded(bars[-1].close),
        indicators=indicators,
        structure=structure,
        patterns=patterns,
        setup=setup,
        review=review,
        provenance=AnalysisProvenance(
            source=result.source,
            backend=result.backend,
            storage=result.storage,
            start_date=result.query.start_date,
            end_date=result.query.end_date,
            adjust=result.query.adjust,
            retrieved_at_utc=result.retrieved_at_utc,
            cache_hit=result.cache_hit,
            frequency="daily",
            bar_count=len(bars),
        ),
        validation=AnalysisValidation(
            sample_size_bars=len(bars),
            backtest_trade_count=0,
            minimum_reference_trades=MINIMUM_REFERENCE_TRADES,
            sample_sufficient=False,
            out_of_sample=False,
            backtest_available=False,
            warnings=(
                "技术指标是描述性观察，尚未在该标的上完成含交易成本的回测。",
                "日线数据无法确认盘中触发顺序，突破必须等待收盘和成交量确认。",
                "样本数量不能替代至少 30 笔交易的统计验证，当前记录不代表存在可交易优势。",
                "研究输出仅供复盘，不构成投资建议。",
            ),
        ),
        parameters=dict(DEFAULT_PARAMETERS),
        notes=(
            "数据窗口和来源写入 provenance；远端备用端点或缓存命中不会被隐藏。",
            "均线、RSI、ATR 等指标存在滞后，不能单独预测未来价格。",
        ),
        created_at_utc=created_at_utc or datetime.now(UTC),
    )


__all__ = ["ANALYSIS_VERSION", "DEFAULT_PARAMETERS", "analyze_market_result", "normalize_bars"]
