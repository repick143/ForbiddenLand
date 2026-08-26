import { useEffect } from "react";
import { X } from "lucide-react";

import type { MarketAsset, MarketBarsResponse } from "../types";
import { MarketChart } from "./MarketChart";

interface AssetDetailDialogProps {
  asset: MarketAsset;
  market: MarketBarsResponse;
  onClose: () => void;
}

function formatNumber(value: number | null, digits = 2): string {
  return value === null
    ? "--"
    : value.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

export function AssetDetailDialog({ asset, market, onClose }: AssetDetailDialogProps) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const change = market.summary.period_change_percent;
  const positive = change === null || change >= 0;

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="asset-detail"
        role="dialog"
        aria-modal="true"
        aria-labelledby="asset-detail-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="modal-header detail-header">
          <div>
            <p className="eyebrow">{asset.asset_type.toUpperCase()} / {asset.code}</p>
            <h2 id="asset-detail-title">{asset.name}</h2>
          </div>
          <div className="detail-quote">
            <strong>{formatNumber(market.summary.latest_close)}</strong>
            <span className={positive ? "quote-up" : "quote-down"}>
              {change === null ? "--" : `${positive ? "+" : ""}${formatNumber(change)}%`}
            </span>
          </div>
          <button type="button" className="icon-button" title="关闭" aria-label="关闭" onClick={onClose}>
            <X size={19} />
          </button>
        </header>

        <MarketChart bars={market.bars} mode="detail" positive={positive} />

        <div className="detail-metrics">
          <div><span>区间高点</span><strong>{formatNumber(market.summary.max_close)}</strong></div>
          <div><span>区间低点</span><strong>{formatNumber(market.summary.min_close)}</strong></div>
          <div><span>交易日</span><strong>{market.summary.bar_count}</strong></div>
          <div><span>复权</span><strong>{market.provenance.adjust || "不复权"}</strong></div>
        </div>

        <div className="detail-table-scroll">
          <table>
            <thead>
              <tr><th>日期</th><th>开盘</th><th>最高</th><th>最低</th><th>收盘</th><th>成交量</th></tr>
            </thead>
            <tbody>
              {market.bars.slice(-8).reverse().map((bar) => (
                <tr key={bar.date}>
                  <td>{bar.date}</td>
                  <td>{formatNumber(bar.open)}</td>
                  <td>{formatNumber(bar.high)}</td>
                  <td>{formatNumber(bar.low)}</td>
                  <td className="close-cell">{formatNumber(bar.close)}</td>
                  <td>{formatNumber(bar.volume, 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
