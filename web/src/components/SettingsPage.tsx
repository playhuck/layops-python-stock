import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchSettings, resetSettings, saveSectorLimit, saveSettings } from "../api";
import { krwText, signed } from "../format";
import type { SectorRow, SettingsResp } from "../types";

const GROUPS: { title: string; sub: string; keys: string[] }[] = [
  { title: "자본 · 매수 한도", sub: "이 전략에 배정한 돈과 하루·1회 매수 상한이에요. 자본이 0이면 매수 제안이 만들어지지 않아요.", keys: ["capital_krw", "daily_buy_limit_krw", "max_order_krw"] },
  { title: "리스크 한도", sub: "한 종목이 손절돼도, 한 달 동안 잃어도 자본에서 이만큼을 넘지 않게 해요.", keys: ["risk_per_trade_pct", "monthly_kill_pct", "max_positions", "max_group_pct"] },
];

export default function SettingsPage({ onChanged }: { onChanged: () => void }) {
  const [s, setS] = useState<SettingsResp | null>(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [msg, setMsg] = useState<string | null>(null);
  const [needConfirm, setNeedConfirm] = useState(false);
  const [confirm, setConfirm] = useState("");
  const [addId, setAddId] = useState(""); const [addPct, setAddPct] = useState("");

  const load = useCallback(() => {
    fetchSettings().then((d) => { setS(d); setForm(Object.fromEntries(Object.entries(d.values).map(([k, v]) => [k, String(v ?? "")]))); }).catch((e) => setMsg(`⚠ ${e}`));
  }, []);
  useEffect(load, [load]);

  const changed = useMemo(() => (s ? Object.entries(form).filter(([k, v]) => v !== "" && Number(v) !== Number(s.values[k])) : []), [form, s]);
  if (!s) return <p className="empty">{msg ?? "불러오는 중…"}</p>;

  const num = (k: string) => Number(form[k] || 0);
  const cap = num("capital_krw");
  const run = async (fn: () => Promise<unknown>, ok: string) => {
    try { await fn(); setMsg(ok); setNeedConfirm(false); setConfirm(""); load(); onChanged(); }
    catch (e) { const err = e as Error & { needsConfirm?: boolean }; setNeedConfirm(!!err.needsConfirm); setMsg(`⚠ ${err.message}`); }
  };
  const save = () => run(() => saveSettings(Object.fromEntries(changed.map(([k, v]) => [k, Number(v)])), confirm || undefined), "설정을 저장했어요");
  const rows = s.sector_rows;
  const listed = new Set(rows.map((r) => r.id));
  const options = s.sector_options.filter((o) => !listed.has(o.id));
  const nameOf = (id: string) => s.sector_options.find((o) => o.id === id)?.name ?? id;

  return (
    <div>
      {msg && <p className={`notice${msg.startsWith("⚠") ? " err" : ""}`}>{msg}</p>}
      {s.live_on && <p className="notice">실주문 모드예요. 한도를 <b>늘리거나 해제하는</b> 변경은 저장할 때 확인 문구(<b>{s.confirm_text}</b>)가 필요해요. 줄이는 변경은 바로 돼요.</p>}

      <div className="cols">
        <div className="col-main">
          {GROUPS.map((g, gi) => (
            <section className="card" key={g.title} style={{ order: gi + 1 }}>
              <h2>{g.title}</h2><small>{g.sub}</small>
              <div className="fields">
                {g.keys.map((k) => {
                  const f = s.fields[k];
                  return (
                    <label className="field" key={k}>
                      <span className="fl">{f.label}{s.overridden.includes(k) && <i className="dot-ov" title={`기본값 ${s.defaults[k]}`} />}</span>
                      <span className="fi"><input className="input num" type="number" min={f.min} max={f.max} step={f.type === "int" ? (k.endsWith("krw") ? 100000 : 1) : 0.1}
                        value={form[k] ?? ""} onChange={(e) => setForm({ ...form, [k]: e.target.value })} />
                        <small>{f.unit.split(" ")[0]}</small></span>
                      {k.endsWith("krw") && num(k) > 0 && <small className="hint">{krwText(num(k))}</small>}
                    </label>
                  );
                })}
              </div>
            </section>
          ))}

          <section className="card" style={{ order: 3 }}>
            <h2>업종별 비중 한도</h2>
            <small>같은 업종 종목을 합쳐 자본에서 차지하는 비중이에요. 개별 설정이 없으면 대분류는 기본 한도({num("max_group_pct")}%)를, 중분류는 한도 없이 봐요. 종목은 여러 업종에 속할 수 있고, 속한 <b>모든 업종</b>을 검사해요.</small>
            <div className="tbl-wrap"><table className="tbl sect">
              <thead><tr><th>업종</th><th className="dc">내 종목</th><th className="dc">보유 비중</th><th>한도(%)</th><th /></tr></thead>
              <tbody>
                {rows.map((r) => <SectorLimitRow key={r.id} r={r} def={num("max_group_pct")} onSave={(pct) => run(() => saveSectorLimit(r.id, pct, confirm || undefined), `${r.name} 한도를 ${pct == null ? "해제" : "저장"}했어요`)} />)}
                {rows.length === 0 && <tr><td colSpan={5} className="empty" style={{ textAlign: "center" }}>업종 분류 데이터가 없어요. 데이터를 갱신하면 자동으로 받아와요.</td></tr>}
              </tbody>
            </table></div>
            <div className="inline">
              <select className="input" style={{ flex: 1, minWidth: 180 }} value={addId} onChange={(e) => setAddId(e.target.value)}>
                <option value="">업종 추가… (내 종목이 없는 업종도 미리 한도를 걸 수 있어요)</option>
                {options.map((o) => <option key={o.id} value={o.id}>{o.depth === 0 ? "" : `${nameOf(o.parent ?? "")} › `}{o.name}</option>)}
              </select>
              <input className="input sm" placeholder="%" type="number" value={addPct} onChange={(e) => setAddPct(e.target.value)} />
              <button className="btn sm" disabled={!addId || !addPct} onClick={() => run(() => saveSectorLimit(addId, Number(addPct), confirm || undefined), "업종 한도를 추가했어요").then(() => { setAddId(""); setAddPct(""); })}>추가</button>
            </div>
          </section>
        </div>

        <div className="col-side">
          <section className="card kv-list" style={{ order: 2 }}>
            <h2>이 설정이 뜻하는 금액</h2>
            {cap > 0 ? <>
              {[["종목당 손절 시 최대 손실", cap * num("risk_per_trade_pct") / 100], ["월 손실 자동 중단선", cap * num("monthly_kill_pct") / 100], [`업종(대분류) 기본 한도`, cap * num("max_group_pct") / 100],
                ["일일 매수 한도", num("daily_buy_limit_krw")]].map(([k, v]) => <div className="kv" key={String(k)}><span className="k">{k}</span><span className="v">{krwText(Number(v))}</span></div>)}
              <div className="kv"><span className="k">종목당 최대 비중(갭 {s.gap.gap_assumption_pct}% 하락해도 손실 {s.gap.max_gap_loss_pct}% 이내)</span><span className="v">{krwText(cap * s.gap.max_gap_loss_pct / s.gap.gap_assumption_pct)} ({(s.gap.max_gap_loss_pct / s.gap.gap_assumption_pct * 100).toFixed(0)}%)</span></div>
              <small>손절 시 손실은 종목 변동성(ATR)에 맞춰 수량을 거꾸로 계산해요. 한도금액은 종목별로 트레이딩 탭에서 정해요.</small>
            </> : <p className="empty" style={{ padding: "10px 0" }}>자본금을 입력하면 금액으로 환산해 보여줘요.</p>}
          </section>

          <section className="card" style={{ order: 4 }}>
            <h2>저장</h2>
            {changed.length > 0 ? <ul className="chg">{changed.map(([k, v]) => <li key={k}><span>{s.fields[k].label}</span><b>{Number(s.values[k]).toLocaleString("ko-KR")} → {Number(v).toLocaleString("ko-KR")}</b></li>)}</ul>
              : <small>바꾼 값이 없어요.</small>}
            {(needConfirm || s.live_on) && <input className="input" style={{ marginTop: 10 }} placeholder={`확인 문구: ${s.confirm_text} (한도를 늘릴 때만)`} value={confirm} onChange={(e) => setConfirm(e.target.value)} />}
            <div className="btn-row">
              <button className="btn" disabled={changed.length === 0} onClick={save}>저장</button>
              <button className="btn ghost" onClick={() => { setForm(Object.fromEntries(Object.entries(s.values).map(([k, v]) => [k, String(v ?? "")]))); setMsg(null); }}>되돌리기</button>
            </div>
            <div className="btn-row"><button className="btn ghost sm" onClick={() => run(() => resetSettings(confirm || undefined), "기본값으로 복원했어요 (config.yaml 기준)")}>모든 설정을 기본값으로</button></div>
          </section>
        </div>
      </div>
    </div>
  );
}

function SectorLimitRow({ r, def, onSave }: { r: SectorRow; def: number; onSave: (pct: number | null) => void }) {
  const [v, setV] = useState(r.limit_pct == null ? "" : String(r.limit_pct));
  useEffect(() => setV(r.limit_pct == null ? "" : String(r.limit_pct)), [r.limit_pct]);
  const lim = r.effective_limit_pct;
  const over = r.exposure_pct != null && lim != null && r.exposure_pct > lim;
  const dirty = (v === "" ? null : Number(v)) !== r.limit_pct;
  return (
    <tr>
      <td style={{ paddingLeft: r.depth ? 24 : 8 }}>{r.depth ? "└ " : ""}<b style={{ fontWeight: r.depth ? 500 : 700 }}>{r.name}</b> <small>{r.depth ? "중분류" : "대분류"}</small>
        <small className="mob-only-s" style={{ display: "block" }}>내 종목 {r.stock_count || 0}개{r.exposure_pct != null ? ` · 보유 ${r.exposure_pct.toFixed(1)}%` : ""}</small></td>
      <td className="dc">{r.stock_count || "-"}</td>
      <td className={`dc ${over ? "neg" : ""}`} title={r.exposure_pct == null ? "자본금을 설정하고 토스가 연결되면 표시돼요" : ""}>{r.exposure_pct == null ? "-" : `${signed(r.exposure_pct, 1).replace("+", "")}%`}</td>
      <td><input className="input sm" type="number" placeholder={r.depth ? "없음" : String(def)} title={r.depth ? "중분류는 개별 한도를 정하지 않으면 한도 없음" : `개별 설정이 없으면 기본 한도 ${def}% 적용`} value={v} onChange={(e) => setV(e.target.value)} /></td>
      <td>{dirty && <button className="btn sm" onClick={() => onSave(v === "" ? null : Number(v))}>{v === "" ? "해제" : "저장"}</button>}</td>
    </tr>
  );
}
