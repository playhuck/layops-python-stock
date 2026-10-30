export interface StepStatus { name: string; ok: boolean; error?: string }
export interface Status { running: boolean; last_run: string | null; trigger: string | null; steps: StepStatus[] }

export interface Result {
  code: string; name: string; price: number; buy_score: number; sell_score: number;
  verdict: string; reasons: string[]; warnings: string[];
  detail: {
    indicators: Record<string, number | null>;
    volume7d: { ratio: number | null; up_down_ratio: number | null };
    flow7d: Record<string, number> | null;
    flow_detail7d: Record<string, number> | null;
    extra: string[];
    spark?: number[];
    sector?: { label: string; primary: string; tags: string[] };
    vix: number | null;
  };
}

export type Market = Record<string, Record<string, number>>;
export interface Dashboard { results: Result[]; waiting: Waiting[]; market: Market; status: Status }
export type Row = Record<string, number | string | null>;

export interface Check { label: string; passed: boolean; value: string; threshold?: string }
export interface Proposal {
  id: number; created_at: string; decided_at: string | null; code: string; name: string; side: "BUY" | "SELL";
  status: string; qty: number; price: number; stop: number | null; target: number | null;
  evidence: Check[]; sizing: { max_loss: number; gap_loss: number; binding: string; amount: number } | null;
  dry_run: number; order_id: string | null; result: string | null;
  attempt_mode?: string | null; attempt_qty?: number | null; attempt_price?: number | null; attempt_at?: string | null;
}
export interface Rule { code: string; name: string; group?: string; budget_krw: number; live: number; protect: number; stop_atr: number | null; target_atr: number | null }
export interface ScreenItem { name: string; price: number; atr: number; buy: { passed: boolean; evidence: Check[]; proposal_id: number | null } | null; sell: { passed: boolean; evidence: Check[]; proposal_id: number | null } | null; error?: string }
export interface TradingState {
  push: { server_configured: boolean; problem: string | null; devices: number };
  toss: { configured: boolean; connected: boolean; error: string | null; expires: string | null; days_left: number | null; buying_power?: number };
  risk: { live_enabled: boolean; live_switch: boolean; kill_switch: boolean; kill_reason: string; capital: number; monthly_pnl: number; monthly_limit: number; spent_today: number; daily_limit: number };
  rules: Rule[]; limits: Record<string, number | boolean | null>;
  pending: Proposal[]; needs_review: Proposal[]; unreconciled: Record<string, { note: string; kind: string; at: string }>; history: Proposal[]; screen: Record<string, ScreenItem | string>;
}

export interface Waiting { code: string; name: string }
export interface SearchHit { code: string; name: string; market: "STK" | "KSQ"; added: boolean }

export interface FlowRow {
  date: string; index_close: number | null; index_rate: number | null;
  individual: number | null; institution: number | null; foreign: number | null; other_corp: number | null;
  parts: Record<string, number | null>;
}
export interface MarketFlow { markets: { id: string; label: string; rows: FlowRow[] }[] }

export interface SeriesPoint { date: string; [k: string]: number | string | null }
export interface StockSeries { flows: SeriesPoint[]; foreign_own: SeriesPoint[]; short: SeriesPoint[]; short_balance: SeriesPoint[] }

export interface SectorRow { id: string; name: string; depth: number; parent: string | null; size: number; stock_count: number; exposure_pct: number | null; limit_pct: number | null; effective_limit_pct: number | null }
export interface SettingsResp {
  values: Record<string, number>; defaults: Record<string, number | null>; overridden: string[];
  fields: Record<string, { type: string; min: number; max: number; label: string; unit: string }>;
  live_on: boolean; confirm_text: string; gap: { max_gap_loss_pct: number; gap_assumption_pct: number }; sector_rows: SectorRow[];
  sector_options: { id: string; name: string; depth: number; parent: string | null }[];
}
export interface SectorNode { id: string; name: string; depth: number; size: number; exposure_pct: number | null; limit_pct: number | null; effective_limit_pct: number | null;
  stocks: { code: string; name: string; primary: boolean }[]; children?: SectorNode[] }
export interface SectorOverview { categories: SectorNode[]; synced_at: string | null; capital: number; default_limit_pct: number }

export interface Candidate { orderId: string; quantity: number; price: number; status: string | null; orderedAt: string; exact: boolean }

type N = number | null | undefined;
export interface UsMarketRow { symbol: string; name: string; date: string; close: number; chg_1d: N; chg_5d: N; chg_20d: N; rsi: N; above_ma20: boolean | null; above_ma60: boolean | null; trend: string | null }
export interface UsLive { symbol: string; name: string; last: number; base: number; base_at: string; as_of: string; chg: number }
export interface UsOverview { market: UsMarketRow[]; live: UsLive[]; status: Status }
export interface UsStock {
  symbol: string; name: string; date: string | null; flags: string[]; pending?: boolean;
  close?: N; chg_1d?: N; chg_5d?: N; chg_20d?: N; chg_60d?: N; rs20_nasdaq?: N; rs60_nasdaq?: N; rs20_small?: N; rs60_small?: N;
  rsi?: N; atr_pct?: N; stoch_k?: N; macd_hist?: N; bb_pos?: N; above_ma20?: boolean | null; above_ma60?: boolean | null; ma20_slope?: N;
  vol_ratio?: N; dollar_vol_20d?: N; from_high52_pct?: N; from_low52_pct?: N; max_gap_60d?: N;
  meta_updated_at?: string | null; market_cap?: N; short_pct_float?: N; beta?: N; sector?: string | null; industry?: string | null; next_earnings?: string | null; days_to_earnings?: N;
}
export interface UsStocks { stocks: UsStock[]; status: Status; max: number }
export interface UsDiscoverHit { symbol: string; name: string; market_cap: N; price: N; avg_volume: N; exchange: string | null; added: boolean }
