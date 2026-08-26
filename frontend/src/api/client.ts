import type {
  HealthResponse,
  MarketBarsResponse,
  SecurityListResponse,
} from "../types";

async function request<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
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

export function getMarketBars(
  symbol: string,
  startDate: string,
  endDate: string,
  adjust: "" | "qfq" | "hfq",
): Promise<MarketBarsResponse> {
  const query = new URLSearchParams({
    symbol,
    start_date: startDate,
    end_date: endDate,
    adjust,
  });
  return request<MarketBarsResponse>(`/api/v1/market/bars?${query.toString()}`);
}
