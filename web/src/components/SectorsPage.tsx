import { useEffect, useState } from "react";
import { fetchSectorOverview, fetchSectorPerf } from "../api";
import type { SectorNode, SectorOverview } from "../types";

const DUR: Record<string, string> = { "1d": "1일", "1w": "1주", "1mo": "1개월", "3mo": "3개월", "6mo": "6개월", "1y": "1년" };

function Perf({ id }: { id: string }) {
  const [rates, setRates] = useState<{ duration: string; rate: number | null }[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { fetchSectorPerf(id).then((r) => setRates(r.rates)).catch((e) => setErr(String(e).slice(0, 60))); }, [id]);
  if (err) return <small className="warn">업종 수익률을 못 불러왔어요</small>;
  if (!rates) return <small>업종 수익률 불러오는 중…</small>;
  return <div className="perf">{rates.map((r) => <span key={r.duration}><small>{DUR[r.duration] ?? r.duration}</small><b className={r.rate == null ? "" : r.rate >= 0 ? "pos" : "neg"}>{r.rate == null ? "-" : `${r.rate >= 0 ? "+" : ""}${r.rate.toFixed(1)}%`}</b></span>)}</div>;
}

function Bar({ used, limit, capital }: { used: number | null; limit: number | null; capital: number }) {
  if (used == null) return <small>{capital > 0 ? "보유 비중은 토스 연결 후 표시돼요" : "설정에서 자본금을 정하면 보유 비중이 표시돼요"}</small>;
  const w = limit ? Math.min(100, (used / limit) * 100) : 0;
  const hot = limit != null && used > limit;
  return (
    <div className="expo"><div className="eb"><i style={{ width: `${w}%`, background: hot ? "var(--danger)" : w > 75 ? "var(--warn)" : "var(--primary)" }} /></div>
      <small className={hot ? "neg" : ""}>보유 {used.toFixed(1)}%{limit != null ? ` / 한도 ${limit}%` : " (한도 없음)"}</small></div>
  );
}

function StockChips({ n, onOpen }: { n: SectorNode; onOpen: (code: string) => void }) {
  return <div className="chips">{n.stocks.map((s) => <button key={s.code} className={`chip stk${s.primary ? " pri" : ""}`} onClick={() => onOpen(s.code)} title={s.primary ? "대표 업종" : ""}>{s.name}</button>)}</div>;
}

export default function SectorsPage({ onOpen }: { onOpen: (code: string) => void }) {
  const [data, setData] = useState<SectorOverview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const [open, setOpen] = useState<Record<string, boolean>>({});
  useEffect(() => { setData(null); fetchSectorOverview(all).then(setData).catch((e) => setErr(String(e))); }, [all]);

  if (err) return <p className="notice err">업종 정보를 불러오지 못했어요: {err}</p>;
  if (!data) return <p className="empty">불러오는 중…</p>;
  const cats = data.categories;
  return (
    <div>
      <div className="toolbar">
        <small>{data.synced_at ? `업종 분류 기준일 ${data.synced_at.slice(0, 10)} · 토스증권 분류` : "업종 분류 데이터가 아직 없어요. 데이터를 갱신하면 받아와요."}</small>
        <label><input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />내 종목이 없는 업종도 보기</label>
      </div>
      {cats.length === 0 && <p className="empty">표시할 업종이 없어요.</p>}
      <div className="cat-grid">
        {cats.map((c) => (
          <section className="card cat" key={c.id}>
            <button className="cat-h" onClick={() => setOpen({ ...open, [c.id]: !open[c.id] })}>
              <div><b>{c.name}</b> <small>대분류 · 종목 {c.size.toLocaleString()}개</small></div>
              <span className="chev">{open[c.id] ? "▾" : "▸"}</span>
            </button>
            <Bar used={c.exposure_pct} limit={c.effective_limit_pct} capital={data.capital} />
            {c.stocks.length > 0 ? <StockChips n={c} onOpen={onOpen} /> : <small>내 관심 종목 없음</small>}
            {open[c.id] && (
              <div className="cat-body">
                <Perf id={c.id} />
                {(c.children ?? []).length > 0 && <div className="subs">
                  {c.children!.map((m) => (
                    <div className="sub" key={m.id}>
                      <div className="sub-h"><span>└ {m.name} <small>중분류 · {m.size}개</small></span>{m.limit_pct != null && <span className="chip mkt">한도 {m.limit_pct}%</span>}</div>
                      {m.stocks.length > 0 ? <StockChips n={m} onOpen={onOpen} /> : <small>내 종목 없음</small>}
                      {m.exposure_pct != null && m.exposure_pct > 0 && <Bar used={m.exposure_pct} limit={m.effective_limit_pct} capital={data.capital} />}
                    </div>))}
                </div>}
              </div>)}
          </section>
        ))}
      </div>
      <small>종목은 여러 업종에 동시에 속할 수 있어서 같은 종목이 여러 카드에 나와요. ‘대표 업종’(진한 칩)은 그중 가장 큰 대분류예요.</small>
    </div>
  );
}
