import { useEffect, useState } from "react";
import { fetchUsJudgment, fetchUsProfile, fetchUsSeries, fetchUsStocks, removeUsStock } from "../api";
import { num, pct, usd } from "../format";
import type { HoldingsView, UsJudgmentStock, UsStock } from "../types";
import ChartCard from "./ChartCard";
import Evidence from "./Evidence";
import { cls, krwBreakdown, px2, Verdict } from "./UsShared";

/** 미국 종목 상세(홈 > 미국 탭에서 연다): 지표 요약, 매수 검토 근거, 내 보유 한 줄, 차트(내 평단선) */
export default function UsStockPage({ symbol, holdings, onBack, onToast, onRemoved }: { symbol: string; holdings: HoldingsView | null; onBack: () => void; onToast: (m: string) => void; onRemoved: () => void }) {
  const [s, setS] = useState<UsStock | null | undefined>(undefined);     // undefined = 불러오는 중, null = 관심 종목에 없음
  const [j, setJ] = useState<UsJudgmentStock | undefined>();
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let stale = false;
    fetchUsStocks().then((r) => { if (!stale) setS(r.stocks.find((x) => x.symbol === symbol) ?? null); }).catch((e) => { if (!stale) setErr(String(e)); });
    fetchUsJudgment().then((r) => { if (!stale) setJ(r.stocks.find((x) => x.symbol === symbol)); }).catch(() => undefined);
    return () => { stale = true; };
  }, [symbol]);
  const mineOn = (holdings?.source ?? "manual") === "manual";            // 직접 입력 모드에서만 내 보유를 보여준다
  const mine = mineOn ? j : undefined;
  const [confirming, setConfirming] = useState(false);
  const remove = async () => {
    try { await removeUsStock(symbol); onToast(`${symbol}을(를) 관심 종목에서 뺐어요`); onRemoved(); } catch (e) { onToast((e as Error).message); setConfirming(false); }
  };

  const top = <div className="top" style={{ paddingBottom: 0 }}><button className="back" onClick={onBack} aria-label="뒤로">← 관심 종목</button><span /></div>;
  if (err) return <div>{top}<p className="notice err">미국 종목 정보를 불러오지 못했어요: {err}</p></div>;
  if (s === undefined) return <div>{top}<p className="empty">불러오는 중…</p></div>;
  if (s === null) return <div>{top}<p className="empty">{symbol}은(는) 미국 관심 종목에 없어요</p></div>;
  const review = j?.buy;
  return (
    <div>
      {top}
      <div className="hero">
        <div className="nm">{s.name} · {s.symbol} <span className="chip mkt">미국</span></div>
        <div className="big">{s.pending ? "-" : `$${px2(s.close)}`}</div>
        {review && <Verdict v={review.verdict} />}
        {review?.verdict.reason && <small className="muted" style={{ marginLeft: 8 }}>{review.verdict.reason}</small>}
      </div>
      <div className="cols">
        <div className="col-main">
          {mine?.position && mine.sell && (
            <section className="card" style={{ order: 1 }}>
              <h2>내 보유</h2>
              <div className="hold-sum">
                <div><span>보유량</span><b>{mine.position.qty}주</b></div>
                <div><span>평균 매수가</span><b>${px2(mine.position.avg_price)}</b></div>
                <div><span>평가손익</span><b className={cls(mine.sell.pnl_pct)}>{pct(mine.sell.pnl_pct)} · {mine.sell.pnl_value != null && `${mine.sell.pnl_value >= 0 ? "+" : "-"}$${Math.abs(mine.sell.pnl_value).toLocaleString("en-US", { maximumFractionDigits: 0 })}`}</b></div>
                <div><span>매도 검토</span><b><Verdict v={mine.sell.verdict} /></b></div>
                {mine.krw?.available && <div><span>원화 평가손익</span><b className={cls(mine.krw.pnl_pct)}>{pct(mine.krw.pnl_pct)} · {mine.krw.pnl_krw! >= 0 ? "+" : "-"}{Math.abs(Math.round(mine.krw.pnl_krw!)).toLocaleString("ko-KR")}원</b></div>}
                {mine.krw?.available && <div><span>환율(매수 → 현재)</span><b>{mine.krw.fx_buy?.toLocaleString("ko-KR")} → {mine.krw.fx_now?.toLocaleString("ko-KR", { maximumFractionDigits: 1 })}원</b></div>}
              </div>
              {mine.krw && <p className="us-levels" style={{ margin: "0 0 6px" }}>{mine.krw.available ? krwBreakdown(mine.krw) : `원화 손익: ${mine.krw.reason}`}</p>}
              <small className="muted">{mine.sell.verdict.reason ?? ""} · 보유 입력·수정과 매도 근거는 미국 탭의 '내 보유 종목'에서 볼 수 있어요 · 차트의 주황 점선이 내 평단이에요</small>
            </section>
          )}
          {!s.pending && <div style={{ order: 2 }}><ChartCard code={s.symbol} source={fetchUsSeries} profile={fetchUsProfile} mine={mine?.position ? { avg: mine.position.avg_price } : undefined} /></div>}
          {review && (
            <section className="card" style={{ order: 3 }}>
              <h2>매수 검토 근거 <small className="muted">{review.as_of ? `${review.as_of} 종가 기준 · 검증 전 규칙, 참고용` : "검증 전 규칙, 참고용"}</small></h2>
              <Evidence items={review.evidence} />
            </section>
          )}
        </div>
        <aside className="col-side">
          <section className="card kv-list" style={{ order: 1 }}>
            <h2>종목 정보</h2>
            {([["업종", [s.sector, s.industry].filter(Boolean).join(" · ") || "정보 없음"], ["시총", usd(s.market_cap)], ["베타", num(s.beta, 2)],
               ["공매도(유통주식 대비)", s.short_pct_float == null ? "-" : `${s.short_pct_float.toFixed(1)}%`], ["최근 60일 최대 갭", pct(s.max_gap_60d)],
               ["실적일", s.next_earnings ?? "확인 안 됨"], ["20일 평균 거래대금", usd(s.dollar_vol_20d)], ["RSI", num(s.rsi, 0)], ["변동성(ATR/종가)", pct(s.atr_pct)],
               ["20일 · 60일 수익률", `${pct(s.chg_20d)} · ${pct(s.chg_60d)}`]] as [string, string][]).map(([k, v]) => <div className="kv" key={k}><span className="k">{k}</span><span className="v">{v}</span></div>)}
            <small className="muted">{s.meta_updated_at ? `종목정보 ${s.meta_updated_at.slice(5, 16).replace("T", " ")} 기준 · 야후 정보라 틀릴 수 있어요` : "종목정보를 아직 받지 못했어요"}</small>
          </section>
          {s.flags.length > 0 && <section className="card" style={{ order: 2 }}><h2>주의</h2>{s.flags.map((f) => <p key={f} className="notice" style={{ margin: "0 0 6px" }}>⚠ {f}</p>)}</section>}
          <div className="btn-row" style={{ order: 3 }}>
            {!confirming ? <button className="btn ghost" onClick={() => setConfirming(true)}>관심 종목에서 삭제</button>
              : <><button className="btn danger" onClick={remove}>정말 삭제할게요</button><button className="btn ghost" onClick={() => setConfirming(false)}>취소</button></>}
          </div>
        </aside>
      </div>
      <p className="disclaimer">참고용 분석이며 투자 판단과 손실 책임은 본인에게 있습니다. 미국 판단은 검증 전 규칙이고 주문 기능은 없어요.</p>
    </div>
  );
}
