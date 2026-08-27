import { useEffect, useState } from "react";
import {
  AlertCircle,
  Expand,
  Landmark,
  Layers3,
  LineChart,
  LoaderCircle,
  X,
} from "lucide-react";

import { getMarketBars } from "../api/client";
import type { MarketAsset, MarketBarsResponse } from "../types";
import { MarketChart } from "./MarketChart";

interface AssetCardProps {
  asset: MarketAsset;
  startDate: string;
  endDate: string;
  adjust: "" | "qfq" | "hfq";
  refreshToken: number;
  onOpen: (asset: MarketAsset, market: MarketBarsResponse) => void;
  onRemove: (asset: MarketAsset) => void;
}

const TYPE_LABEL = { stock: "个股", index: "指数", concept: "概念" } as const;

function AssetIcon({ asset }: { asset: MarketAsset }) {
  if (asset.asset_type === "index") return <Landmark size={15} />;
  if (asset.asset_type === "concept") return <Layers3 size={15} />;
  return <LineChart size={15} />;
}

function formatNumber(value: number | null, digits = 2): string {
  return value === null
    ? "--"
    : value.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

export function AssetCard({
  asset,
  startDate,
  endDate,
  adjust,
  refreshToken,
  onOpen,
  onRemove,
}: AssetCardProps) {
  const [market, setMarket] = useState<MarketBarsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getMarketBars(asset, startDate, endDate, adjust, controller.signal)
      .then(setMarket)
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setMarket(null);
        setError(reason instanceof Error ? reason.message : "行情请求失败");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [adjust, asset, endDate, refreshToken, startDate]);

  const change = market?.summary.period_change_percent ?? null;
  const positive = change === null || change >= 0;

  return (
    <article className="asset-card">
      <header className="asset-card-header">
        <div className="asset-identity">
          <span className={`asset-type asset-type-${asset.asset_type}`}>
            <AssetIcon asset={asset} /> {TYPE_LABEL[asset.asset_type]}
          </span>
          <div>
            <h3>{asset.name}</h3>
            <span className="asset-code">{asset.code}</span>
          </div>
        </div>
        <div className="icon-actions">
          <button
            type="button"
            className="icon-button"
            title="展开行情"
            aria-label={`展开 ${asset.name} 行情`}
            disabled={!market}
            onClick={() => market && onOpen(asset, market)}
          >
            <Expand size={16} />
          </button>
          <button
            type="button"
            className="icon-button"
            title="移出分组"
            aria-label={`移出 ${asset.name}`}
            onClick={() => onRemove(asset)}
          >
            <X size={16} />
          </button>
        </div>
      </header>

      {loading ? (
        <div className="asset-loading" aria-label="正在读取行情">
          <LoaderCircle size={20} className="spin" />
        </div>
      ) : error ? (
        <div className="asset-error" role="alert">
          <AlertCircle size={18} />
          <span>{error}</span>
        </div>
      ) : market ? (
        <>
          <div className="asset-quote-row">
            <strong>{formatNumber(market.summary.latest_close)}</strong>
            <span className={positive ? "quote-up" : "quote-down"}>
              {change === null ? "--" : `${positive ? "+" : ""}${formatNumber(change)}%`}
            </span>
            <small>{market.summary.latest_date}</small>
          </div>
          <MarketChart bars={market.bars} />
          <footer className="asset-card-footer">
            <span>日线蜡烛图 · {market.summary.bar_count} 个交易日</span>
            <span>{market.provenance.backend}</span>
          </footer>
        </>
      ) : null}
    </article>
  );
}
