import { useEffect, useMemo, useState } from "react";
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fetchMarketFlow } from "../api";
import { signed } from "../format";
import { INV } from "../investors";
import type { FlowRow, MarketFlow } from "../types";
import { useWide } from "../useWide";

const PARTS = ["금융투자", "보험", "투신", "사모", "은행", "기타금융", "연기금"];
const fmt = (v: number | null | undefined) => (v == null ? "-" : signed(v));
const cls = (v: number | null | undefined) => (v == null ? "" : v >= 0 ? "pos" : "neg");

export default function MarketPage() {
  const [data, setData] = useState<MarketFlow | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [days, setDays] = useState(20);
  const [mkt, setMkt] = useState("STK");
  const [mode, setMode] = useState<"daily" | "cum">("daily");
  const [detail, setDetail] = useState(false);
  const wide = useWide();

  useEffect(() => { fetchMarketFlow(60).then(setData).catch((e) => setErr(String(e))); }, []);
  const market = data?.markets.find((m) => m.id === mkt) ?? data?.markets[0];
  const rows: FlowRow[] = useMemo(() => (market?.rows ?? []).slice(-days), [market, days]);

  const chart = useMemo(() => {
    const acc = { individual: 0, institution: 0, foreign: 0 };
    return rows.map((r) => {
      const o: Record<string, number | string | null> = { date: r.date.slice(5) };
      for (const [k] of INV) { acc[k] += r[k] ?? 0; o[k] = mode === "cum" ? Math.round(acc[k]) : r[k]; }
      return o;
    });
  }, [rows, mode]);

  if (err) return <p className="notice err">시장 수급을 불러오지 못했어요: {err}</p>;
  if (!data) return <p className="empty">불러오는 중…</p>;
  if (!market) return <p className="empty">시장 수급 데이터가 아직 없어요. 데이터를 갱신해 주세요.</p>;
  const last = rows[rows.length - 1];
  const sum = (k: "individual" | "institution" | "foreign") => rows.reduce((a, r) => a + (r[k] ?? 0), 0);

  return (
    <div>
      <div className="toolbar">
        <div className="seg" style={{ minWidth: 200 }}>{data.markets.map((m) => <button key={m.id} className={m.id === market.id ? "on" : ""} onClick={() => setMkt(m.id)}>{m.label}</button>)}</div>
        <div className="seg" style={{ minWidth: 220 }}>{[10, 20, 30].map((d) => <button key={d} className={days === d ? "on" : ""} onClick={() => setDays(d)}>{d}일</button>)}</div>
      </div>

      {last && (
        <div className="stats">
          <div className="stat"><div className="l">{market.label} 지수 ({last.date.slice(5)})</div><div className="n">{last.index_close?.toLocaleString("ko-KR", { maximumFractionDigits: 2 }) ?? "-"}</div>
            <small className={cls(last.index_rate)}>{last.index_rate == null ? "" : `${last.index_rate >= 0 ? "+" : ""}${last.index_rate.toFixed(2)}%`}</small></div>
          {INV.map(([k, label]) => (
            <div className="stat" key={k}><div className="l">{label} · 당일 순매수</div><div className={`n ${cls(last[k])}`}>{fmt(last[k])}<small>억</small></div>
              <small>최근 {days}일 합계 <span className={cls(sum(k))}>{signed(Math.round(sum(k)))}억</span></small></div>
          ))}
        </div>
      )}

      <section className="card">
        <div className="toolbar">
          <h2 style={{ margin: 0 }}>투자자별 순매수 <small>(억원)</small></h2>
          <div className="seg" style={{ minWidth: 160 }}><button className={mode === "daily" ? "on" : ""} onClick={() => setMode("daily")}>일별</button><button className={mode === "cum" ? "on" : ""} onClick={() => setMode("cum")}>누적</button></div>
        </div>
        <ResponsiveContainer width="100%" height={wide ? 360 : 260}>
          <ComposedChart data={chart} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke="var(--line)" />
            <XAxis dataKey="date" tick={{ fontSize: 11, fill: "#8b95a1" }} tickLine={false} axisLine={false} minTickGap={16} />
            <YAxis tick={{ fontSize: 11, fill: "#8b95a1" }} width={56} tickLine={false} axisLine={false} tickFormatter={(v: number) => v.toLocaleString("ko-KR")} />
            <Tooltip contentStyle={{ fontSize: 12, borderRadius: 12, border: "none", background: "#333d4b", color: "#fff" }} labelStyle={{ color: "#c5cbd3" }} itemStyle={{ color: "#fff" }}
                     formatter={(v: number) => `${v.toLocaleString("ko-KR")}억`} />
            <Legend wrapperStyle={{ fontSize: 12 }} />
            <ReferenceLine y={0} stroke="#8b95a1" />
            {mode === "daily"
              ? INV.map(([k, label, c]) => <Bar key={k} dataKey={k} name={label} fill={c} radius={[3, 3, 0, 0]} isAnimationActive={false} />)
              : INV.map(([k, label, c]) => <Line key={k} type="monotone" dataKey={k} name={label} stroke={c} strokeWidth={2.2} dot={false} isAnimationActive={false} />)}
          </ComposedChart>
        </ResponsiveContainer>
      </section>

      <section className="card">
        <div className="toolbar">
          <h2 style={{ margin: 0 }}>일별 상세 <small>(순매수 억원, 최신순)</small></h2>
          <label><input type="checkbox" checked={detail} onChange={(e) => setDetail(e.target.checked)} />기관 세부 구분 보기</label>
        </div>
        <div className="tbl-wrap">
          <table className="tbl">
            <thead><tr><th>날짜</th><th>지수</th><th>등락률</th><th>개인</th><th>기관</th><th>외국인</th><th>기타법인</th>{detail && PARTS.map((p) => <th key={p}>{p}</th>)}</tr></thead>
            <tbody>
              {[...rows].reverse().map((r) => (
                <tr key={r.date}>
                  <td>{wide ? r.date : r.date.slice(5)}</td><td>{r.index_close?.toLocaleString("ko-KR", { maximumFractionDigits: 2 }) ?? "-"}</td>
                  <td className={cls(r.index_rate)}>{r.index_rate == null ? "-" : `${r.index_rate >= 0 ? "+" : ""}${r.index_rate.toFixed(2)}%`}</td>
                  <td className={cls(r.individual)}>{fmt(r.individual)}</td><td className={cls(r.institution)}>{fmt(r.institution)}</td>
                  <td className={cls(r.foreign)}>{fmt(r.foreign)}</td><td className={cls(r.other_corp)}>{fmt(r.other_corp)}</td>
                  {detail && PARTS.map((p) => <td key={p} className={cls(r.parts[p])}>{fmt(r.parts[p])}</td>)}
                </tr>))}
            </tbody>
          </table>
        </div>
        <small>개인·기관·외국인·기타법인의 합은 0에 가깝습니다(매수=매도). 외국인은 외국인+기타외국인 합계입니다. 당일 값은 장중에 바뀔 수 있어요.</small>
      </section>
    </div>
  );
}
