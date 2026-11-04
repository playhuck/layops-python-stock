import { useCallback, useEffect, useState } from "react";
import { fetchUsJudgment, fetchUsStocks, refreshUs } from "../api";
import { num, pct, quoteTime, usd } from "../format";
import type { HoldingsView, Quote, UsJudgment, UsStocks } from "../types";
import { cls, Verdict } from "./UsShared";

/** 홈 '미국' 탭의 관심 종목 표. 행을 누르면 종목 상세로 간다. 보유 배지는 '직접 입력' 모드에서만 보인다(가져오기 모드와 섞이지 않게) */
export default function UsWatchTable({ quotes, onQuotes, holdings, version, onOpen, onAdd, onToast }: { quotes?: Record<string, Quote>; onQuotes: (fresh?: boolean) => void; holdings: HoldingsView | null; version: number; onOpen: (symbol: string) => void; onAdd: () => void; onToast: (m: string) => void }) {
  const [st, setSt] = useState<UsStocks | null>(null);
  const [jd, setJd] = useState<UsJudgment | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => {
    fetchUsStocks().then((s) => { setSt(s); setErr(null); }).catch((e) => setErr(String(e)));
    fetchUsJudgment().then(setJd).catch(() => setJd(null));          // 판단이 실패해도 표는 그대로 보여준다
  }, []);
  useEffect(load, [load, version]);
  const running = st?.status.running ?? false;
  useEffect(() => {
    if (!running) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [running, load]);

  if (err && !st) return <p className="notice err">미국 데이터를 불러오지 못했어요: {err}</p>;
  if (!st) return <p className="empty">불러오는 중…</p>;
  const failed = st.status.steps.filter((s) => !s.ok);
  const jmap = new Map((jd?.stocks ?? []).map((j) => [j.symbol, j]));
  const mineOn = (holdings?.source ?? "manual") === "manual";
  const refresh = async () => { const r = await refreshUs(); onToast(r.started ? "미국 데이터를 갱신하고 있어요" : "이미 갱신 중이에요"); load(); onQuotes(true); };
  return (
    <section className="card list" style={{ padding: "12px 12px 8px" }}>
      <div className="toolbar" style={{ padding: "0 8px" }}>
        <small className="muted">{running ? "미국 데이터 갱신 중…" : st.status.last_run ? `미국 ${st.status.last_run.slice(5, 16).replace("-", "/")} 갱신 · 매일 07시(한국시간)` : "아직 갱신 전이에요 · 매일 07시(한국시간)"}</small>
        <button className="add" onClick={refresh} disabled={running}>미국 갱신</button>
      </div>
      {failed.slice(0, 3).map((s) => <p key={s.name} className="notice">⚠ {s.name}: {s.error}</p>)}
      {failed.length > 3 && <p className="notice">⚠ 그 밖에 {failed.length - 3}건 실패</p>}
      {st.stocks.length === 0 ? <p className="empty">미국 관심 종목이 없어요. 아래 "＋ 종목 추가"에서 티커로 추가하거나 후보를 찾아보세요.</p> : (
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr><th>종목</th><th title="매수 판단 (검증 전 규칙, 참고용)">판단</th><th title="마우스를 올리면 체결 시각. 판단·등락률은 전일 종가 기준">현재가</th><th>1일</th><th>5일</th><th>20일</th><th>60일</th><th title="20일 수익률 − 나스닥 종합">RS(나스닥)</th><th title="20일 수익률 − 러셀2000 ETF(IWM)">RS(소형)</th>
              <th>RSI</th><th title="ATR ÷ 종가">변동성</th><th title="오늘 거래량 ÷ 직전 20일 평균">거래량비</th><th title="최근 20일 평균 거래대금">거래대금</th><th>52주 고점비</th><th>실적</th><th>주의</th></tr></thead>
            <tbody>
              {st.stocks.map((s) => (
                <tr key={s.symbol} onClick={() => onOpen(s.symbol)} style={{ cursor: "pointer" }}>
                  <td><b>{s.symbol}</b> <small className="muted">{s.name}</small>{mineOn && jmap.get(s.symbol)?.position && <span className="chip held">보유</span>}</td>
                  {s.pending ? <td colSpan={15} className="muted" style={{ textAlign: "left" }}>시세를 아직 받지 못했어요. "미국 갱신"을 눌러 보세요</td> : <>
                    <td>{jmap.get(s.symbol) ? <Verdict v={jmap.get(s.symbol)!.buy.verdict} /> : <span className="muted">-</span>}</td>
                    <td title={quotes?.[s.symbol] ? `${quoteTime(quotes[s.symbol].as_of)} 기준 (전일 종가 ${num(s.close, 2)})` : "전일 종가 기준"}>{num(quotes?.[s.symbol]?.price ?? s.close, 2)}</td><td className={cls(s.chg_1d)}>{pct(s.chg_1d)}</td><td className={cls(s.chg_5d)}>{pct(s.chg_5d)}</td>
                    <td className={cls(s.chg_20d)}>{pct(s.chg_20d)}</td><td className={cls(s.chg_60d)}>{pct(s.chg_60d)}</td>
                    <td className={cls(s.rs20_nasdaq)}>{pct(s.rs20_nasdaq)}</td><td className={cls(s.rs20_small)}>{pct(s.rs20_small)}</td>
                    <td>{num(s.rsi, 0)}</td><td>{pct(s.atr_pct)}</td><td>{s.vol_ratio == null ? "-" : `${s.vol_ratio.toFixed(1)}배`}</td><td>{usd(s.dollar_vol_20d)}</td>
                    <td className={cls(s.from_high52_pct)}>{pct(s.from_high52_pct)}</td>
                    <td>{s.days_to_earnings == null ? "-" : `D-${s.days_to_earnings}`}</td>
                    <td>{s.flags.length ? <span className="warn">⚠ {s.flags.length}</span> : <span className="muted">-</span>}</td></>}
                </tr>))}
            </tbody>
          </table>
        </div>
      )}
      <button className="row add-row" onClick={onAdd}>＋ 종목 추가 <small className="muted">({st.stocks.length}/{st.max})</small></button>
      <small style={{ display: "block", padding: "0 8px 8px" }}>표의 수치는 사실값이고, "판단"은 검증 전 규칙으로 계산한 참고용이에요(주문은 없어요). 등락 색은 한국 화면과 같게 빨강=상승, 파랑=하락이에요. 종가는 완성된 마지막 거래일 기준(달러)이고, 실적일·공매도·시총은 야후 정보라 틀릴 수 있어요.</small>
    </section>
  );
}
