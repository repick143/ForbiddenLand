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

export type AnalysisStance = "bullish" | "neutral" | "bearish";

export interface AnalysisPattern {
  name: string;
  timeframe: string;
  status: string;
  evidence: string;
  volume_confirmation: string;
  confidence: string | null;
}

export interface AnalysisSetup {
  direction: string;
  status: string;
  trigger_price: number | null;
  entry_price: number | null;
  stop_loss: number | null;
  target_price: number | null;
  risk_reward: number | null;
  invalidation: string;
  risk_note: string;
}

export interface AnalysisReview {
  status: string;
  previous_analysis_date: string | null;
  previous_stance: string | null;
  period_start: string | null;
  period_end: string | null;
  outcome: string;
  thesis_status: string;
  checks: string[];
  summary: string;
}

export interface AnalysisProvenance {
  source: string;
  backend: string;
  storage: string;
  start_date: string;
  end_date: string;
  adjust: string;
  retrieved_at_utc: string | null;
  cache_hit: boolean;
  frequency: string;
  bar_count: number;
}

export interface AnalysisValidation {
  sample_size_bars: number;
  backtest_trade_count: number;
  minimum_reference_trades: number;
  sample_sufficient: boolean;
  out_of_sample: boolean;
  backtest_available: boolean;
  warnings: string[];
}

export interface AnalysisHistorySummary {
  analysis_id: string;
  analysis_date: string;
  as_of_date: string;
  asset: MarketAsset;
  headline: string;
  stance: AnalysisStance;
  summary: string;
  review_status: string;
  previous_analysis_date: string | null;
  latest_close: number | null;
  sample_size_bars: number;
  provenance_source: string;
  backend: string;
  cache_hit: boolean;
}

export interface AnalysisHistoryListResponse {
  items: AnalysisHistorySummary[];
  total: number;
  warnings: string[];
}

export interface AnalysisRecord {
  schema_version: number;
  analysis_version: string;
  analysis_id: string;
  analysis_date: string;
  as_of_date: string;
  asset: MarketAsset;
  headline: string;
  stance: AnalysisStance;
  summary: string;
  latest_close: number | null;
  indicators: Record<string, unknown>;
  structure: Record<string, unknown>;
  patterns: AnalysisPattern[];
  setup: AnalysisSetup;
  review: AnalysisReview;
  provenance: AnalysisProvenance;
  validation: AnalysisValidation;
  parameters: Record<string, unknown>;
  notes: string[];
  created_at_utc: string | null;
}
