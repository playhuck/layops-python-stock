import { useEffect, useState } from "react";
import { addUsStock, fetchUsDiscover } from "../api";
import { num, usd } from "../format";
import type { UsDiscoverHit } from "../types";

// 시총(달러) 구간: 소형 0.3~2B, 중형 2~10B
const CAPS: [string, number, number][] = [["소형+중형", 3e8, 1e10], ["소형", 3e8, 2e9], ["중형", 2e9, 1e10]];

/** 미국 종목 추가 패널(종목 추가 시트의 '미국' 탭): 티커 직접 입력 + 후보 찾기 */
export default function UsAddPanel({ onAdded, onToast }: { onAdded: (symbol: string) => void; onToast: (m: string) => void }) {
  return (
    <div>
      <AddTicker onAdded={onAdded} onToast={onToast} />
      <Discover onAdded={onAdded} />
    </div>
  );
}

/** 티커를 입력하면 서버가 야후에서 시세가 오는 티커인지 확인한 뒤 추가한다 (후보 찾기에 안 나오는 종목도 가능) */
export function AddTicker({ onAdded, onToast }: { onAdded: (symbol: string) => void; onToast: (m: string) => void }) {
  const [sym, setSym] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: { preventDefault: () => void }) => {
    e.preventDefault();
    const symbol = sym.trim().toUpperCase();
    if (!symbol) return;
    setBusy(true);
    try { await addUsStock(symbol, ""); setSym(""); onAdded(symbol); } catch (err) { onToast((err as Error).message); }
    setBusy(false);
  };
  return (
    <form className="us-form" onSubmit={submit} style={{ margin: "0 0 10px" }}>
      <label>티커로 추가<input className="input sm" autoFocus value={sym} onChange={(e) => setSym(e.target.value)} placeholder="SYM" maxLength={5} aria-label="추가할 티커" /></label>
      <button className="add" type="submit" disabled={busy || !sym.trim()}>{busy ? "찾는 중…" : "찾아서 추가"}</button>
      <small className="muted">입력한 티커를 야후에서 찾아 추가해요 (시세가 오는 티커만 추가돼요)</small>
    </form>
  );
}

function Discover({ onAdded }: { onAdded: (symbol: string) => void }) {
  const [cap, setCap] = useState(0);
  const [hits, setHits] = useState<UsDiscoverHit[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => {
    let stale = false; setHits(null); setErr(null);
    fetchUsDiscover(CAPS[cap][1], CAPS[cap][2]).then((h) => { if (!stale) setHits(h); }).catch(() => { if (!stale) setErr("후보를 불러오지 못했어요. 잠시 후 다시 시도해 주세요"); });
    return () => { stale = true; };
  }, [cap]);
  const add = async (h: UsDiscoverHit) => {
    setBusy(h.symbol);
    try { await addUsStock(h.symbol, h.name); setHits((cur) => cur && cur.map((x) => (x.symbol === h.symbol ? { ...x, added: true } : x))); onAdded(h.symbol); }
    catch (e) { setErr((e as Error).message); }
    setBusy(null);
  };
  return (
    <div>
      <div className="toolbar">
        <h3 style={{ margin: 0 }}>후보 찾기 <small className="muted">(나스닥 · 평균 거래량 100만주 이상 · 주가 $5 이상 · 거래량 큰 순 상위 40개)</small></h3>
        <div className="seg" style={{ minWidth: 220 }}>{CAPS.map(([l], i) => <button key={l} className={cap === i ? "on" : ""} onClick={() => setCap(i)}>{l}</button>)}</div>
      </div>
      {err && <p className="warn">{err}</p>}
      {!hits && !err && <p className="empty">불러오는 중…</p>}
      {hits && hits.length === 0 && <p className="empty">조건에 맞는 종목이 없어요</p>}
      {hits && hits.length > 0 && (
        <div className="tbl-wrap" style={{ maxHeight: 320, overflowY: "auto" }}>
          <table className="tbl">
            <thead><tr><th>종목</th><th>시총</th><th>주가</th><th>평균 거래량</th><th /></tr></thead>
            <tbody>{hits.map((h) => (
              <tr key={h.symbol}><td><b>{h.symbol}</b> <small className="muted">{h.name}</small></td><td>{usd(h.market_cap)}</td><td>${num(h.price, 2)}</td>
                <td>{h.avg_volume == null ? "-" : `${(h.avg_volume / 1e6).toFixed(1)}M주`}</td>
                <td><button className="add" disabled={h.added || busy !== null} onClick={() => add(h)}>{h.added ? "추가됨" : "추가"}</button></td></tr>))}</tbody>
          </table>
        </div>)}
      <small>야후 스크리너 결과예요. 거래량 큰 순이라 변동성이 큰 종목이 많고, 순위 밖 종목은 위의 티커 입력으로 추가하세요. 추가 전에 실적일·갭 위험을 꼭 확인하세요.</small>
    </div>
  );
}
