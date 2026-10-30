export const signed = (n: number, digits = 0) =>
  (n >= 0 ? "+" : "") + n.toLocaleString("ko-KR", { maximumFractionDigits: digits });
export const won = (n: number | null | undefined) => (n == null || !Number.isFinite(n) ? "-" : n.toLocaleString("ko-KR", { maximumFractionDigits: 0 }));
export const num = (n: number | null | undefined, d = 0) => (n == null ? "-" : n.toFixed(d));

/** 판정 문구 → 색상 클래스 */
export const verdictClass = (v: string) =>
  v.includes("매수 적기") ? "buy" : v.includes("분할") ? "split" : v.includes("매도") ? "sell" : "wait";

const HUES = ["#3182f6", "#f04452", "#00b493", "#f59e0b", "#8b5cf6", "#ec4899", "#14b8a6", "#64748b"];
export const avatarColor = (code: string) => HUES[[...code].reduce((a, c) => a + c.charCodeAt(0), 0) % HUES.length];
export const mktLabel = (m: string) => (m === "KSQ" ? "코스닥" : "코스피");

/** 큰 금액을 읽기 쉽게: 1,000만원 / 1.5억원 */
export const krwText = (n: number) => (n >= 1e8 ? `${(n / 1e8).toLocaleString("ko-KR", { maximumFractionDigits: 2 })}억원` : n >= 1e4 ? `${Math.round(n / 1e4).toLocaleString("ko-KR")}만원` : `${n.toLocaleString("ko-KR")}원`);

/** 등락률 +1.2% (값이 없으면 -) */
export const pct = (n: number | null | undefined, d = 1) => (n == null || !Number.isFinite(n) ? "-" : `${n >= 0 ? "+" : ""}${n.toFixed(d)}%`);
/** 달러 금액을 읽기 쉽게: $1.2B / $35.0M */
export const usd = (n: number | null | undefined) => (n == null || !Number.isFinite(n) ? "-" : n >= 1e9 ? `$${(n / 1e9).toFixed(1)}B` : n >= 1e6 ? `$${(n / 1e6).toFixed(1)}M` : `$${Math.round(n).toLocaleString("en-US")}`);
