import { useEffect, useState } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, ReferenceArea, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fetchProfile, fetchSeries } from "../api";
import type { Row, VolumeProfile } from "../types";
import { useWide } from "../useWide";
import { HelpButton, HelpPanel, type HelpItem } from "./ChartHelp";

type View = "price" | "volume" | "rsi" | "stoch" | "macd" | "obv";
const TABS: [View, string][] = [["price", "가격"], ["volume", "거래량"], ["rsi", "RSI"], ["stoch", "스토캐스틱"], ["macd", "MACD"], ["obv", "OBV"]];
const css = (v: string) => `var(${v})`;

interface Series { key: string; name: string; color: string; w?: number; dash?: string; bar?: boolean; desc: string; kr?: string }   // kr: 한국 종목 화면에서만 덧붙이는 문장(이 앱의 매수 조건 등)
const SERIES: Record<View, Series[]> = {
  price: [
    { key: "close", name: "종가", color: "#3182f6", w: 2.2, desc: "그날 장이 끝날 때의 가격이에요. 아래 선들은 모두 이 종가로 계산해요." },
    { key: "ma5", name: "5일선", color: "#f04452", w: 1.2, desc: "최근 5일 종가의 평균이에요. 가장 빨리 반응해서 단기 흐름을 보여줘요." },
    { key: "ma20", name: "20일선", color: "#f59e0b", w: 1.4, desc: "최근 20일(약 1개월) 종가의 평균이에요. 스윙의 기준선이에요. 선이 오르는 중이고 가격이 그 위에 있으면 상승 추세, 가격이 아래로 내려오면 추세가 약해졌다고 봐요." },
    { key: "ma60", name: "60일선", color: "#00b493", w: 1.2, desc: "최근 60일(약 3개월) 평균이에요. 중기 추세를 보여줘요. 가격이 이 선 아래면 중기 하락 구간일 수 있어요.",
      kr: "이 앱의 매수 제안은 종가가 60일선과 120일선 위에 있을 때만 나와요." },
    { key: "ma120", name: "120일선", color: "#8b5cf6", w: 1.2, desc: "최근 120일(약 6개월) 평균이에요. 큰 흐름을 보여줘요." },
    { key: "bb_up", name: "볼린저 상단", color: "#c5cbd3", w: 1, dash: "4 3", desc: "20일 평균에 표준편차의 2배를 더한 선이에요. 가격은 보통 상단과 하단 사이에서 움직여요. 상단에 닿으면 단기로 많이 오른 상태예요. 다만 강한 상승장에서는 상단을 따라 계속 오를 수도 있어요." },
    { key: "bb_low", name: "볼린저 하단", color: "#c5cbd3", w: 1, dash: "4 3", desc: "20일 평균에서 표준편차의 2배를 뺀 선이에요. 하단에 닿으면 단기로 많이 내린 상태예요. 하락이 강하면 하단을 따라 더 내려갈 수도 있어요." }],
  volume: [{ key: "volume", name: "거래량", color: "#b0b8c1", bar: true, desc: "그날 거래된 주식 수예요. 가격이 오를 때 거래량이 함께 늘면 힘이 실린 상승이고, 거래량 없이 오르면 오래가지 못할 수도 있어요." }],
  rsi: [{ key: "rsi", name: "RSI", color: "#8b5cf6", w: 2, desc: "최근 14일 동안 오른 폭과 내린 폭을 비교해 0~100으로 나타낸 값이에요. 70 이상이면 과열(많이 오름), 30 이하면 과매도(많이 내림)로 봐요.",
    kr: "이 앱은 기본 설정에서 RSI가 65 미만일 때만 매수를 제안해요." }],
  stoch: [
    { key: "stoch_k", name: "%K (빠른 선)", color: "#f04452", desc: "최근 14일의 가장 높은 값~가장 낮은 값 범위에서 오늘 종가가 어디쯤인지를 0~100으로 나타내고, 그걸 3일 평균 낸 값이에요. 100에 가까우면 최근 고점 근처, 0에 가까우면 저점 근처예요." },
    { key: "stoch_d", name: "%D (느린 선)", color: "#3182f6", desc: "%K의 3일 평균이에요. %K보다 느리게 움직이는 신호선이에요. %K가 %D를 아래에서 위로 뚫으면 반등 신호, 위에서 아래로 뚫으면 힘이 약해지는 신호로 봐요. 20 아래에서 일어난 상향 돌파를 더 의미 있게 봐요." }],
  macd: [
    { key: "macd", name: "MACD", color: "#f04452", desc: "12일 지수평균에서 26일 지수평균을 뺀 값이에요. 단기 추세가 장기 추세보다 얼마나 앞서 있는지 보여줘요. 0보다 위면 상승 쪽, 아래면 하락 쪽이에요." },
    { key: "macd_signal", name: "시그널", color: "#3182f6", desc: "MACD의 9일 지수평균이에요. MACD가 시그널을 위로 뚫으면 상승 전환, 아래로 뚫으면 하락 전환 신호로 봐요." },
    { key: "macd_hist", name: "히스토그램", color: "#b0b8c1", bar: true, desc: "MACD에서 시그널을 뺀 값이에요. 막대가 0 위에서 커지면 상승하는 힘이 세지는 중이고, 줄어들면 힘이 빠지는 중이에요." }],
  obv: [{ key: "obv", name: "OBV", color: "#00b493", w: 2, desc: "오른 날의 거래량은 더하고 내린 날의 거래량은 빼서 쌓은 선이에요. 가격은 제자리인데 OBV가 오르면 사는 힘이 쌓이는 중일 수 있고, 가격은 오르는데 OBV가 내리면 힘이 빠지는 중일 수 있어요. 숫자 자체보다 방향을 보세요." }],
};
// 점선 기준선도 설명한다 (차트에 그려지는 ReferenceLine 과 같은 값)
const PROFILE_HELP: HelpItem = { name: "추정 매물대 (가로 막대)", color: "#8b95a1", shape: "bar",
  desc: "가격대별로 거래가 얼마나 몰렸는지를 오른쪽에서 왼쪽으로 뻗는 가로 막대로 그린 거예요. 하루 거래량이 그날의 저가~고가 사이에 고르게 퍼졌다고 가정한 추정치라 실제 체결 가격과는 달라요. 진한 막대는 평균의 1.5배 이상 몰린 두꺼운 매물대예요. 현재가 위의 두꺼운 매물대는 팔려는 사람이 많을 수 있는 가격대(저항 후보), 아래는 사려는 사람이 많았던 가격대(지지 후보)로 보지만, 가격이 그대로 뚫고 지나갈 수도 있어요. 막대 길이는 이 기간 안에서의 상대 크기예요." };
const REFS: Partial<Record<View, HelpItem[]>> = {
  rsi: [{ name: "파란 점선 30", color: "#3182f6", shape: "dash", desc: "이 아래로 내려가면 과매도 구간이에요. 많이 내렸다는 뜻이지 곧 오른다는 보장은 아니에요." },
        { name: "빨간 점선 70", color: "#f04452", shape: "dash", desc: "이 위로 올라가면 과열 구간이에요. 많이 올랐다는 뜻이고, 강한 상승장에서는 한동안 이 위에 머물 수도 있어요." }],
  stoch: [{ name: "파란 점선 20", color: "#3182f6", shape: "dash", desc: "이 아래는 과매도 구간이에요." }, { name: "빨간 점선 80", color: "#f04452", shape: "dash", desc: "이 위는 과열 구간이에요." }],
  macd: [{ name: "회색 0선", color: "#8b95a1", shape: "line", desc: "MACD와 시그널이 0 위에 있으면 상승 쪽, 아래에 있으면 하락 쪽이에요." }],
};
// 기준선(과매수·과매도 등)의 의미를 차트 아래에 같이 적는다
const GUIDE: Partial<Record<View, string>> = { rsi: "점선: 30 이하 과매도 · 70 이상 과매수", stoch: "점선: 20 이하 과매도 · 80 이상 과매수", macd: "MACD가 시그널을 위로 뚫으면 상승 전환 신호" };

const WINDOWS = [60, 120, 250];
const PROFILE_MAX_FRAC = 0.3;      // 가장 긴 매물대 막대가 차지하는 가로 비율
const sgn = (v: number) => (v >= 0 ? "+" : "−") + Math.abs(v).toFixed(1);
const px = (v: number) => v.toLocaleString("ko-KR", { maximumFractionDigits: v >= 1000 ? 0 : 2 });
const atrTxt = (v: number | null | undefined) => (v == null ? "" : ` · ${sgn(v)} ATR`);

/** 현재가 위·아래 주요 추정 매물대 한 줄 요약. 위는 영역 하단(진입)·상단(돌파), 아래는 영역 상단까지의 거리 */
function ProfileSummary({ p }: { p: VolumeProfile }) {
  if (!p.ok) return <p className="prof-sum muted">추정 매물대를 계산하지 못했어요 · {p.reason}</p>;
  return (
    <p className="prof-sum">
      {p.inside && <span>현재가는 두꺼운 매물대 {px(p.inside.lo)}~{px(p.inside.hi)} 안에 있어요 · </span>}
      {p.up ? <span>위 매물대 <b>{px(p.up.lo)}~{px(p.up.hi)}</b> (진입 {sgn(p.up.enter_pct)}%{atrTxt(p.up.enter_atr)}, 돌파 {sgn(p.up.break_pct ?? 0)}%{atrTxt(p.up.break_atr)})</span> : <span>위쪽에 두꺼운 매물대 없음</span>}
      {" · "}
      {p.down ? <span>아래 매물대 <b>{px(p.down.lo)}~{px(p.down.hi)}</b> (상단까지 {sgn(p.down.enter_pct)}%{atrTxt(p.down.enter_atr)})</span> : <span>아래쪽에 두꺼운 매물대 없음</span>}
      {p.poc && <span className="muted"> · 최대 매물대 {px(p.poc.lo)}~{px(p.poc.hi)}</span>}
    </p>
  );
}

export default function ChartCard({ code, source = fetchSeries, profile = fetchProfile, mine }: { code: string; source?: (code: string) => Promise<Row[]>; profile?: (code: string, window: number) => Promise<VolumeProfile>; mine?: { avg: number } }) {
  const [data, setData] = useState<Row[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState<View>("price");
  const [sel, setSel] = useState<string[]>([]);             // 포커스한 선의 key 목록. 비어 있으면 전체를 보여준다
  const [help, setHelp] = useState(false);
  const [profOn, setProfOn] = useState(true);
  const [profWin, setProfWin] = useState(120);
  const [prof, setProf] = useState<VolumeProfile | null>(null);
  const [profErr, setProfErr] = useState(false);
  const wide = useWide();
  useEffect(() => { setData(null); setErr(null); source(code).then(setData).catch((e) => setErr(String(e))); }, [code, source]);
  useEffect(() => {
    if (!profOn) return;
    let stale = false; setProf(null); setProfErr(false);
    profile(code, profWin).then((p) => { if (!stale) setProf(p); }).catch(() => { if (!stale) setProfErr(true); });
    return () => { stale = true; };
  }, [code, profile, profOn, profWin]);

  const series = SERIES[view], shown = series.filter((x) => sel.length === 0 || sel.includes(x.key));
  const pick = (key: string) => setSel((cur) => (cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key]));    // 누르면 추가, 다시 누르면 빼기, 다 빠지면 전체
  const isKr = source === fetchSeries;
  const showProf = view === "price" && profOn;
  const helpItems: HelpItem[] = [...series.map((x) => ({ name: x.name, color: x.color, shape: (x.bar ? "bar" : x.dash ? "dash" : "line") as HelpItem["shape"], desc: x.desc + (isKr && x.kr ? ` ${x.kr}` : "") })), ...(REFS[view] ?? []), ...(showProf ? [PROFILE_HELP] : [])];
  const tabName = TABS.find(([k]) => k === view)![1];
  const domain: [number, number] | undefined = view === "rsi" || view === "stoch" ? [0, 100] : undefined;
  return (
    <section className="card">
      <div className="chart-head">
        <div className="seg">{TABS.map(([k, l]) => <button key={k} className={view === k ? "on" : ""} onClick={() => { setView(k); setSel([]); }}>{l}</button>)}</div>
        <HelpButton open={help} onClick={() => setHelp((v) => !v)} controls="chart-help" />
      </div>
      {help && <HelpPanel id="chart-help" title={`${tabName} 차트 선 설명`} items={helpItems} onClose={() => setHelp(false)} />}
      {view === "price" && (
        <div className="prof-bar">
          <button className={`lg${profOn ? " on" : ""}`} onClick={() => setProfOn((v) => !v)} aria-pressed={profOn} title="일봉 범위 기반 추정 매물대를 차트에 겹쳐 보기">추정 매물대</button>
          {profOn && <div className="seg prof-win" role="group" aria-label="매물대 기간">{WINDOWS.map((w) => <button key={w} className={profWin === w ? "on" : ""} onClick={() => setProfWin(w)}>{w}일</button>)}</div>}
        </div>
      )}
      {showProf && prof && <ProfileSummary p={prof} />}
      {showProf && profErr && <p className="prof-sum muted">매물대를 불러오지 못했어요</p>}
      {err && <p className="warn">{err}</p>}
      {!data && !err && <p className="empty">차트 불러오는 중…</p>}
      {data && (
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height={view === "price" ? (wide ? 400 : 280) : (wide ? 300 : 220)}>
            <ComposedChart data={data} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke={css("--line")} />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: "#8b95a1" }} minTickGap={50} tickLine={false} axisLine={false} tickFormatter={(d: string) => d.slice(5)} />
              <YAxis tick={{ fontSize: 10, fill: "#8b95a1" }} width={52} domain={domain ?? ["auto", "auto"]} tickLine={false} axisLine={false}
                     tickFormatter={(v: number) => (Math.abs(v) >= 1e6 ? `${(v / 1e6).toFixed(1)}M` : Math.abs(v) >= 1e4 ? `${Math.round(v / 1e3)}k` : String(Math.round(v)))} />
              <Tooltip formatter={(v) => (typeof v === "number" ? v.toLocaleString("ko-KR", { maximumFractionDigits: 2 }) : String(v))} contentStyle={{ fontSize: 12, borderRadius: 12, border: "none", background: "#333d4b", color: "#fff" }} labelStyle={{ color: "#c5cbd3" }} itemStyle={{ color: "#fff" }} />
              {showProf && prof?.ok && data.length > 1 && prof.bins!.map((b, i) => {      // 오른쪽 끝에서 왼쪽으로 뻗는 막대 (길이는 최대 구간 대비 비율)
                const k = Math.max(1, Math.round(b.rel_poc * Math.floor(data.length * PROFILE_MAX_FRAC)));
                const thick = b.mult >= (prof.meta.thickness_multiple ?? 1.5);
                return <ReferenceArea key={i} x1={String(data[Math.max(0, data.length - 1 - k)].date)} x2={String(data[data.length - 1].date)} y1={b.lo} y2={b.hi}
                                      fill={thick ? "#3182f6" : "#8b95a1"} fillOpacity={thick ? 0.28 : 0.14} stroke="none" ifOverflow="hidden" />;
              })}
              {view === "price" && mine && data.length > 0 && (() => {      // 내 평단: 현재가에서 50% 넘게 벗어나면 차트가 찌그러지므로 범위를 넓히지 않고 숨긴다
                const lastClose = Number(data[data.length - 1].close);
                const near = lastClose > 0 && Math.abs(mine.avg / lastClose - 1) <= 0.5;
                return <ReferenceLine y={mine.avg} stroke="#e8830a" strokeDasharray="6 3" strokeWidth={1.6} ifOverflow={near ? "extendDomain" : "hidden"}
                                      label={{ value: "내 평단", position: "insideTopLeft", fontSize: 11, fill: "#e8830a" }} />;
              })()}
              {view === "rsi" && <><ReferenceLine y={30} stroke="#3182f6" strokeDasharray="3 3" /><ReferenceLine y={70} stroke="#f04452" strokeDasharray="3 3" /></>}
              {view === "stoch" && <><ReferenceLine y={20} stroke="#3182f6" strokeDasharray="3 3" /><ReferenceLine y={80} stroke="#f04452" strokeDasharray="3 3" /></>}
              {view === "macd" && <ReferenceLine y={0} stroke="#8b95a1" />}
              {[...shown].sort((x, y) => Number(!!y.bar) - Number(!!x.bar)).map((x) => x.bar
                ? <Bar key={x.key} dataKey={x.key} name={x.name} fill={x.color} radius={[3, 3, 0, 0]} isAnimationActive={false} onClick={() => pick(x.key)} cursor="pointer" />
                : <Line key={x.key} type="monotone" dataKey={x.key} name={x.name} stroke={x.color} dot={false} strokeWidth={sel.length ? (x.w ?? 1.6) + 0.6 : x.w ?? 1.6}
                        strokeDasharray={x.dash} isAnimationActive={false} activeDot={{ r: 4, onClick: () => pick(x.key) }} style={{ cursor: "pointer" }} onClick={() => pick(x.key)} />)}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      {data && series.length > 1 && (
        <div className="legend" role="group" aria-label="차트 선 목록">
          {series.map((x) => {
            const on = sel.includes(x.key);
            return (
            <button key={x.key} className={`lg${on ? " on" : ""}${sel.length && !on ? " dim" : ""}`} onClick={() => pick(x.key)} aria-pressed={on}
                    title={on ? (sel.length === 1 ? "다시 누르면 선택을 풀고 전체를 봐요" : "다시 누르면 선택에서 빼요") : sel.length ? `${x.name}도 함께 보기` : `${x.name}만 보기`}>
              <i style={{ background: x.color, borderTop: x.dash ? `2px dashed ${x.color}` : undefined, height: x.bar ? 8 : x.dash ? 0 : 3 }} />{x.name}
            </button>);
          })}
          {sel.length > 0 && <button className="lg reset" onClick={() => setSel([])}>전체 보기</button>}
        </div>
      )}
      {data && <p className="legend-hint">{GUIDE[view] ?? ""}{series.length > 1 ? `${GUIDE[view] ? " · " : ""}선 이름을 누르면 그 선만 보고, 다른 선을 누르면 함께 봐요 (다시 누르면 빼요)` : ""}</p>}
    </section>
  );
}
