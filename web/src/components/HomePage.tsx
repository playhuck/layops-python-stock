import { avatarColor, signed, verdictClass, won } from "../format";
import { useState } from "react";
import type { Dashboard } from "../types";
import Sparkline from "./Sparkline";

export default function HomePage({ data: d, onOpen, onAdd }: { data: Dashboard; onOpen: (code: string) => void; onAdd: () => void }) {
  const data = { ...d, waiting: d.waiting ?? [] };  // 구버전 서버 응답에도 화면이 죽지 않도록
  const [cat, setCat] = useState<string>("");
  const market = Object.entries(data.market ?? {});
  const tagCount: Record<string, number> = {};
  data.results.forEach((r) => (r.detail.sector?.tags ?? []).forEach((t) => (tagCount[t] = (tagCount[t] ?? 0) + 1)));
  const tags = Object.entries(tagCount).sort((a, b) => b[1] - a[1]).map(([t]) => t);
  const shown = cat ? data.results.filter((r) => r.detail.sector?.tags.includes(cat)) : data.results;
  const count = (c: string) => data.results.filter((r) => verdictClass(r.verdict) === c).length;
  const fgn = (r: Dashboard["results"][number]) => r.detail.flow7d?.foreign;

  return (
    <div className="cols">
      <div className="col-main">
        <div className="section-title" style={{ order: 2 }}>관심 종목 <small>{data.results.length + data.waiting.length}개</small></div>
        {tags.length > 1 && <div className="chips filter" style={{ order: 2 }}><button className={`chip fil${cat === "" ? " on" : ""}`} onClick={() => setCat("")}>전체</button>{tags.map((t) => <button key={t} className={`chip fil${cat === t ? " on" : ""}`} onClick={() => setCat(t)}>{t} <small>{tagCount[t]}</small></button>)}</div>}
        <section className="card list" style={{ order: 2, padding: "4px 0" }}>
          <div className="thead"><span /><span>종목</span><span className="c-spark">30일 추이</span><span className="r">매수 점수</span><span className="r">매도 점수</span><span className="r c-rsi">RSI</span><span className="r c-fgn">외국인 7일</span><span className="r">현재가 · 판정</span></div>
          {shown.map((r) => (
            <button className="row srow" key={r.code} onClick={() => onOpen(r.code)}>
              <div className="avatar" style={{ background: avatarColor(r.code) }}>{r.name[0]}</div>
              <div className="mid" title={r.detail.sector?.label}><div className="name">{r.name}</div><small>{r.code}{r.detail.sector?.primary ? ` · ${r.detail.sector.primary}` : ""}</small></div>
              <Sparkline values={r.detail.spark ?? []} />
              <div className="cell r pos">{r.buy_score}</div>
              <div className="cell r neg">{r.sell_score}</div>
              <div className="cell r c-rsi">{r.detail.indicators.rsi == null ? "-" : r.detail.indicators.rsi.toFixed(0)}</div>
              <div className={`cell r c-fgn ${(fgn(r) ?? 0) >= 0 ? "pos" : "neg"}`}>{fgn(r) == null ? "-" : `${signed(Math.round(fgn(r)! / 1e4))}만주`}</div>
              <div className="right">
                <div className="price">{won(r.price)}원</div>
                <span className={`chip ${verdictClass(r.verdict)}`}>{r.verdict.replace(/\s*\(.*\)/, "")}</span>
              </div>
            </button>
          ))}
          {data.waiting.map((w) => (
            <div className="row" key={w.code}>
              <div className="avatar" style={{ background: "var(--mute)" }}>{w.name[0]}</div>
              <div className="mid"><div className="name">{w.name}</div><small>{w.code}</small></div>
              <div className="right"><small>{data.status.running ? "시세 수집 중…" : "데이터 없음 — 갱신해 주세요"}</small></div>
            </div>
          ))}
          {data.results.length === 0 && data.waiting.length === 0 && <p className="empty">관심 종목을 추가해 보세요</p>}
          <button className="row add-row" onClick={onAdd}>＋ 종목 추가</button>
        </section>
        <p className="disclaimer" style={{ order: 9 }}>참고용 분석이며 투자 판단과 손실 책임은 본인에게 있습니다.</p>
      </div>

      <aside className="col-side">
        {market.length > 0 && (
          <section className="card kv-list" style={{ order: 1 }}>
            <h2>시장 수급 <small>최근 7일 순매수 (억원)</small></h2>
            {market.map(([m, v]) => (
              <div className="kv" key={m}>
                <span className="k">{m}</span>
                <span className="v" style={{ fontWeight: 500 }}>{Object.entries(v).map(([k, x]) => <span key={k} className={x >= 0 ? "pos" : "neg"}>{k} {signed(x)}{"  "}</span>)}</span>
              </div>
            ))}
            <small>일별 상세는 ‘시장’ 탭에서 볼 수 있어요.</small>
          </section>
        )}
        <section className="card" style={{ order: 3 }}>
          <h2>오늘의 판정</h2>
          {([["buy", "매수 적기"], ["split", "분할 매수"], ["wait", "관망"], ["sell", "매도/익절 준비"]] as const).map(([c, l]) => (
            <div className="sumline" key={c}><span className={`chip ${c}`}>{l}</span><b>{count(c)}종목</b></div>
          ))}
        </section>
      </aside>
    </div>
  );
}
