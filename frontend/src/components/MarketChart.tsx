import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  createChart,
} from "lightweight-charts";

import type { MarketBar } from "../types";

interface MarketChartProps {
  bars: MarketBar[];
  mode?: "compact" | "detail";
  positive?: boolean;
}

export function MarketChart({ bars, mode = "compact", positive = true }: MarketChartProps) {
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

    if (mode === "detail") {
      const priceSeries = chart.addSeries(CandlestickSeries, {
        upColor: "#d84a3a",
        downColor: "#16805e",
        borderUpColor: "#d84a3a",
        borderDownColor: "#16805e",
        wickUpColor: "#d84a3a",
        wickDownColor: "#16805e",
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
          color: bar.close >= bar.open ? "rgb(216 74 58 / 42%)" : "rgb(22 128 94 / 42%)",
        })),
      );
    } else {
      const lineSeries = chart.addSeries(LineSeries, {
        color: positive ? "#d84a3a" : "#16805e",
        lineWidth: 2,
        priceLineVisible: false,
        crosshairMarkerRadius: 4,
      });
      lineSeries.setData(bars.map((bar) => ({ time: bar.date, value: bar.close })));
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
  }, [bars, mode, positive]);

  return <div ref={containerRef} className={`market-chart market-chart-${mode}`} />;
}
