import { useEffect, useState } from "react";
import { Bar, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fetchSeries } from "../api";
import type { Row } from "../types";
import { useWide } from "../useWide";

type View = "price" | "volume" | "rsi" | "stoch" | "macd" | "obv";
const TABS: [View, string][] = [["price", "가격"], ["volume", "거래량"], ["rsi", "RSI"], ["stoch", "스토캐스틱"], ["macd", "MACD"], ["obv", "OBV"]];
const css = (v: string) => `var(${v})`;

interface Series { key: string; name: string; color: string; w?: number; dash?: string; bar?: boolean }
const SERIES: Record<View, Series[]> = {
  price: [{ key: "close", name: "종가", color: "#3182f6", w: 2.2 }, { key: "ma5", name: "5일선", color: "#f04452", w: 1.2 }, { key: "ma20", name: "20일선", color: "#f59e0b", w: 1.4 },
    { key: "ma60", name: "60일선", color: "#00b493", w: 1.2 }, { key: "ma120", name: "120일선", color: "#8b5cf6", w: 1.2 },
    { key: "bb_up", name: "볼린저 상단", color: "#c5cbd3", w: 1, dash: "4 3" }, { key: "bb_low", name: "볼린저 하단", color: "#c5cbd3", w: 1, dash: "4 3" }],
  volume: [{ key: "volume", name: "거래량", color: "#b0b8c1", bar: true }],
  rsi: [{ key: "rsi", name: "RSI", color: "#8b5cf6", w: 2 }],
  stoch: [{ key: "stoch_k", name: "%K (빠른 선)", color: "#f04452" }, { key: "stoch_d", name: "%D (느린 선)", color: "#3182f6" }],
  macd: [{ key: "macd", name: "MACD", color: "#f04452" }, { key: "macd_signal", name: "시그널", color: "#3182f6" }, { key: "macd_hist", name: "히스토그램", color: "#b0b8c1", bar: true }],
  obv: [{ key: "obv", name: "OBV", color: "#00b493", w: 2 }],
};
// 기준선(과매수·과매도 등)의 의미를 차트 아래에 같이 적는다
const GUIDE: Partial<Record<View, string>> = { rsi: "점선: 30 이하 과매도 · 70 이상 과매수", stoch: "점선: 20 이하 과매도 · 80 이상 과매수", macd: "MACD가 시그널을 위로 뚫으면 상승 전환 신호" };

export default function ChartCard({ code, source = fetchSeries }: { code: string; source?: (code: string) => Promise<Row[]> }) {
  const [data, setData] = useState<Row[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState<View>("price");
  const [solo, setSolo] = useState<string | null>(null);   // 하나만 보기: 선택한 선의 key (null = 전체)
  const wide = useWide();
  useEffect(() => { setData(null); setErr(null); source(code).then(setData).catch((e) => setErr(String(e))); }, [code, source]);

  const series = SERIES[view], shown = series.filter((x) => !solo || x.key === solo);
  const pick = (key: string) => setSolo((cur) => (cur === key ? null : key));
  const domain: [number, number] | undefined = view === "rsi" || view === "stoch" ? [0, 100] : undefined;
  return (
    <section className="card">
      <div className="seg">{TABS.map(([k, l]) => <button key={k} className={view === k ? "on" : ""} onClick={() => { setView(k); setSolo(null); }}>{l}</button>)}</div>
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
              {view === "rsi" && <><ReferenceLine y={30} stroke="#3182f6" strokeDasharray="3 3" /><ReferenceLine y={70} stroke="#f04452" strokeDasharray="3 3" /></>}
              {view === "stoch" && <><ReferenceLine y={20} stroke="#3182f6" strokeDasharray="3 3" /><ReferenceLine y={80} stroke="#f04452" strokeDasharray="3 3" /></>}
              {view === "macd" && <ReferenceLine y={0} stroke="#8b95a1" />}
              {[...shown].sort((x, y) => Number(!!y.bar) - Number(!!x.bar)).map((x) => x.bar
                ? <Bar key={x.key} dataKey={x.key} name={x.name} fill={x.color} radius={[3, 3, 0, 0]} isAnimationActive={false} onClick={() => pick(x.key)} cursor="pointer" />
                : <Line key={x.key} type="monotone" dataKey={x.key} name={x.name} stroke={x.color} dot={false} strokeWidth={solo ? (x.w ?? 1.6) + 0.6 : x.w ?? 1.6}
                        strokeDasharray={x.dash} isAnimationActive={false} activeDot={{ r: 4, onClick: () => pick(x.key) }} style={{ cursor: "pointer" }} onClick={() => pick(x.key)} />)}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      {data && series.length > 1 && (
        <div className="legend" role="group" aria-label="차트 선 목록">
          {series.map((x) => (
            <button key={x.key} className={`lg${solo === x.key ? " on" : ""}${solo && solo !== x.key ? " dim" : ""}`} onClick={() => pick(x.key)} aria-pressed={solo === x.key}
                    title={solo === x.key ? "다시 누르면 전체 보기" : `${x.name}만 보기`}>
              <i style={{ background: x.color, borderTop: x.dash ? `2px dashed ${x.color}` : undefined, height: x.bar ? 8 : x.dash ? 0 : 3 }} />{x.name}
            </button>))}
          {solo && <button className="lg reset" onClick={() => setSolo(null)}>전체 보기</button>}
        </div>
      )}
      {data && <p className="legend-hint">{GUIDE[view] ?? ""}{series.length > 1 ? `${GUIDE[view] ? " · " : ""}선 이름을 누르면 그 선만 봐요` : ""}</p>}
    </section>
  );
}
