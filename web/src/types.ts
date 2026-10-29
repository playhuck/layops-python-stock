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
