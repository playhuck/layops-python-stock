import { Fragment, useCallback, useEffect, useState } from "react";
import { addUsStock, fetchUsDiscover, fetchUsOverview, fetchUsSeries, fetchUsStocks, refreshUs, removeUsStock } from "../api";
import { num, pct, usd } from "../format";
import type { UsDiscoverHit, UsOverview, UsStock, UsStocks } from "../types";
import ChartCard from "./ChartCard";
import UsStrip from "./UsStrip";

const cls = (v: number | null | undefined) => (v == null ? "" : v >= 0 ? "pos" : "neg");
// 시총(달러) 구간: 소형 0.3~2B, 중형 2~10B
const CAPS: [string, number, number][] = [["소형+중형", 3e8, 1e10], ["소형", 3e8, 2e9], ["중형", 2e9, 1e10]];

export default function UsPage({ onToast }: { onToast: (m: string) => void }) {
  const [ov, setOv] = useState<UsOverview | null>(null);
  const [st, setSt] = useState<UsStocks | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [finding, setFinding] = useState(false);

  const load = useCallback(() => {
    fetchUsOverview().then(setOv).catch((e) => setErr(String(e)));
    fetchUsStocks().then((s) => { setSt(s); setErr(null); }).catch((e) => setErr(String(e)));
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
      {finding && <Discover onAdded={(sym) => { onToast(`${sym}을(를) 추가했어요`); load(); }} />}

      <section className="card">
        <h2 style={{ margin: "0 0 4px" }}>스윙 후보 <small>({st.stocks.length}/{st.max})</small></h2>
        {st.stocks.length === 0 ? <p className="empty">관심 종목이 없어요. "후보 찾기"로 나스닥 소형·중형주를 추가해 보세요.</p> : (
          <div className="tbl-wrap">
            <table className="tbl">
              <thead><tr><th>종목</th><th>종가</th><th>1일</th><th>5일</th><th>20일</th><th>60일</th><th title="20일 수익률 − 나스닥 종합">RS(나스닥)</th><th title="20일 수익률 − 러셀2000 ETF(IWM)">RS(소형)</th>
                <th>RSI</th><th title="ATR ÷ 종가">변동성</th><th title="오늘 거래량 ÷ 직전 20일 평균">거래량비</th><th title="최근 20일 평균 거래대금">거래대금</th><th>52주 고점비</th><th>실적</th><th>주의</th></tr></thead>
              <tbody>
                {st.stocks.map((s) => (
                  <Fragment key={s.symbol}>
                    <tr onClick={() => setOpen(open === s.symbol ? null : s.symbol)} style={{ cursor: "pointer" }} className={open === s.symbol ? "sel" : undefined}>
                      <td><b>{s.symbol}</b> <small className="muted">{s.name}</small></td>
                      {s.pending ? <td colSpan={14} className="muted" style={{ textAlign: "left" }}>시세를 아직 받지 못했어요. "미국 갱신"을 눌러 보세요</td> : <>
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
            <Detail s={sel} onRemove={() => remove(sel)} />
            {!sel.pending && <ChartCard code={sel.symbol} source={fetchUsSeries} />}
          </div>
        )}
        <small>사실값만 보여줘요. "매수 적기" 같은 판정은 검증 전이라 만들지 않았어요. 등락 색은 한국 화면과 같게 빨강=상승, 파랑=하락이에요. 종가는 완성된 마지막 거래일 기준(달러)이고, 실적일·공매도·시총은 야후 정보라 틀릴 수 있어요.</small>
      </section>
    </div>
  );
}

function Detail({ s, onRemove }: { s: UsStock; onRemove: () => void }) {
  return (
    <div style={{ padding: "6px 0 10px" }}>
      <div className="toolbar" style={{ marginBottom: 6 }}>
        <small className="muted">{[s.sector, s.industry].filter(Boolean).join(" · ") || "업종 정보 없음"} · 시총 {usd(s.market_cap)} · 베타 {num(s.beta, 2)} · 공매도 {s.short_pct_float == null ? "-" : `${s.short_pct_float.toFixed(1)}%`} · 최근 60일 최대 갭 {pct(s.max_gap_60d)}{s.next_earnings ? ` · 실적 ${s.next_earnings}` : ""}{s.meta_updated_at ? ` · 종목정보 ${s.meta_updated_at.slice(5, 16).replace("T", " ")} 기준` : " · 종목정보 아직 없음"}</small>
        <button className="add" onClick={onRemove}>목록에서 빼기</button>
      </div>
      {s.flags.map((f) => <p key={f} className="notice" style={{ margin: "0 0 6px" }}>⚠ {f}</p>)}
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
