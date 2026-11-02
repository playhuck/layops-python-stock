import { useState } from "react";
import { deleteKrPosition, putKrPosition } from "../api";
import { pct, signed, won } from "../format";
import type { HoldingItem, HoldingsSource, HoldingsView } from "../types";
import { krHolding, krUnknownNote, pnlPct, SourceToggle } from "./Holdings";

const EMPTY = { qty: "", avg: "", date: "", note: "" };

/** 종목 상세의 '내 보유': 선택한 출처(직접 입력/가져와서 반영)의 보유량과 평단 대비 위치. 직접 입력일 때만 고칠 수 있다 */
export default function MyHolding({ code, price, holdings, onSource, onChanged, onToast }: {
  code: string; price: number; holdings: HoldingsView | null; onSource: (s: HoldingsSource) => void; onChanged: () => void; onToast: (m: string) => void;
}) {
  const source = holdings?.source ?? "manual";
  const h = krHolding(holdings, code);
  const unknown = krUnknownNote(holdings);
  const [f, setF] = useState(EMPTY);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [del, setDel] = useState(false);
  const set = (k: keyof typeof EMPTY) => (e: { target: { value: string } }) => setF((cur) => ({ ...cur, [k]: e.target.value }));
  const startEdit = (cur?: HoldingItem) => { setF(cur ? { qty: String(cur.qty), avg: String(cur.avg), date: cur.opened_on ?? "", note: cur.note ?? "" } : EMPTY); setEditing(true); };
  const save = async (e: { preventDefault: () => void }) => {
    e.preventDefault(); setBusy(true);
    try { await putKrPosition(code, { qty: Number(f.qty), avg_price: Number(f.avg), opened_on: f.date || null, note: f.note }); setEditing(false); onToast("보유를 저장했어요"); onChanged(); }
    catch (err) { onToast((err as Error).message); }
    setBusy(false);
  };
  const remove = async () => {
    if (!del) { setDel(true); setTimeout(() => setDel(false), 3000); return; }          // 두 번 눌러야 지운다
    try { await deleteKrPosition(code); setDel(false); onToast("보유 기록을 지웠어요"); onChanged(); } catch (err) { onToast((err as Error).message); }
  };
  const p = h ? pnlPct(h, price) : null;
  const last = h ? h.last ?? price : price;
  return (
    <section className="card">
      <div className="toolbar"><h2 style={{ margin: 0 }}>내 보유</h2><SourceToggle source={source} onChange={onSource} /></div>
      {h && (
        <>
          <div className="hold-sum">
            <div><span>보유량</span><b>{h.qty.toLocaleString("ko-KR")}주</b></div>
            <div><span>평균 매수가</span><b>{won(h.avg)}원</b></div>
            <div><span>평가금액</span><b>{won(h.qty * last)}원</b></div>
            <div><span>평가손익</span><b className={p == null ? "" : p >= 0 ? "pos" : "neg"}>{p == null ? "-" : `${pct(p)} · ${signed(Math.round((last - h.avg) * h.qty))}원`}</b></div>
          </div>
          <small className="muted">{p == null ? "" : `현재가가 평단보다 ${p >= 0 ? "위" : "아래"}에 있어요 · 차트의 주황 점선이 내 평단이에요`}{h.opened_on ? ` · 매수일 ${h.opened_on}` : ""}{h.note ? ` · ${h.note}` : ""}</small>
        </>
      )}
      {source === "import" && !h && <p className="empty" style={{ margin: "8px 0 0" }}>{unknown ?? "토스에서 가져온 보유에 이 종목이 없어요"}</p>}
      {source === "manual" && !editing && (
        <div style={{ marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          {!h && <small className="muted">직접 입력한 보유가 없어요.</small>}
          <button className="add" onClick={() => startEdit(h)}>{h ? "수정" : "보유 입력"}</button>
          {h && <button className="add" onClick={remove}>{del ? "정말 지우기" : "삭제"}</button>}
        </div>
      )}
      {source === "manual" && editing && (
        <form className="us-form" onSubmit={save} style={{ marginTop: 10 }}>
          <label>수량(주)<input className="input sm" value={f.qty} onChange={set("qty")} inputMode="numeric" placeholder="10" required /></label>
          <label>평균 매수가(원)<input className="input sm wide" value={f.avg} onChange={set("avg")} inputMode="decimal" placeholder="70000" required /></label>
          <label>매수일(선택)<input className="input sm wide" type="date" value={f.date} onChange={set("date")} /></label>
          <label>메모(선택)<input className="input sm wide" value={f.note} onChange={set("note")} maxLength={200} /></label>
          <button className="add" type="submit" disabled={busy}>{busy ? "저장 중…" : "저장"}</button>
          <button className="add" type="button" onClick={() => setEditing(false)}>취소</button>
        </form>
      )}
      <small className="muted" style={{ display: "block", marginTop: 8 }}>{source === "manual" ? "직접 입력한 값은 화면 표시용이에요. 매도 제안·주문은 토스 보유를 기준으로 해요." : "토스에서 가져온 보유예요. 직접 입력한 보유는 보이지 않아요."}</small>
    </section>
  );
}
