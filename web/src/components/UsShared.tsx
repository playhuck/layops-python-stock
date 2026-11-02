import { pct, signed } from "../format";
import type { UsKrw, UsVerdict } from "../types";

export const cls = (v: number | null | undefined) => (v == null ? "" : v >= 0 ? "pos" : "neg");
// 판정 → 칩 색: 매수 검토=빨강, 매도 검토=파랑, 주의·실적일 확인 필요=주황, 나머지는 회색 (한국 화면과 같은 색 약속)
const CHIP: Record<string, string> = { review: "buy", review_check: "split", sell: "sell", caution: "split" };
export const Verdict = ({ v }: { v: UsVerdict }) => <span className={`chip ${CHIP[v.code] ?? "wait"}`}>{v.label}</span>;
export const px2 = (n: number | null | undefined) => (n == null ? "-" : n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

/** 원화 기준 손익 한 줄(행에 붙이는 짧은 글). 못 구하면 사유를 흐리게 보여준다 */
export function KrwInline({ k }: { k: UsKrw | null | undefined }) {
  if (!k) return null;
  if (!k.available) return <span className="muted"> · 원화 손익: {k.reason}</span>;
  return <> · 원화 <b className={cls(k.pnl_pct)}>{pct(k.pnl_pct)}</b> <span className="muted">({signed(Math.round(k.pnl_krw!))}원)</span></>;
}

/** 원화 손익을 주가 손익과 환율 손익으로 나눈 설명 */
const fxText = (n: number | null | undefined) => (n == null ? "-" : n.toLocaleString("ko-KR", { maximumFractionDigits: 1 }));
export const krwBreakdown = (k: UsKrw): string =>
  `원화 환산: 매수 환율 ${fxText(k.fx_buy)}원 → 현재 ${fxText(k.fx_now)}원(${k.fx_as_of ?? "-"} 기준, ${pct(k.fx_pct)}) · 주가 ${pct(k.price_pct)} · 환율 ${pct(k.fx_pct)} → 원화 ${pct(k.pnl_pct)} · 수수료·세금·환전 수수료 제외`;
