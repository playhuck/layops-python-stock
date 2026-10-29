import { useState } from "react";
import { removeStock } from "../api";
import { avatarColor, num, signed, verdictClass, won } from "../format";
import type { Result } from "../types";
import ChartCard from "./ChartCard";
import FlowCard from "./FlowCard";
import Gauge from "./Gauge";

function Meter({ label, v, color }: { label: string; v: number; color: string }) {
  return <div className="meter"><span>{label}</span><div className="bar"><i style={{ width: `${Math.min(100, v)}%`, background: color }} /></div><span className="v">{v}</span></div>;
}

export default function StockPage({ r, onBack, onToast, onRemoved }: { r: Result; onBack: () => void; onToast: (m: string) => void; onRemoved: () => void }) {
  const [confirming, setConfirming] = useState(false);
  const i = r.detail.indicators;
  const remove = async () => {
    try { await removeStock(r.code); onToast(`${r.name}을(를) 관심 종목에서 뺐어요`); onRemoved(); }
    catch (e) { onToast(`⚠ ${(e as Error).message}`); setConfirming(false); }
  };
  const flows = r.detail.flow7d, fd = r.detail.flow_detail7d;
  return (
    <div>
      <div className="top" style={{ paddingBottom: 0 }}><button className="back" onClick={onBack} aria-label="뒤로">← 관심 종목</button><span /></div>
      <div className="hero">
        <div className="nm">{r.name} · {r.code}</div>
        {!!r.detail.sector?.tags.length && <div className="tags">{r.detail.sector.tags.map((t) => <span key={t} className="chip mkt">{t}</span>)}{r.detail.sector.label && <small> {r.detail.sector.label}</small>}</div>}
        <div className="big">{won(r.price)}원</div>
        <span className={`chip ${verdictClass(r.verdict)}`}>{r.verdict}</span>
      </div>

      <div className="cols">
      <div className="col-main">
      <div style={{ order: 2 }}><ChartCard code={r.code} /></div>
      <div style={{ order: 3 }}><FlowCard code={r.code} /></div>
      <section className="card kv-list" style={{ order: 4 }}>
        <h2>지표</h2>
        <Gauge label="RSI (14)" value={i.rsi ?? null} low={30} high={70} />
        <Gauge label="스토캐스틱 슬로우 %K" value={i.stoch_k ?? null} low={20} high={80} sub={`%D ${num(i.stoch_d)}`} />
        {[["MACD / 시그널", `${num(i.macd)} / ${num(i.macd_signal)}`],
          ["5 · 20일선", `${won(i.ma5 ?? 0)} · ${won(i.ma20 ?? 0)}`], ["60 · 120일선", `${won(i.ma60 ?? 0)} · ${won(i.ma120 ?? 0)}`],
          ["볼린저 하단 · 상단", `${won(i.bb_low ?? 0)} · ${won(i.bb_up ?? 0)}`],
          ["7일 거래량 / 20일 평균", r.detail.volume7d.ratio ? `${r.detail.volume7d.ratio.toFixed(2)}배` : "-"],
          ["VIX", r.detail.vix ? r.detail.vix.toFixed(1) : "-"]].map(([k, v]) => <div className="kv" key={k}><span className="k">{k}</span><span className="v">{v}</span></div>)}
      </section>
      <div className="pair">
      {flows && (
        <section className="card kv-list" style={{ order: 5 }}>
          <h2>최근 7일 수급 (순매수 수량)</h2>
          {Object.entries(flows).map(([k, v]) => <div className="kv" key={k}><span className="k">{{ individual: "개인", institution: "기관", foreign: "외국인" }[k] ?? k}</span><span className={`v ${v >= 0 ? "pos" : "neg"}`}>{signed(v)}주</span></div>)}
        </section>
      )}
      {fd && (
        <section className="card kv-list" style={{ order: 5 }}>
          <h2>투자자별 순매수대금 (7일, 억원)</h2>
          {Object.entries(fd).map(([k, v]) => <div className="kv" key={k}><span className="k">{k}</span><span className={`v ${v >= 0 ? "pos" : "neg"}`}>{signed(v)}</span></div>)}
        </section>
      )}
      </div>
      </div>
      <div className="col-side">
      <section className="card" style={{ order: 1 }}>
        <h2>AI 판단</h2>
        <Meter label="매수 점수" v={r.buy_score} color="var(--up)" />
        <Meter label="매도 점수" v={r.sell_score} color="var(--down)" />
        <ul className="reasons">
          {r.reasons.map((x) => <li key={x}><span className="dot ok">✓</span><span>{x}</span></li>)}
          {r.warnings.map((x) => <li key={x}><span className="dot w">!</span><span>{x}</span></li>)}
        </ul>
      </section>

      {r.detail.extra.length > 0 && (
        <section className="card kv-list" style={{ order: 6 }}><h2>보조 정보</h2>{r.detail.extra.map((x) => <div className="kv" key={x}><span className="v" style={{ textAlign: "left" }}>{x}</span></div>)}</section>
      )}

      <div className="btn-row" style={{ marginTop: 12, order: 7 }}>
        {!confirming ? <button className="btn ghost" onClick={() => setConfirming(true)}>관심 종목에서 삭제</button>
          : <><button className="btn danger" onClick={remove}>정말 삭제할게요</button><button className="btn ghost" onClick={() => setConfirming(false)}>취소</button></>}
      </div>
      </div>
      </div>
      <p className="disclaimer">참고용 분석이며 투자 판단과 손실 책임은 본인에게 있습니다.</p>
    </div>
  );
}
