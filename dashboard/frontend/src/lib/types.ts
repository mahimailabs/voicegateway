export interface StatusResponse {
  providers: Record<string, { configured: boolean; type: string }>;
  models: Record<string, { modality: string; provider: string }>;
  fallbacks: Record<string, string[]>;
}

export interface OverviewResponse {
  total_requests: number;
  total_cost_today: number;
  total_cost_all: number;
  active_models: number;
  providers_configured: number;
}

export interface CostsResponse {
  period: string;
  project: string | null;
  total: number;
  by_provider: Record<string, { cost: number; requests: number }>;
  by_model: Record<string, { cost: number; requests: number }>;
  by_project: Record<string, { cost: number; requests: number }>;
}

export interface PercentileBucket {
  p50: number | null;
  p95: number | null;
  p99: number | null;
  // extra keys tolerated for forward-compat if config.latency.percentiles changes
  [key: string]: number | null | undefined;
}

export interface LatencyStats {
  avg_ttfb_ms: number;
  avg_latency_ms: number;
  request_count: number;
  ttfb_percentiles: PercentileBucket;
  latency_percentiles: PercentileBucket;
}

export type LatencyResponse = Record<string, LatencyStats>;

export interface LogRecord {
  id: string;
  timestamp: number;
  modality: string;
  model_id: string;
  provider: string;
  project: string;
  cost_usd: number;
  ttfb_ms: number | null;
  total_latency_ms: number | null;
  status: string;
}
