import { useEffect, useState } from "react";
import { deleteUsPosition, fetchUsJudgment, fetchUsProfile, fetchUsSeries, fetchUsStocks, putUsPosition, removeUsStock } from "../api";
import { num, pct, quoteTime, usd } from "../format";
import type { HoldingsSource, HoldingsView, Quote, UsJudgmentStock, UsStock } from "../types";
import ChartCard from "./ChartCard";
import Evidence from "./Evidence";
import { SourceToggle } from "./Holdings";
import UsHoldingForm, { HOLD_EMPTY, holdBody, holdFormFrom, type HoldForm } from "./UsHoldingForm";
import { AddOpinionPanel, cls, krwBreakdown, px2, Verdict } from "./UsShared";

/** 미국 종목 상세(홈 > 미국 탭에서 연다): 지표 요약, 매수 검토 근거, 내 보유 한 줄, 차트(내 평단선) */
export default function UsStockPage({ symbol, quote, holdings, onSource, onHoldings, onBack, onToast, onRemoved }: { symbol: string; quote?: Quote; holdings: HoldingsView | null; onSource: (s: HoldingsSource) => void; onHoldings: () => void; onBack: () => void; onToast: (m: string) => void; onRemoved: () => void }) {
  const [s, setS] = useState<UsStock | null | undefined>(undefined);     // undefined = 불러오는 중, null = 관심 종목에 없음
  const [j, setJ] = useState<UsJudgmentStock | undefined>();
  const [err, setErr] = useState<string | null>(null);
  const [ver, setVer] = useState(0);                                       // 보유를 저장·삭제하면 올려서 판단·원화 손익을 다시 읽는다
  useEffect(() => {
    let stale = false;
    fetchUsStocks().then((r) => { if (!stale) setS(r.stocks.find((x) => x.symbol === symbol) ?? null); }).catch((e) => { if (!stale) setErr(String(e)); });
    fetchUsJudgment().then((r) => { if (!stale) setJ(r.stocks.find((x) => x.symbol === symbol)); }).catch(() => undefined);
    return () => { stale = true; };
  }, [symbol, ver]);
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
        <div className="big">{s.pending ? "-" : `$${px2(quote?.price ?? s.close)}`}</div>
        {!s.pending && <small className="muted">{quote ? `${quoteTime(quote.as_of)} 기준 · 판단은 전일 종가 $${px2(s.close)} 기준` : "전일 종가 기준"}</small>}
        {review && <Verdict v={review.verdict} />}
        {review?.verdict.reason && <small className="muted" style={{ marginLeft: 8 }}>{review.verdict.reason}</small>}
      </div>
      <div className="cols">
        <div className="col-main">
          <div style={{ order: 1 }}><UsMyHolding symbol={symbol} mine={j} source={holdings?.source ?? "manual"} onSource={onSource} onChanged={() => { setVer((v) => v + 1); onHoldings(); }} onToast={onToast} /></div>
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

const fxText = (n: number | null | undefined) => (n == null ? "-" : n.toLocaleString("ko-KR", { maximumFractionDigits: 1 }));

/** 종목 상세의 '내 보유': 직접 입력한 보유를 보고, 이 화면에서 바로 입력·수정·삭제한다(저장하면 판단과 원화 손익이 다시 계산된다). 주문은 나가지 않는다 */
function UsMyHolding({ symbol, mine, source, onSource, onChanged, onToast }: { symbol: string; mine?: UsJudgmentStock; source: HoldingsSource; onSource: (s: HoldingsSource) => void; onChanged: () => void; onToast: (m: string) => void }) {
  const [f, setF] = useState<HoldForm>(HOLD_EMPTY);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [del, setDel] = useState(false);
  const [adviceOpen, setAdviceOpen] = useState(false);
  const p = mine?.position, sell = mine?.sell, krw = mine?.krw, advice = mine?.add;
  const start = () => { setF(p ? holdFormFrom(p) : { ...HOLD_EMPTY, symbol }); setEditing(true); };
  const save = async (e: { preventDefault: () => void }) => {
    e.preventDefault(); setBusy(true);
    try { await putUsPosition(symbol, holdBody(f)); setEditing(false); onToast("보유를 저장했어요"); onChanged(); }
    catch (err) { onToast((err as Error).message); }
    setBusy(false);
  };
  const remove = async () => {
    if (!del) { setDel(true); setTimeout(() => setDel(false), 3000); return; }          // 두 번 눌러야 지운다
    try { await deleteUsPosition(symbol); setDel(false); onToast("보유 기록을 지웠어요"); onChanged(); } catch (err) { onToast((err as Error).message); }
  };
  return (
    <section className="card">
      <div className="toolbar"><h2 style={{ margin: 0 }}>내 보유 {source === "manual" && advice && <button className="add" style={{ marginLeft: 8, fontSize: 12 }} onClick={() => setAdviceOpen((v) => !v)} aria-expanded={adviceOpen} title="물타기·불타기 의견과 이유">제안Plus+</button>}</h2><SourceToggle source={source} onChange={onSource} /></div>
      {source === "import" ? <p className="empty" style={{ margin: "8px 0 0" }}>미국 종목은 토스에서 가져오기를 지원하지 않아요. 직접 입력한 보유를 보려면 위에서 "직접 입력"을 선택하세요.</p> : <>
        {p && sell && (
          <>
            <div className="hold-sum">
              <div><span>보유량</span><b>{p.qty}주</b></div>
              <div><span>평균 매수가</span><b>${px2(p.avg_price)}</b></div>
              <div><span>평가손익</span><b className={cls(sell.pnl_pct)}>{pct(sell.pnl_pct)} · {sell.pnl_value != null && `${sell.pnl_value >= 0 ? "+" : "-"}$${Math.abs(sell.pnl_value).toLocaleString("en-US", { maximumFractionDigits: 0 })}`}</b></div>
              <div><span>매도 검토</span><b><Verdict v={sell.verdict} /></b></div>
              {krw?.available && <div><span>원화 평가손익</span><b className={cls(krw.pnl_pct)}>{pct(krw.pnl_pct)} · {krw.pnl_krw! >= 0 ? "+" : "-"}{Math.abs(Math.round(krw.pnl_krw!)).toLocaleString("ko-KR")}원</b></div>}
              {krw?.available && <div><span>환율(매수 → 현재)</span><b>{fxText(krw.fx_buy)} → {fxText(krw.fx_now)}원</b></div>}
            </div>
            {krw && <p className="us-levels" style={{ margin: "0 0 6px" }}>{krw.available ? krwBreakdown(krw) : `원화 손익: ${krw.reason}`}</p>}
            <small className="muted">{sell.verdict.reason ?? ""}{p.entry_atr != null ? ` · 진입 ATR $${px2(p.entry_atr)}` : ""}{p.opened_on ? ` · 매수일 ${p.opened_on}` : ""}{p.note ? ` · ${p.note}` : ""} · 차트의 주황 점선이 내 평단이에요</small>
          </>
        )}
        {adviceOpen && advice && <AddOpinionPanel a={advice} />}
        {!editing && (
          <div style={{ marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            {!p && <small className="muted">직접 입력한 보유가 없어요.</small>}
            <button className="add" onClick={start}>{p ? "수정" : "보유 입력"}</button>
            {p && <button className="add" onClick={remove}>{del ? "정말 지우기" : "삭제"}</button>}
          </div>
        )}
        {editing && (
          <div style={{ marginTop: 10 }}>
            <UsHoldingForm f={f} setF={setF} busy={busy} lockSymbol onSubmit={save}><button className="add" type="button" onClick={() => setEditing(false)}>취소</button></UsHoldingForm>
          </div>
        )}
        <small className="muted" style={{ display: "block", marginTop: 8 }}>직접 입력한 값으로 매도 검토를 계산해요. 주문은 나가지 않아요. 액면분할·역분할이 있으면 수량과 평균 매수가를 고쳐 주세요.</small>
      </>}
    </section>
  );
}
