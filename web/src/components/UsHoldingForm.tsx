import type { ReactNode } from "react";
import type { UsPosition, UsPositionInput } from "../types";

export const HOLD_EMPTY = { symbol: "", qty: "", avg: "", date: "", note: "", atr: "", fx: "" };
export type HoldForm = typeof HOLD_EMPTY;

/** 저장된 보유 → 수정 폼의 값. 진입 ATR 은 '직접 입력'한 값만 채운다(자동 계산 값은 비워 두면 서버가 유지/재계산한다) */
export const holdFormFrom = (p: UsPosition): HoldForm => ({
  symbol: p.symbol, qty: String(p.qty), avg: String(p.avg_price), date: p.opened_on ?? "", note: p.note,
  atr: p.entry_atr_source === "manual" && p.entry_atr != null ? String(p.entry_atr) : "", fx: p.fx_buy != null ? String(p.fx_buy) : "",
});

/** 폼 → 서버 요청 본문. 비어 있으면 null(매수일·진입 ATR·환율은 선택) */
export const holdBody = (f: HoldForm): UsPositionInput => ({
  qty: Number(f.qty), avg_price: Number(f.avg), opened_on: f.date || null, note: f.note,
  entry_atr: f.atr.trim() === "" ? null : Number(f.atr), fx_buy: f.fx.trim() === "" ? null : Number(f.fx.replace(/,/g, "")),
});

/** 미국 보유 입력·수정 폼 (미국 탭의 '내 보유 종목'과 종목 상세의 '내 보유'가 함께 쓴다). lockSymbol 이면 티커를 고칠 수 없다 */
export default function UsHoldingForm({ f, setF, busy, lockSymbol = false, onSubmit, children }: {
  f: HoldForm; setF: (next: HoldForm) => void; busy: boolean; lockSymbol?: boolean; onSubmit: (e: { preventDefault: () => void }) => void; children?: ReactNode;
}) {
  const set = (k: keyof HoldForm) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <form className="us-form" onSubmit={onSubmit}>
      <label>티커<input className="input sm" value={f.symbol} onChange={set("symbol")} placeholder="SOUN" maxLength={5} required readOnly={lockSymbol} /></label>
      <label>수량<input className="input sm" value={f.qty} onChange={set("qty")} placeholder="10" inputMode="decimal" required /></label>
      <label>평균 매수가($)<input className="input sm" value={f.avg} onChange={set("avg")} placeholder="11.20" inputMode="decimal" required /></label>
      <label>매수일(선택)<input className="input sm wide" type="date" value={f.date} onChange={set("date")} /></label>
      <label title="매수한 날 적용된 원/달러 환율(예: 1350). 넣으면 원화 기준 손익을 보여줘요. 모르면 비워두세요">매수 환율(원/$, 선택)<input className="input sm" value={f.fx} onChange={set("fx")} placeholder="1350" inputMode="decimal" /></label>
      <label title="비우면 매수일 직전(없으면 지금) 시세로 자동 계산해요. 손절·목표·트레일링은 이 값으로 고정돼요">진입 ATR($, 선택)<input className="input sm" value={f.atr} onChange={set("atr")} placeholder="자동" inputMode="decimal" /></label>
      <label>메모(선택)<input className="input sm wide" value={f.note} onChange={set("note")} maxLength={200} /></label>
      <button className="add" type="submit" disabled={busy}>{busy ? "저장 중…" : "저장"}</button>
      {children}
    </form>
  );
}
