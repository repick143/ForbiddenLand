import type {
  AssetType,
  HealthResponse,
  MarketAsset,
  MarketAssetListResponse,
  MarketBarsResponse,
  SecurityListResponse,
} from "../types";

async function request<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
    signal,
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: string };
      detail = body.detail ?? detail;
    } catch {
      // Keep the HTTP status when the server did not return JSON.
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export function getHealth(): Promise<HealthResponse> {
  return request<HealthResponse>("/api/v1/health");
}

export function getSecurities(): Promise<SecurityListResponse> {
  return request<SecurityListResponse>("/api/v1/market/securities");
}

export function getAssets(
  assetType: AssetType,
  query = "",
  signal?: AbortSignal,
): Promise<MarketAssetListResponse> {
  const search = new URLSearchParams({
    asset_type: assetType,
    query,
    limit: "50",
  });
  return request<MarketAssetListResponse>(`/api/v1/market/assets?${search.toString()}`, signal);
}

export function getMarketBars(
  asset: Pick<MarketAsset, "asset_type" | "code">,
  startDate: string,
  endDate: string,
  adjust: "" | "qfq" | "hfq",
  signal?: AbortSignal,
): Promise<MarketBarsResponse> {
  const query = new URLSearchParams({
    asset_type: asset.asset_type,
    symbol: asset.code,
    start_date: startDate,
    end_date: endDate,
    adjust: asset.asset_type === "stock" ? adjust : "",
  });
  return request<MarketBarsResponse>(`/api/v1/market/bars?${query.toString()}`, signal);
}
