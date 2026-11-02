import { Fragment, useCallback, useEffect, useState } from "react";
import { addUsStock, deleteUsPosition, fetchUsDiscover, fetchUsJudgment, fetchUsOverview, fetchUsProfile, fetchUsSeries, fetchUsStocks, putUsPosition, refreshUs, removeUsStock } from "../api";
import { num, pct, usd } from "../format";
import type { UsDiscoverHit, UsJudgment, UsJudgmentStock, UsOverview, UsReview, UsStock, UsStocks, UsVerdict } from "../types";
import ChartCard from "./ChartCard";
import Evidence from "./Evidence";
import UsStrip from "./UsStrip";

const cls = (v: number | null | undefined) => (v == null ? "" : v >= 0 ? "pos" : "neg");
// 시총(달러) 구간: 소형 0.3~2B, 중형 2~10B
const CAPS: [string, number, number][] = [["소형+중형", 3e8, 1e10], ["소형", 3e8, 2e9], ["중형", 2e9, 1e10]];
// 판정 → 칩 색: 매수 검토=빨강, 매도 검토=파랑, 주의·실적일 확인 필요=주황, 나머지는 회색 (한국 화면과 같은 색 약속)
const CHIP: Record<string, string> = { review: "buy", review_check: "split", sell: "sell", caution: "split" };
const Verdict = ({ v }: { v: UsVerdict }) => <span className={`chip ${CHIP[v.code] ?? "wait"}`}>{v.label}</span>;
const px2 = (n: number | null | undefined) => (n == null ? "-" : n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));

export default function UsPage({ onToast }: { onToast: (m: string) => void }) {
  const [ov, setOv] = useState<UsOverview | null>(null);
  const [st, setSt] = useState<UsStocks | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [finding, setFinding] = useState(false);
  const [jd, setJd] = useState<UsJudgment | null>(null);
  const [jdErr, setJdErr] = useState(false);

  const load = useCallback(() => {
    fetchUsOverview().then(setOv).catch((e) => setErr(String(e)));
    fetchUsStocks().then((s) => { setSt(s); setErr(null); }).catch((e) => setErr(String(e)));
    fetchUsJudgment().then((j) => { setJd(j); setJdErr(false); }).catch(() => setJdErr(true));     // 판단이 실패해도 표는 그대로 보여준다
  }, []);
  useEffect(load, [load]);
  const running = st?.status.running ?? false;
  useEffect(() => {      // 갱신 중에는 2초마다 다시 읽는다
    if (!running) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [running, load]);

  if (err && !st) return <p className="notice err">미국 데이터를 불러오지 못했어요: {err}</p>;
  if (!st || !ov) return <p className="empty">불러오는 중…</p>;

  const failed = st.status.steps.filter((s) => !s.ok);
  const sel = st.stocks.find((s) => s.symbol === open);
  const jmap = new Map((jd?.stocks ?? []).map((j) => [j.symbol, j]));
  const refresh = async () => { const r = await refreshUs(); onToast(r.started ? "미국 데이터를 갱신하고 있어요" : "이미 갱신 중이에요"); load(); };
  const remove = async (s: UsStock) => {
    try { await removeUsStock(s.symbol); setOpen(null); load(); onToast(`${s.symbol}을(를) 뺐어요`); } catch (e) { onToast((e as Error).message); }
  };

  return (
    <div>
      <div className="toolbar">
        <small className="muted">{running ? "미국 데이터 갱신 중…" : st.status.last_run ? `미국 ${st.status.last_run.slice(5, 16).replace("-", "/")} 갱신 · 매일 07시(한국시간)` : "아직 갱신 전이에요 · 매일 07시(한국시간)"}</small>
        <span><button className="add" onClick={() => setFinding((v) => !v)}>{finding ? "후보 닫기" : "후보 찾기"}</button> <button className="add" onClick={refresh} disabled={running}>미국 갱신</button></span>
      </div>
      {failed.slice(0, 3).map((s) => <p key={s.name} className="notice">⚠ {s.name}: {s.error}</p>)}
      {failed.length > 3 && <p className="notice">⚠ 그 밖에 {failed.length - 3}건 실패</p>}

      <UsStrip data={ov} full />
      <Holdings jd={jd} jdErr={jdErr} onChanged={load} onToast={onToast} />
      {finding && <Discover onAdded={(sym) => { onToast(`${sym}을(를) 추가했어요`); load(); }} />}

      <section className="card">
        <h2 style={{ margin: "0 0 4px" }}>스윙 후보 <small>({st.stocks.length}/{st.max})</small></h2>
        <AddTicker disabled={st.stocks.length >= st.max} onAdded={(sym) => { onToast(`${sym}을(를) 추가했어요`); load(); }} onToast={onToast} />
        {st.stocks.length === 0 ? <p className="empty">관심 종목이 없어요. "후보 찾기"로 나스닥 소형·중형주를 추가해 보세요.</p> : (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr><th>종목</th><th title="매수 판단 (검증 전 규칙, 참고용)">판단</th><th>종가</th><th>1일</th><th>5일</th><th>20일</th><th>60일</th><th title="20일 수익률 − 나스닥 종합">RS(나스닥)</th><th title="20일 수익률 − 러셀2000 ETF(IWM)">RS(소형)</th>
                <th>RSI</th><th title="ATR ÷ 종가">변동성</th><th title="오늘 거래량 ÷ 직전 20일 평균">거래량비</th><th title="최근 20일 평균 거래대금">거래대금</th><th>52주 고점비</th><th>실적</th><th>주의</th></tr></thead>
              <tbody>
                {st.stocks.map((s) => (
                  <Fragment key={s.symbol}>
                    <tr onClick={() => setOpen(open === s.symbol ? null : s.symbol)} style={{ cursor: "pointer" }} className={open === s.symbol ? "sel" : undefined}>
                      <td><b>{s.symbol}</b> <small className="muted">{s.name}</small></td>
                      {s.pending ? <td colSpan={15} className="muted" style={{ textAlign: "left" }}>시세를 아직 받지 못했어요. "미국 갱신"을 눌러 보세요</td> : <>
                        <td>{jmap.get(s.symbol) ? <Verdict v={jmap.get(s.symbol)!.buy.verdict} /> : <span className="muted">-</span>}</td>
                        <td>{num(s.close, 2)}</td><td className={cls(s.chg_1d)}>{pct(s.chg_1d)}</td><td className={cls(s.chg_5d)}>{pct(s.chg_5d)}</td>
                        <td className={cls(s.chg_20d)}>{pct(s.chg_20d)}</td><td className={cls(s.chg_60d)}>{pct(s.chg_60d)}</td>
                        <td className={cls(s.rs20_nasdaq)}>{pct(s.rs20_nasdaq)}</td><td className={cls(s.rs20_small)}>{pct(s.rs20_small)}</td>
                        <td>{num(s.rsi, 0)}</td><td>{pct(s.atr_pct)}</td><td>{s.vol_ratio == null ? "-" : `${s.vol_ratio.toFixed(1)}배`}</td><td>{usd(s.dollar_vol_20d)}</td>
                        <td className={cls(s.from_high52_pct)}>{pct(s.from_high52_pct)}</td>
                        <td>{s.days_to_earnings == null ? "-" : `D-${s.days_to_earnings}`}</td>
                        <td>{s.flags.length ? <span className="warn">⚠ {s.flags.length}</span> : <span className="muted">-</span>}</td></>}
                    </tr>
                  </Fragment>))}
              </tbody>
            </table>
          </div>
        )}
        {sel && (      // 표가 가로로 길어 행 안에 펼치면 차트가 잘려서, 선택한 종목의 상세는 표 아래에 둔다
          <div style={{ marginTop: 14, borderTop: "1px solid var(--line)", paddingTop: 12 }}>
            <h3 style={{ margin: "0 0 4px" }}>{sel.symbol} <small className="muted">{sel.name}</small></h3>
            <Detail s={sel} review={jmap.get(sel.symbol)?.buy} onRemove={() => remove(sel)} />
            {!sel.pending && <ChartCard code={sel.symbol} source={fetchUsSeries} profile={fetchUsProfile} />}
          </div>
        )}
        <small>표의 수치는 사실값이고, "판단"은 검증 전 규칙으로 계산한 참고용이에요(주문은 없어요). 등락 색은 한국 화면과 같게 빨강=상승, 파랑=하락이에요. 종가는 완성된 마지막 거래일 기준(달러)이고, 실적일·공매도·시총은 야후 정보라 틀릴 수 있어요.</small>
      </section>
    </div>
  );
}

function Detail({ s, review, onRemove }: { s: UsStock; review?: UsReview; onRemove: () => void }) {
  return (
    <div style={{ padding: "6px 0 10px" }}>
      <div className="toolbar" style={{ marginBottom: 6 }}>
        <small className="muted">{[s.sector, s.industry].filter(Boolean).join(" · ") || "업종 정보 없음"} · 시총 {usd(s.market_cap)} · 베타 {num(s.beta, 2)} · 공매도 {s.short_pct_float == null ? "-" : `${s.short_pct_float.toFixed(1)}%`} · 최근 60일 최대 갭 {pct(s.max_gap_60d)}{s.next_earnings ? ` · 실적 ${s.next_earnings}` : ""}{s.meta_updated_at ? ` · 종목정보 ${s.meta_updated_at.slice(5, 16).replace("T", " ")} 기준` : " · 종목정보 아직 없음"}</small>
        <button className="add" onClick={onRemove}>목록에서 빼기</button>
      </div>
      {s.flags.map((f) => <p key={f} className="notice" style={{ margin: "0 0 6px" }}>⚠ {f}</p>)}
      {review && (
        <div style={{ margin: "6px 0 10px" }}>
          <div><Verdict v={review.verdict} /> <small className="muted">{review.verdict.reason ?? ""} {review.as_of ? `· ${review.as_of} 종가 기준` : ""}</small></div>
          <Evidence items={review.evidence} />
        </div>
      )}
    </div>
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
    <section className="card">
      <div className="toolbar">
        <h2 style={{ margin: 0 }}>후보 찾기 <small>(나스닥 · 평균 거래량 100만주 이상 · 주가 $5 이상 · 거래량 큰 순)</small></h2>
        <div className="seg" style={{ minWidth: 220 }}>{CAPS.map(([l], i) => <button key={l} className={cap === i ? "on" : ""} onClick={() => setCap(i)}>{l}</button>)}</div>
      </div>
      {err && <p className="warn">{err}</p>}
      {!hits && !err && <p className="empty">불러오는 중…</p>}
      {hits && hits.length === 0 && <p className="empty">조건에 맞는 종목이 없어요</p>}
      {hits && hits.length > 0 && (
        <div className="tbl-wrap" style={{ maxHeight: 360, overflowY: "auto" }}>
          <table className="tbl">
            <thead><tr><th>종목</th><th>시총</th><th>주가</th><th>평균 거래량</th><th /></tr></thead>
            <tbody>{hits.map((h) => (
              <tr key={h.symbol}><td><b>{h.symbol}</b> <small className="muted">{h.name}</small></td><td>{usd(h.market_cap)}</td><td>${num(h.price, 2)}</td>
                <td>{h.avg_volume == null ? "-" : `${(h.avg_volume / 1e6).toFixed(1)}M주`}</td>
                <td><button className="add" disabled={h.added || busy !== null} onClick={() => add(h)}>{h.added ? "추가됨" : "추가"}</button></td></tr>))}</tbody>
          </table>
        </div>)}
      <small>야후 스크리너 결과예요. 거래량 큰 순이라 변동성이 큰 종목이 많아요. 추가 전에 실적일·갭 위험을 꼭 확인하세요.</small>
    </section>
  );
}

const EMPTY = { symbol: "", qty: "", avg: "", date: "", note: "", atr: "" };
const ATR_SRC = { opened: "매수일 직전 종가 기준", registered: "등록 시점 기준", manual: "직접 입력" };

/** 직접 입력한 보유 종목과 매도 검토. 주문은 없고, 저장한 값은 판단의 입력으로만 쓰인다 */
function Holdings({ jd, jdErr, onChanged, onToast }: { jd: UsJudgment | null; jdErr: boolean; onChanged: () => void; onToast: (m: string) => void }) {
  const [f, setF] = useState(EMPTY);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [del, setDel] = useState<string | null>(null);
  const held = (jd?.stocks ?? []).filter((s): s is UsJudgmentStock & { position: NonNullable<UsJudgmentStock["position"]>; sell: UsReview } => !!s.position && !!s.sell);
  const set = (k: keyof typeof EMPTY) => (e: { target: { value: string } }) => setF((cur) => ({ ...cur, [k]: e.target.value }));
  const submit = async (e: { preventDefault: () => void }) => {
    e.preventDefault();
    const symbol = f.symbol.trim().toUpperCase();
    setBusy(true);
    try {
      await putUsPosition(symbol, { qty: Number(f.qty), avg_price: Number(f.avg), opened_on: f.date || null, note: f.note, entry_atr: f.atr.trim() === "" ? null : Number(f.atr) });
      setF(EMPTY); onToast(`${symbol} 보유를 저장했어요`); onChanged();
    } catch (err) { onToast((err as Error).message); }
    setBusy(false);
  };
  const edit = (p: UsJudgmentStock["position"]) => p && setF({ symbol: p.symbol, qty: String(p.qty), avg: String(p.avg_price), date: p.opened_on ?? "", note: p.note, atr: p.entry_atr_source === "manual" && p.entry_atr != null ? String(p.entry_atr) : "" });
  const remove = async (symbol: string) => {
    if (del !== symbol) { setDel(symbol); setTimeout(() => setDel((cur) => (cur === symbol ? null : cur)), 3000); return; }   // 두 번 눌러야 지운다
    try { await deleteUsPosition(symbol); setDel(null); onToast(`${symbol} 보유 등록을 지웠어요`); onChanged(); } catch (err) { onToast((err as Error).message); }
  };
  return (
    <section className="card">
      <h2 style={{ margin: "0 0 4px" }}>내 보유 종목 <small>(매도 검토)</small></h2>
      <p className="us-note">직접 입력한 종목만 판단해요. 주문은 나가지 않고, 매수·매도는 직접 하세요. 평가손익은 달러 기준이고 수수료·세금·환율·배당은 빠져 있어요. 액면분할·역분할이 있으면 수량과 평균 매수가를 직접 고쳐 주세요. {jd?.note ?? ""}</p>
      <form className="us-form" onSubmit={submit}>
        <label>티커<input className="input sm" value={f.symbol} onChange={set("symbol")} placeholder="SOUN" maxLength={5} required /></label>
        <label>수량<input className="input sm" value={f.qty} onChange={set("qty")} placeholder="10" inputMode="decimal" required /></label>
        <label>평균 매수가($)<input className="input sm" value={f.avg} onChange={set("avg")} placeholder="11.20" inputMode="decimal" required /></label>
        <label>매수일(선택)<input className="input sm wide" type="date" value={f.date} onChange={set("date")} /></label>
        <label title="비우면 매수일 직전(없으면 지금) 시세로 자동 계산해요. 손절·목표·트레일링은 이 값으로 고정돼요">진입 ATR($, 선택)<input className="input sm" value={f.atr} onChange={set("atr")} placeholder="자동" inputMode="decimal" /></label>
        <label>메모(선택)<input className="input sm wide" value={f.note} onChange={set("note")} maxLength={200} /></label>
        <button className="add" type="submit" disabled={busy}>{busy ? "저장 중…" : "저장"}</button>
        {f.symbol && <button className="add" type="button" onClick={() => setF(EMPTY)}>입력 비우기</button>}
      </form>
      {jdErr && <p className="warn">판단을 불러오지 못했어요. 잠시 후 다시 시도해 주세요</p>}
      {!jd && !jdErr && <p className="empty">불러오는 중…</p>}
      {jd && held.length === 0 && <p className="empty">등록한 보유 종목이 없어요. 위에서 티커·수량·평균 매수가를 입력해 보세요. 관심 종목에 없으면 자동으로 추가돼요.</p>}
      {held.map((s) => {
        const r = s.sell, p = s.position, l = r.levels ?? {};
        return (
          <div className="us-hold" key={s.symbol}>
            <div className="row" onClick={() => setOpen(open === s.symbol ? null : s.symbol)}>
              <b>{s.symbol}</b> <small className="muted">{s.name}</small> <Verdict v={r.verdict} />
              <span className="sub">{p.qty}주 @ {px2(p.avg_price)} · 현재 {px2(r.price)} · <span className={cls(r.pnl_pct)}>{pct(r.pnl_pct)}</span>{p.entry_atr != null && <> · 진입 ATR ${px2(p.entry_atr)}{p.entry_atr_source ? ` (${ATR_SRC[p.entry_atr_source]})` : ""}</>}</span>
              <span className="btns" onClick={(e) => e.stopPropagation()}>
                <button className="add" onClick={() => edit(p)}>수정</button>
                <button className="add" onClick={() => remove(s.symbol)}>{del === s.symbol ? "정말 지우기" : "삭제"}</button>
              </span>
            </div>
            {r.verdict.reason && <p className="us-reason">{r.verdict.reason}</p>}
            {r.warnings.map((w) => <p key={w} className="notice" style={{ margin: "6px 0 0" }}>⚠ {w}</p>)}
            {open === s.symbol && (
              <>
                <Evidence items={r.evidence} hit />
                <p className="us-levels">{r.as_of ? `${r.as_of} 종가 기준 · ` : ""}손절선 {px2(l.stop)} · 트레일링 {px2(l.trail)}(진입 이후 최고 종가 {px2(l.high)}) · 목표 {px2(l.target)} · 20일선 {px2(l.ma20)}</p>
              </>
            )}
          </div>
        );
      })}
    </section>
  );
}

/** 후보 찾기에 안 나오는 종목(거래량 순위 밖 등)을 티커로 직접 추가: 서버가 야후에서 시세가 오는 티커인지 확인한 뒤 추가한다 */
function AddTicker({ disabled, onAdded, onToast }: { disabled: boolean; onAdded: (symbol: string) => void; onToast: (m: string) => void }) {
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
    <form className="us-form" onSubmit={submit} style={{ margin: "6px 0 10px" }}>
      <label>티커로 추가<input className="input sm" value={sym} onChange={(e) => setSym(e.target.value)} placeholder="SYM" maxLength={5} aria-label="추가할 티커" /></label>
      <button className="add" type="submit" disabled={busy || disabled || !sym.trim()}>{busy ? "찾는 중…" : "찾아서 추가"}</button>
      <small className="muted">후보 찾기에 안 나오는 종목도 티커를 입력하면 야후에서 찾아 추가해요 (시세가 오는 티커만 추가돼요)</small>
    </form>
  );
}
