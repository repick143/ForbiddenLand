import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  createChart,
} from "lightweight-charts";

import type { MarketBar } from "../types";

interface MarketChartProps {
  bars: MarketBar[];
  mode?: "compact" | "detail";
}

const CANDLE_UP_COLOR = "#d84a3a";
const CANDLE_DOWN_COLOR = "#16805e";

export function MarketChart({ bars, mode = "compact" }: MarketChartProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || bars.length === 0) return undefined;

    const chart = createChart(container, {
      width: Math.max(container.clientWidth, 240),
      height: container.clientHeight,
      layout: {
        background: { type: ColorType.Solid, color: "#ffffff" },
        textColor: "#758186",
        fontFamily: '"SF Pro Display", "PingFang SC", "Microsoft YaHei", sans-serif',
        fontSize: mode === "compact" ? 10 : 11,
        attributionLogo: true,
      },
      grid: {
        vertLines: { color: mode === "compact" ? "#ffffff" : "#edf0ed" },
        horzLines: { color: mode === "compact" ? "#f1f3f1" : "#edf0ed" },
      },
      crosshair: { mode: CrosshairMode.Magnet },
      rightPriceScale: {
        borderVisible: false,
        scaleMargins: mode === "detail" ? { top: 0.08, bottom: 0.28 } : { top: 0.12, bottom: 0.12 },
      },
      timeScale: {
        borderVisible: false,
        rightOffset: 1,
        barSpacing: mode === "compact" ? 7 : 9,
        minBarSpacing: 2,
        timeVisible: false,
      },
      handleScroll: mode === "detail",
      handleScale: mode === "detail",
    });

    const priceSeries = chart.addSeries(CandlestickSeries, {
      upColor: CANDLE_UP_COLOR,
      downColor: CANDLE_DOWN_COLOR,
      borderUpColor: CANDLE_UP_COLOR,
      borderDownColor: CANDLE_DOWN_COLOR,
      wickUpColor: CANDLE_UP_COLOR,
      wickDownColor: CANDLE_DOWN_COLOR,
      priceLineVisible: false,
    });
    priceSeries.setData(
      bars.map((bar) => ({
        time: bar.date,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      })),
    );

    if (mode === "detail") {
      const volumeSeries = chart.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
        priceLineVisible: false,
        lastValueVisible: false,
      });
      chart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.78, bottom: 0 },
      });
      volumeSeries.setData(
        bars.filter((bar) => bar.volume !== null).map((bar) => ({
          time: bar.date,
          value: bar.volume ?? 0,
          color:
            bar.close >= bar.open
              ? "rgb(216 74 58 / 42%)"
              : "rgb(22 128 94 / 42%)",
        })),
      );
    }

    chart.timeScale().fitContent();
    const resizeObserver = new ResizeObserver(([entry]) => {
      chart.applyOptions({
        width: Math.max(Math.floor(entry.contentRect.width), 240),
        height: Math.max(Math.floor(entry.contentRect.height), 180),
      });
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
    };
  }, [bars, mode]);

  return (
    <div
      ref={containerRef}
      className={`market-chart market-chart-${mode}`}
      role="img"
      aria-label="日线蜡烛图"
    />
  );
}
