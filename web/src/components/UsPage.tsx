import { useCallback, useEffect, useState } from "react";
import { deleteUsPosition, fetchUsJudgment, fetchUsOverview, putUsPosition, refreshUs } from "../api";
import { pct } from "../format";
import type { HoldingsSource, HoldingsView, UsJudgment, UsJudgmentStock, UsOverview, UsReview } from "../types";
import Evidence from "./Evidence";
import { SourceToggle } from "./Holdings";
import UsHoldingForm, { HOLD_EMPTY, holdBody, holdFormFrom } from "./UsHoldingForm";
import { cls, KrwInline, krwBreakdown, px2, Verdict } from "./UsShared";
import UsStrip from "./UsStrip";

/** 미국 탭: 시장 지표와 내 보유 종목(매도 검토). 관심 종목 목록·추가·상세는 홈 > 미국 탭으로 옮겼다 */
export default function UsPage({ onToast, holdings, onSource, onHoldings, onOpenHome }: { onToast: (m: string) => void; holdings: HoldingsView | null; onSource: (s: HoldingsSource) => void; onHoldings: () => void; onOpenHome: () => void }) {
  const [ov, setOv] = useState<UsOverview | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [jd, setJd] = useState<UsJudgment | null>(null);
  const [jdErr, setJdErr] = useState(false);

  const load = useCallback(() => {
    fetchUsOverview().then((o) => { setOv(o); setErr(null); }).catch((e) => setErr(String(e)));
    fetchUsJudgment().then((j) => { setJd(j); setJdErr(false); }).catch(() => setJdErr(true));
  }, []);
  useEffect(load, [load]);
  const running = ov?.status.running ?? false;
  useEffect(() => {      // 갱신 중에는 2초마다 다시 읽는다
    if (!running) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [running, load]);

  if (err && !ov) return <p className="notice err">미국 데이터를 불러오지 못했어요: {err}</p>;
  if (!ov) return <p className="empty">불러오는 중…</p>;

  const failed = ov.status.steps.filter((s) => !s.ok);
  const source = holdings?.source ?? "manual";
  const refresh = async () => { const r = await refreshUs(); onToast(r.started ? "미국 데이터를 갱신하고 있어요" : "이미 갱신 중이에요"); load(); };

  return (
    <div>
      <div className="toolbar">
        <small className="muted">{running ? "미국 데이터 갱신 중…" : ov.status.last_run ? `미국 ${ov.status.last_run.slice(5, 16).replace("-", "/")} 갱신 · 매일 07시(한국시간)` : "아직 갱신 전이에요 · 매일 07시(한국시간)"}</small>
        <span><button className="add" onClick={onOpenHome}>관심 종목 보기(홈 · 미국)</button> <button className="add" onClick={refresh} disabled={running}>미국 갱신</button></span>
      </div>
      {failed.slice(0, 3).map((s) => <p key={s.name} className="notice">⚠ {s.name}: {s.error}</p>)}
      {failed.length > 3 && <p className="notice">⚠ 그 밖에 {failed.length - 3}건 실패</p>}

      <UsStrip data={ov} full />
      <Holdings jd={jd} jdErr={jdErr} source={source} onSource={onSource} onChanged={() => { load(); onHoldings(); }} onToast={onToast} />
      <p className="disclaimer">미국 관심 종목의 목록·추가·상세는 홈 &gt; 미국 탭으로 옮겼어요.</p>
    </div>
  );
}

const ATR_SRC = { opened: "매수일 직전 종가 기준", registered: "등록 시점 기준", manual: "직접 입력" };

/** 직접 입력한 보유 종목과 매도 검토. 주문은 없고, 저장한 값은 판단의 입력으로만 쓰인다 */
function Holdings({ jd, jdErr, source, onSource, onChanged, onToast }: { jd: UsJudgment | null; jdErr: boolean; source: HoldingsSource; onSource: (s: HoldingsSource) => void; onChanged: () => void; onToast: (m: string) => void }) {
  const [f, setF] = useState(HOLD_EMPTY);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [del, setDel] = useState<string | null>(null);
  const held = (jd?.stocks ?? []).filter((s): s is UsJudgmentStock & { position: NonNullable<UsJudgmentStock["position"]>; sell: UsReview } => !!s.position && !!s.sell);
  const submit = async (e: { preventDefault: () => void }) => {
    e.preventDefault();
    const symbol = f.symbol.trim().toUpperCase();
    setBusy(true);
    try {
      await putUsPosition(symbol, holdBody(f));
      setF(HOLD_EMPTY); onToast(`${symbol} 보유를 저장했어요`); onChanged();
    } catch (err) { onToast((err as Error).message); }
    setBusy(false);
  };
  const edit = (p: UsJudgmentStock["position"]) => p && setF(holdFormFrom(p));
  const remove = async (symbol: string) => {
    if (del !== symbol) { setDel(symbol); setTimeout(() => setDel((cur) => (cur === symbol ? null : cur)), 3000); return; }   // 두 번 눌러야 지운다
    try { await deleteUsPosition(symbol); setDel(null); onToast(`${symbol} 보유 등록을 지웠어요`); onChanged(); } catch (err) { onToast((err as Error).message); }
  };
  return (
    <section className="card">
      <div className="toolbar"><h2 style={{ margin: "0 0 4px" }}>내 보유 종목 <small>(매도 검토)</small></h2><SourceToggle source={source} onChange={onSource} /></div>
      {source === "import" ? <p className="empty">미국 종목은 토스에서 가져오기를 지원하지 않아요. 직접 입력한 보유를 보려면 위에서 "직접 입력"을 선택하세요.</p> : <>
      <p className="us-note">직접 입력한 종목만 판단해요. 주문은 나가지 않고, 매수·매도는 직접 하세요. 평가손익은 달러 기준이고(손절·목표 판단도 달러 기준), 매수 환율을 넣으면 원화 손익을 따로 보여줘요. 수수료·세금·배당은 빠져 있어요. 액면분할·역분할이 있으면 수량과 평균 매수가를 직접 고쳐 주세요. {jd?.note ?? ""}</p>
      <UsHoldingForm f={f} setF={setF} busy={busy} onSubmit={submit}>
        {f.symbol && <button className="add" type="button" onClick={() => setF(HOLD_EMPTY)}>입력 비우기</button>}
      </UsHoldingForm>
      {jdErr && <p className="warn">판단을 불러오지 못했어요. 잠시 후 다시 시도해 주세요</p>}
      {!jd && !jdErr && <p className="empty">불러오는 중…</p>}
      {jd && held.length === 0 && <p className="empty">등록한 보유 종목이 없어요. 위에서 티커·수량·평균 매수가를 입력해 보세요. 관심 종목에 없으면 자동으로 추가돼요.</p>}
      {held.map((s) => {
        const r = s.sell, p = s.position, l = r.levels ?? {};
        return (
          <div className="us-hold" key={s.symbol}>
            <div className="row" onClick={() => setOpen(open === s.symbol ? null : s.symbol)}>
              <b>{s.symbol}</b> <small className="muted">{s.name}</small> <Verdict v={r.verdict} />
              <span className="sub">{p.qty}주 @ {px2(p.avg_price)} · 현재 {px2(r.price)} · <span className={cls(r.pnl_pct)}>{pct(r.pnl_pct)}</span>{p.entry_atr != null && <> · 진입 ATR ${px2(p.entry_atr)}{p.entry_atr_source ? ` (${ATR_SRC[p.entry_atr_source]})` : ""}</>}<KrwInline k={s.krw} /></span>
              <span className="btns" onClick={(e) => e.stopPropagation()}>
                <button className="add" onClick={() => edit(p)}>수정</button>
                <button className="add" onClick={() => remove(s.symbol)}>{del === s.symbol ? "정말 지우기" : "삭제"}</button>
              </span>
            </div>
            {r.verdict.reason && <p className="us-reason">{r.verdict.reason}</p>}
            {r.warnings.map((w) => <p key={w} className="notice" style={{ margin: "6px 0 0" }}>⚠ {w}</p>)}
            {open === s.symbol && (
              <>
                {s.krw?.available && <p className="us-levels" style={{ marginTop: 6 }}>{krwBreakdown(s.krw)}</p>}
                <Evidence items={r.evidence} hit />
                <p className="us-levels">{r.as_of ? `${r.as_of} 종가 기준 · ` : ""}손절선 {px2(l.stop)} · 트레일링 {px2(l.trail)}(진입 이후 최고 종가 {px2(l.high)}) · 목표 {px2(l.target)} · 20일선 {px2(l.ma20)}</p>
              </>
            )}
          </div>
        );
      })}
      </>}
    </section>
  );
}
