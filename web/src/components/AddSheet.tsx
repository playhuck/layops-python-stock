import { useEffect, useState } from "react";
import { addStock, searchStocks } from "../api";
import { avatarColor, mktLabel } from "../format";
import type { SearchHit } from "../types";

export default function AddSheet({ onClose, onAdded }: { onClose: () => void; onAdded: (name: string) => void }) {
  const [q, setQ] = useState("");
  const [group, setGroup] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {  // 입력이 멈춘 뒤 0.3초에 검색 (KRX 호출 최소화)
    if (!q.trim()) { setHits([]); setLoading(false); return; }
    let stale = false;  // 더 최근 입력이 있으면 이전 응답은 버린다 (KRX 응답이 3~4초 걸려 순서가 뒤바뀔 수 있음)
    setLoading(true);
    const t = setTimeout(() => {
      searchStocks(q.trim()).then((h) => { if (!stale) { setHits(h); setErr(null); } })
        .catch(() => { if (!stale) setErr("검색에 실패했어요. 잠시 후 다시 시도해 주세요"); })
        .finally(() => { if (!stale) setLoading(false); });
    }, 300);
    return () => { stale = true; clearTimeout(t); };
  }, [q]);

  const add = async (h: SearchHit) => {
    setBusy(true);
    try { await addStock({ code: h.code, name: h.name, market: h.market, group: group || undefined }); onAdded(h.name); }
    catch (e) { setErr((e as Error).message); setBusy(false); }
  };

  return (
    <div className="overlay" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()}>
        <div className="grab" />
        <h2 style={{ margin: "0 0 14px", fontSize: 20 }}>종목 추가</h2>
        <input className="input" autoFocus placeholder="종목명 또는 코드 (예: 삼성전자, 005930)" value={q} onChange={(e) => setQ(e.target.value)} />
        <input className="input" style={{ marginTop: 8 }} placeholder="업종 직접 지정 (선택) — 비워두면 토스 분류로 자동 설정돼요" value={group} onChange={(e) => setGroup(e.target.value)} />
        {err && <p className="warn">{err}</p>}
        <div style={{ margin: "10px -20px 0" }}>
          {hits.map((h) => (
            <div className="row" key={h.code}>
              <div className="avatar" style={{ background: avatarColor(h.code) }}>{h.name[0]}</div>
              <div className="mid"><div className="name">{h.name}</div><small>{h.code} · {mktLabel(h.market)}</small></div>
              <button className="add" disabled={h.added || busy} onClick={() => add(h)}>{h.added ? "추가됨" : "추가"}</button>
            </div>
          ))}
          {loading && <p className="empty">검색 중…</p>}
          {q.trim() && !loading && hits.length === 0 && !err && <p className="empty">검색 결과가 없어요</p>}
        </div>
        <small>코스피·코스닥 상장 종목을 검색해요. 추가하면 시세·수급을 바로 가져오고, 업종은 자동으로 분류해 업종 쏠림을 검사해요.</small>
      </div>
    </div>
  );
}
