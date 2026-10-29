import type { Dashboard, Row, Status } from "./types";

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url} → ${r.status}`);
  return r.json();
}

export const fetchDashboard = () => get<Dashboard>("/api/dashboard");
export const fetchStatus = () => get<Status>("/api/status");
export const fetchSeries = (code: string) => get<Row[]>(`/api/series/${code}`);
export const startRefresh = () => fetch("/api/refresh", { method: "POST" });

// 상태 변경 요청은 X-Stockwatch 헤더 필수 (서버의 CSRF 방어)
async function send<T>(method: string, url: string, body?: unknown): Promise<T> {
  const r = await fetch(url, { method, headers: { "Content-Type": "application/json", "X-Stockwatch": "1" }, body: body === undefined ? undefined : JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) {
    const err = new Error((j as { error?: string }).error ?? `${url} → ${r.status}`) as Error & { needsConfirm?: boolean };
    err.needsConfirm = !!(j as { needs_confirm?: boolean }).needs_confirm;   // 서버가 '위험설정변경' 확인을 요구하는 경우
    throw err;
  }
  return j as T;
}

import type { Rule, TradingState } from "./types";
export const fetchTrading = () => get<TradingState>("/api/trading/state");
export const saveRule = (code: string, r: Partial<Rule>) => send("PUT", `/api/trading/rules/${code}`, r);
export const setLive = (enabled: boolean, confirm?: string) => send("POST", "/api/trading/live", { enabled, confirm });
export const resetKill = (confirm: string) => send("POST", "/api/trading/kill/reset", { confirm });
export const decide = (id: number, action: "approve" | "reject") => send<{ ok: boolean; mode?: string; error?: string }>("POST", `/api/trading/proposals/${id}/${action}`);
export const syncOrders = () => send<{ notes: string[] }>("POST", "/api/trading/sync");
export const pushTest = () => send<{ sent: number; failed: number; error: string | null }>("POST", "/api/push/test");

import type { SearchHit } from "./types";
export const searchStocks = (q: string) => get<SearchHit[]>(`/api/stocks/search?q=${encodeURIComponent(q)}`);
export const addStock = (b: { code: string; name: string; market: string; group?: string }) => send("POST", "/api/watchlist", b);
export const removeStock = (code: string) => send("DELETE", `/api/watchlist/${code}`);

import type { MarketFlow } from "./types";
export const fetchMarketFlow = (days: number) => get<MarketFlow>(`/api/market/flow?days=${days}`);

import type { StockSeries } from "./types";
export const fetchStockSeries = (code: string, days: number) => get<StockSeries>(`/api/stocks/${code}/series?days=${days}`);

import type { SectorOverview, SettingsResp } from "./types";
export const fetchSettings = () => get<SettingsResp>("/api/settings");
export const saveSettings = (values: Record<string, number>, confirm?: string) => send<{ ok: boolean }>("PUT", "/api/settings", { values, confirm });
export const saveSectorLimit = (sector_id: string, max_pct: number | null, confirm?: string) => send<{ ok: boolean }>("PUT", "/api/settings/sector-limit", { sector_id, max_pct, confirm });
export const resetSettings = (confirm?: string) => send<{ ok: boolean }>("POST", "/api/settings/reset", { confirm });
export const fetchSectorOverview = (all: boolean) => get<SectorOverview>(`/api/sectors/overview${all ? "?all=1" : ""}`);
export const fetchSectorPerf = (id: string) => get<{ rates: { duration: string; rate: number | null }[] }>(`/api/sectors/${id}/performance`);

export const resolveProposal = (id: number, resolution: "adopt" | "not_sent" | "unreconciled", order_id?: string) =>
  send<{ ok: boolean; status?: string }>("POST", `/api/trading/proposals/${id}/resolve`, { resolution, order_id });
export const fetchCandidates = (id: number) => get<import("./types").Candidate[]>(`/api/trading/proposals/${id}/candidates`);
export const dismissUnreconciled = (code: string) => send<{ ok: boolean }>("POST", `/api/trading/unreconciled/${code}/dismiss`);
