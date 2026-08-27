export interface HealthResponse {
  status: "ok";
  service: string;
  version: string;
  backend: string;
  data_policy: string;
}

export interface Security {
  code: string;
  name: string;
}

export interface SecurityListResponse {
  items: Security[];
}

export type AssetType = "stock" | "index" | "concept";

export interface MarketAsset {
  asset_type: AssetType;
  code: string;
  name: string;
}

export interface MarketAssetListResponse {
  items: MarketAsset[];
}

export interface MarketBar {
  symbol: string;
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  amount: number | null;
  change: number | null;
  change_percent: number | null;
  turnover_rate: number | null;
}

export interface MarketBarsResponse {
  asset_type: AssetType;
  symbol: string;
  bars: MarketBar[];
  summary: {
    bar_count: number;
    first_date: string;
    latest_date: string;
    latest_close: number;
    period_change_percent: number | null;
    max_close: number;
    min_close: number;
  };
  provenance: {
    source: string;
    backend: string;
    storage: string;
    start_date: string;
    end_date: string;
    adjust: "" | "qfq" | "hfq";
    retrieved_at_utc: string;
    local_snapshot_review_required: boolean;
    cache_hit: boolean;
  };
}
