import { useCallback, useEffect, useState } from "react";
import { decide, dismissUnreconciled, fetchCandidates, fetchTrading, pushTest, resetKill, resolveProposal, saveRule, setLive, syncOrders } from "../api";
import { avatarColor, signed, won } from "../format";
import { enablePush, listenForeground } from "../push";
import type { Candidate, Proposal, Rule, ScreenItem, TradingState } from "../types";
import Evidence from "./Evidence";

const STATUS: Record<string, string> = {
  pending: "대기", approving: "처리 중", simulated: "모의 처리", ordered: "주문 접수", filled: "체결",
  rejected: "거절", blocked: "차단", failed: "실패", expired: "만료", closed: "종료", needs_review: "확인 필요",
};

function ProposalCard({ p, live, onDone }: { p: Proposal; live: boolean; onDone: (m: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const isBuy = p.side === "BUY";
  const run = async (action: "approve" | "reject") => {
    setBusy(true);
    try {
      const r = await decide(p.id, action);
      onDone(action === "reject" ? "거절했어요" : r.mode === "dry-run" ? "모의로 처리했어요 (주문은 전송되지 않았어요)" : "주문을 접수했어요");
    } catch (e) { onDone(`⚠ ${(e as Error).message}`); }
    finally { setBusy(false); setConfirming(false); }
  };
  return (
    <article className={`card proposal ${isBuy ? "p-buy" : "p-sell"}`}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <div className="avatar" style={{ background: avatarColor(p.code) }}>{p.name[0]}</div>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 700, fontSize: 17 }}><span className={`side ${isBuy ? "buy" : "sell"}`}>{isBuy ? "매수" : "매도"}</span>{p.name}
            <span className={`mode ${live ? "mode-live" : "mode-dry"}`}>{live ? "실주문" : "모의"}</span></div>
          <small>{p.qty.toLocaleString()}주 × {won(p.price)}원 · 지정가</small>
        </div>
      </div>
      <div style={{ fontSize: 26, fontWeight: 700, margin: "14px 0 2px" }}>{won(p.qty * p.price)}원</div>
      {isBuy && p.stop && p.target && (
        <small>손절 {won(p.stop)} · 익절 {won(p.target)} · 손절 시 최대 {won(p.sizing?.max_loss ?? 0)}원 손실 · 갭하락 가정 시 {won(p.sizing?.gap_loss ?? 0)}원 · 수량 제한: {p.sizing?.binding}</small>
      )}
      <Evidence items={p.evidence} />
      <div className="actions">
        {!confirming ? (
          <button className={isBuy ? "b-buy" : "b-sell"} disabled={busy} onClick={() => (live ? setConfirming(true) : run("approve"))}>{isBuy ? "매수 승인" : "매도 승인"}</button>
        ) : (
          <button className="b-danger" disabled={busy} onClick={() => run("approve")}>실제 주문이 전송돼요 · 한 번 더 눌러 확정</button>
        )}
        <button className="b-ghost" disabled={busy} onClick={() => (confirming ? setConfirming(false) : run("reject"))}>{confirming ? "취소" : "거절"}</button>
      </div>
    </article>
  );
}

function ReviewCard({ p, onDone, onFail }: { p: Proposal; onDone: (m: string) => void; onFail: (e: unknown) => void }) {
  const [cands, setCands] = useState<Candidate[] | null>(null);
  const [pick, setPick] = useState("");
  useEffect(() => { fetchCandidates(p.id).then(setCands).catch(() => setCands([])); }, [p.id]);
  const act = async (r: "adopt" | "not_sent" | "unreconciled", msg: string) => {
    try { await resolveProposal(p.id, r, r === "adopt" ? pick : undefined); onDone(msg); } catch (e) { onFail(e); }
  };
  return (
    <article className="card proposal p-sell">
      <b>{p.side === "BUY" ? "매수" : "매도"} {p.name}</b> <small>{p.code} · 시도: {p.attempt_qty ?? "?"}주 @ {p.attempt_price ? won(p.attempt_price) : "?"}원 · {p.attempt_mode === "live" ? "실주문 시도" : p.attempt_mode === "unknown" ? "시도 내용 불명" : p.attempt_mode}</small>
      <p className="notice err" style={{ marginTop: 10 }}>승인 처리 중 서버가 멈춰서 주문이 토스로 나갔는지 확정하지 못했어요. 확인 전까지 이 종목은 새 제안이 막히고 한도·현금에 예약돼요.{p.side === "SELL" ? " 매도 전에 보호주문이 해제됐을 수 있어요." : ""}</p>
      <b style={{ fontSize: 14 }}>토스의 같은 종목 주문</b>
      {cands === null && <p className="empty" style={{ padding: 8 }}>불러오는 중…</p>}
      {cands !== null && cands.length === 0 && <p className="empty" style={{ padding: 8 }}>후보가 없어요 (시도 이후 같은 종목·방향의 주문이 토스에서 보이지 않아요)</p>}
      <ul className="evidence">{(cands ?? []).map((c) => (
        <li key={c.orderId} className={c.exact ? "ok" : "no"} style={{ cursor: "pointer" }} onClick={() => setPick(c.orderId)}>
          <span className="mark">{pick === c.orderId ? "●" : "○"}</span>
          <span className="lbl">{c.quantity}주 @ {won(c.price)}원 · {c.status ?? "?"}{c.exact ? " · 시도와 수량·가격 일치" : ""}</span>
          <span className="val">{c.orderedAt.slice(5, 16).replace("T", " ")}</span>
        </li>))}</ul>
      <div className="actions">
        <button className="b-buy" disabled={!pick} onClick={() => act("adopt", "주문을 이어받았어요. 체결 동기화로 원장과 보호주문을 복구해요")}>선택한 주문이 맞아요 (편입)</button>
      </div>
      <div className="actions">
        <button className="b-ghost" onClick={() => act("not_sent", "미전송으로 확정했어요")}>주문이 나가지 않았어요</button>
        <button className="b-ghost" onClick={() => act("unreconciled", "체결됐지만 원장에는 못 넣었어요. 경고가 남아요")}>체결됐는데 후보에 없어요</button>
      </div>
    </article>
  );
}

function RuleCard({ r, onSaved }: { r: Rule; onSaved: (m: string) => void }) {
  const [f, setF] = useState({ budget: String(r.budget_krw), live: !!r.live, protect: !!r.protect, stop: String(r.stop_atr ?? ""), target: String(r.target_atr ?? "") });
  const save = async () => {
    try {
      await saveRule(r.code, { budget_krw: Number(f.budget), live: f.live ? 1 : 0, protect: f.protect ? 1 : 0, stop_atr: f.stop === "" ? null : Number(f.stop), target_atr: f.target === "" ? null : Number(f.target) });
      onSaved(`${r.name} 설정을 저장했어요`);
    } catch (e) { onSaved(`⚠ ${(e as Error).message}`); }
  };
  return (
    <div className="rule">
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <div className="avatar" style={{ background: avatarColor(r.code), width: 36, height: 36, fontSize: 14 }}>{r.name[0]}</div>
        <div style={{ flex: 1 }}><b>{r.name}</b> <small>{r.code}{r.group ? ` · ${r.group}` : ""}</small></div>
        <button className="btn sm" onClick={save}>저장</button>
      </div>
      <div className="grid">
        <label>매수 한도금액 (원, 0이면 제안 없음)<input className="input" type="number" min={0} step={100000} value={f.budget} onChange={(e) => setF({ ...f, budget: e.target.value })} /></label>
        <label>손절 / 익절 ATR 배수<span style={{ display: "flex", gap: 6 }}>
          <input className="input sm" placeholder="기본" value={f.stop} onChange={(e) => setF({ ...f, stop: e.target.value })} />
          <input className="input sm" placeholder="기본" value={f.target} onChange={(e) => setF({ ...f, target: e.target.value })} /></span></label>
        <label className="chk"><input type="checkbox" checked={f.live} onChange={(e) => setF({ ...f, live: e.target.checked })} />실주문 허용</label>
        <label className="chk"><input type="checkbox" checked={f.protect} onChange={(e) => setF({ ...f, protect: e.target.checked })} />체결 후 보호 손절 등록</label>
      </div>
    </div>
  );
}

export default function TradingPage({ onChanged }: { onChanged: () => void }) {
  const [s, setS] = useState<TradingState | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [liveText, setLiveText] = useState("");
  const [killText, setKillText] = useState("");
  const load = useCallback(() => { fetchTrading().then(setS).catch((e) => setMsg(`⚠ ${e}`)); }, []);
  useEffect(load, [load]);
  useEffect(() => { listenForeground((t, b) => { setMsg(`🔔 ${t} — ${b}`); load(); }); }, [load]);
  const done = (m: string) => { setMsg(m); load(); onChanged(); };
  const fail = (e: unknown) => setMsg(`⚠ ${(e as Error).message}`);

  if (!s) return <p className="empty">{msg ?? "불러오는 중…"}</p>;
  const { toss, risk, limits: lim } = s;
  const toggleLive = async (on: boolean) => {
    try { await setLive(on, on ? liveText : undefined); setLiveText(""); done(on ? "실주문 모드를 켰어요" : "모의 모드로 바꿨어요"); } catch (e) { fail(e); }
  };

  return (
    <div>
      {msg && <p className={`notice${msg.startsWith("⚠") ? " err" : ""}`}>{msg}</p>}
      {risk.kill_switch && <p className="notice err"><b>킬스위치 작동 중</b> — {risk.kill_reason}</p>}

      {/* 폰: order 숫자 순서로 한 줄 / 웹: 왼쪽(승인·근거·이력) + 오른쪽(안전·리스크·설정·연결) */}
      <div className="cols">
        <div className="col-main">
          {(s.needs_review.length > 0 || Object.keys(s.unreconciled).length > 0) && (
            <div style={{ order: 0 }}>
              {Object.entries(s.unreconciled).map(([code, u]) => (
                <p className="notice err" key={code}><b>원장 미반영 가능성 · {code}</b><br />{u.note}
                  <button className="btn ghost sm" style={{ marginLeft: 8 }} onClick={async () => { try { await dismissUnreconciled(code); done("경고를 닫았어요"); } catch (e) { fail(e); } }}>확인했어요</button>
                  {u.kind === "position" && <small> · 보유가 정리되면 자동으로 사라져요</small>}</p>))}
              {s.needs_review.length > 0 && <div className="section-title" style={{ marginTop: 4 }}>전송 여부 확인이 필요한 주문 <span className="neg">{s.needs_review.length}</span></div>}
              {s.needs_review.map((p) => <ReviewCard key={p.id} p={p} onDone={done} onFail={fail} />)}
            </div>)}
          <div style={{ order: 1 }}>
            <div className="section-title" style={{ marginTop: 4 }}>승인 대기 {s.pending.length > 0 && <span className="pos">{s.pending.length}</span>}</div>
            {s.pending.length === 0 && <section className="card"><p className="empty" style={{ padding: "14px 0" }}>갱신 시점에 조건을 충족한 종목이 아직 없어요.<br /><small>아래 ‘종목별 판단 근거’에서 이유를 볼 수 있어요.</small></p></section>}
            {s.pending.map((p) => <ProposalCard key={p.id} p={p} live={risk.live_enabled && !!s.rules.find((r) => r.code === p.code)?.live} onDone={done} />)}
          </div>

          <div style={{ order: 2 }}>
            <div className="section-title">종목별 판단 근거</div>
            {Object.entries(s.screen).map(([code, v]) => {
              if (typeof v === "string") return <p key={code} className="notice">{v}</p>;
              const it = v as ScreenItem, blk = it.buy ?? it.sell;
              if (!blk) return <p key={code} className="notice">{code}: {it.error}</p>;
              const bad = blk.evidence.filter((c) => !c.passed).length;
              return (
                <details key={code} className="card">
                  <summary>{it.name} <small>{it.buy ? "매수" : "매도"} 검토</small> <span className={`chip ${blk.passed ? "buy" : "wait"}`} style={{ float: "right" }}>{blk.passed ? "조건 충족" : `${bad}개 미충족`}</span></summary>
                  <Evidence items={blk.evidence} />
                </details>
              );
            })}
          </div>

          <div style={{ order: 8 }}>
            <div className="section-title">거래 이력</div>
            <section className="card">
              <button className="btn ghost sm" onClick={async () => { try { const r = await syncOrders(); done(r.notes.join(" / ") || "동기화할 변경이 없어요"); } catch (e) { fail(e); } }}>체결 동기화</button>
              <table className="hist"><tbody>
                {s.history.map((p) => (
                  <tr key={p.id}><td><small>{p.created_at.slice(5, 16).replace("T", " ")}</small></td>
                    <td><b className={p.side === "BUY" ? "pos" : "neg"}>{p.side === "BUY" ? "매수" : "매도"}</b> {p.name}<br /><small>{p.qty}주 @ {won(p.price)}</small></td>
                    <td style={{ textAlign: "right" }}>{STATUS[p.status] ?? p.status}{p.dry_run ? <small> · 모의</small> : null}
                      {(p.status === "blocked" || p.status === "failed") && <><br /><small>{JSON.parse(p.result ?? "{}").error ?? ""}</small></>}</td></tr>))}
              </tbody></table>
              {s.history.length === 0 && <p className="empty">아직 이력이 없어요</p>}
            </section>
          </div>
        </div>

        <div className="col-side">
          <div style={{ order: 4 }}>
            <div className="section-title" style={{ marginTop: 4 }}>안전 설정</div>
            <section className="card">
              <h2>실주문 스위치 <span className={`mode ${risk.live_enabled ? "mode-live" : "mode-dry"}`}>{risk.live_enabled ? "실주문" : "모의"}</span></h2>
              <small>이 스위치와 종목별 ‘실주문 허용’이 모두 켜져야 실제 주문이 전송돼요. 평소엔 모의 모드로 두세요.</small>
              <div className="inline">
                {!risk.live_switch ? (<>
                  <input className="input" style={{ flex: 1, minWidth: 140 }} placeholder="‘실주문’ 입력" value={liveText} onChange={(e) => setLiveText(e.target.value)} />
                  <button className="btn danger sm" onClick={() => toggleLive(true)}>켜기</button></>
                ) : <button className="btn ghost sm" onClick={() => toggleLive(false)}>모의 모드로 전환</button>}
              </div>
              {risk.kill_switch && (
                <div className="inline"><input className="input" style={{ flex: 1, minWidth: 140 }} placeholder="‘점검완료’ 입력" value={killText} onChange={(e) => setKillText(e.target.value)} />
                  <button className="btn sm" onClick={async () => { try { await resetKill(killText); setKillText(""); done("킬스위치를 해제했어요 (실주문은 꺼진 상태예요)"); } catch (e) { fail(e); } }}>킬스위치 해제</button></div>
              )}
            </section>
          </div>

          <section className="card kv-list" style={{ order: 5 }}>
            <h2>리스크 현황</h2>
            {[["자본", `${won(risk.capital)}원`], ["이번 달 손익", `${signed(risk.monthly_pnl)}원 (중단선 ${won(risk.monthly_limit)}원)`], ["오늘 매수 / 일일 한도", `${won(risk.spent_today)} / ${won(risk.daily_limit)}원`],
              ["1종목 손절 시 최대 손실", `자본의 ${String(lim.risk_per_trade_pct)}%`], [`갭하락 ${String(lim.gap_assumption_pct)}% 가정 시`, `자본의 ${String(lim.max_gap_loss_pct)}% 이내`], ["최대 보유 종목", `${String(lim.max_positions)}개`]]
              .map(([k, v]) => <div className="kv" key={k}><span className="k">{k}</span><span className="v">{v}</span></div>)}
            {risk.capital === 0 && <p className="notice" style={{ marginTop: 10 }}>config.yaml 의 trading.capital_krw / daily_buy_limit_krw 가 0이라 매수 제안이 만들어지지 않아요.</p>}
          </section>

          <div style={{ order: 3 }}>
            <div className="section-title">종목별 설정</div>
            <section className="card">
              {s.rules.map((r) => <RuleCard key={r.code} r={r} onSaved={done} />)}
              <small>ATR 배수를 비우면 기본값(손절 {String(lim.stop_atr)} / 익절 {String(lim.target_atr)})을 써요. 자본·일일한도·월 손실 중단선은 config.yaml 에서 정해요.</small>
            </section>
          </div>

          <section className="card kv-list" style={{ order: 6 }}>
            <h2>토스증권 연결</h2>
            <div className="kv"><span className="k">상태</span><span className={`v ${toss.connected ? "ok" : "warn"}`}>{toss.connected ? "연결됨" : toss.configured ? "연결 실패" : "API 키 없음"}</span></div>
            {toss.expires && <div className="kv"><span className="k">키 만료</span><span className="v">{toss.expires} (D-{toss.days_left}){toss.days_left !== null && toss.days_left < 30 ? " ⚠" : ""}</span></div>}
            {toss.buying_power !== undefined && <div className="kv"><span className="k">원화 매수가능금액</span><span className="v">{won(toss.buying_power)}원</span></div>}
            {toss.error && <p className="notice err" style={{ marginTop: 10 }}>{toss.error}</p>}
          </section>

          <section className="card" style={{ order: 7 }}>
            <h2>푸시 알림</h2>
            <small>등록된 기기 {s.push.devices}대 · 서버 발송 {s.push.server_configured ? "설정됨" : "미설정"} · 제안/체결/킬스위치 발생 사실만 보내고 금액·수량은 포함하지 않아요.</small>
            {s.push.problem && <p className="notice err" style={{ marginTop: 10 }}>{s.push.problem}</p>}
            <div className="btn-row">
              <button className="btn" onClick={async () => { const r = await enablePush().catch((e) => ({ ok: false, message: String(e) })); done(r.ok ? r.message : `⚠ ${r.message}`); }}>이 기기에서 알림 켜기</button>
              <button className="btn ghost" disabled={!s.push.devices || !s.push.server_configured}
                      onClick={async () => { try { const r = await pushTest(); done(r.error ? `⚠ ${r.error}` : `테스트 알림 ${r.sent}건 성공 · ${r.failed}건 실패`); } catch (e) { fail(e); } }}>테스트 보내기</button>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
