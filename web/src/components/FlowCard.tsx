import { useEffect, useMemo, useState } from "react";
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fetchStockSeries } from "../api";
import { INV } from "../investors";
import type { StockSeries } from "../types";
import { useWide } from "../useWide";
import { HelpButton, HelpPanel, type HelpItem } from "./ChartHelp";

type View = "flow" | "cum" | "fown" | "short" | "sbal";
const TABS: [View, string, string][] = [
  ["flow", "수급", "일별 순매수 수량(만주). 외국인·기관이 함께 사는 날이 늘면 긍정적 신호로 봅니다."],
  ["cum", "누적 수급", "기간 시작부터의 누적 순매수. 선이 우상향이면 그 주체가 꾸준히 사들이는 중입니다."],
  ["fown", "외국인 보유율", "상장주식 중 외국인이 가진 비율(%). 보유율이 오르면 외국인이 모으는 중입니다."],
  ["short", "공매도 비중", "거래대금 중 공매도 비율(%). 갑자기 높아지면 하락에 베팅하는 물량이 늘었다는 뜻입니다."],
  ["sbal", "공매도 잔고", "갚지 않고 남아 있는 공매도 비율(%). 공시 기준이라 며칠 늦게 반영됩니다."],
];
const INV_DESC: Record<string, string> = {
  individual: "개인 투자자가 산 수량에서 판 수량을 뺀 값이에요. 마이너스면 판 쪽이에요.",
  institution: "연기금·투신·보험·금융투자 등 기관 전체의 순매수예요. 큰 자금이라 꾸준히 사면 긍정적으로 봐요.",
  foreign: "외국인 투자자 전체의 순매수예요. 기관과 함께 사는 날이 늘면 긍정적인 신호로 봐요.",
};
const HELP: Record<View, { items: HelpItem[]; note?: string }> = {
  flow: { items: INV.map(([k, l, c]) => ({ name: `${l} (막대)`, color: c, shape: "bar" as const, desc: INV_DESC[k] })),
          note: "하루 단위 순매수 수량(만주)이에요. 한쪽이 사면 다른 쪽이 판 것이라, 개인·기관·외국인·기타법인을 모두 더하면 0에 가까워요." },
  cum: { items: INV.map(([k, l, c]) => ({ name: `${l} (선)`, color: c, shape: "line" as const, desc: `${INV_DESC[k]} 여기서는 기간 시작부터 더한 누적값이에요. 선이 우상향이면 꾸준히 사들이는 중이에요.` })),
         note: "단위는 만주예요. 기간이 바뀌면 시작점(0)이 바뀌어서 선의 높이도 달라져요." },
  fown: { items: [{ name: "외국인 보유율", color: "#00b493", desc: "상장주식 중 외국인이 가지고 있는 비율(%)이에요. 오르면 외국인이 모으는 중, 내리면 줄이는 중이에요." }] },
  short: { items: [{ name: "공매도 비중 (막대)", color: "#f59e0b", shape: "bar", desc: "그날 거래대금 중 공매도가 차지한 비율(%)이에요. 공매도는 주가가 내릴 것에 거는 거래라, 갑자기 높아지면 하락에 거는 물량이 늘었다는 뜻이에요." }] },
  sbal: { items: [{ name: "공매도 잔고 비율", color: "#8b5cf6", desc: "아직 갚지 않고 남아 있는 공매도 물량의 비율(%)이에요. 공시 기준이라 며칠 늦게 반영돼요. 높으면 나중에 갚으려고 되사야 하는 물량도 많다는 뜻이에요." }],
          note: "공매도 잔고는 공시가 나온 날짜 기준이라 가장 최근 값이 며칠 전 것일 수 있어요." },
};
const tip = { contentStyle: { fontSize: 12, borderRadius: 12, border: "none", background: "#333d4b", color: "#fff" }, labelStyle: { color: "#c5cbd3" }, itemStyle: { color: "#fff" } };
const axis = { tick: { fontSize: 11, fill: "#8b95a1" }, tickLine: false, axisLine: false } as const;

export default function FlowCard({ code }: { code: string }) {
  const [data, setData] = useState<StockSeries | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState<View>("flow");
  const [days, setDays] = useState(40);
  const [help, setHelp] = useState(false);
  const wide = useWide();
  useEffect(() => { setData(null); setErr(null); fetchStockSeries(code, 120).then(setData).catch((e) => setErr(String(e))); }, [code]);

  const rows = useMemo(() => {
    if (!data) return [];
    const man = (v: unknown) => (typeof v === "number" ? Math.round(v / 1e4) : null);
    if (view === "flow" || view === "cum") {
      let acc = { individual: 0, institution: 0, foreign: 0 };
      return data.flows.slice(-days).map((r) => {
        const o: Record<string, number | string | null> = { date: String(r.date).slice(5) };
        for (const [k] of INV) { acc = { ...acc, [k]: acc[k] + (typeof r[k] === "number" ? (r[k] as number) : 0) }; o[k] = view === "cum" ? Math.round(acc[k] / 1e4) : man(r[k]); }
        return o;
      });
    }
    const src = view === "fown" ? data.foreign_own : view === "short" ? data.short : data.short_balance;
    return src.slice(-days).map((r) => ({ date: String(r.date).slice(5), ratio: r.ratio as number | null }));
  }, [data, view, days]);

  const caption = TABS.find(([k]) => k === view)![2];
  const empty = data && rows.length === 0;
  return (
    <section className="card">
      <div className="toolbar">
        <h2 style={{ margin: 0 }}>수급 · 공매도 추이</h2>
        <div className="tools">
          <div className="seg" style={{ minWidth: 170 }}>{[20, 40, 60].map((d) => <button key={d} className={days === d ? "on" : ""} onClick={() => setDays(d)}>{d}일</button>)}</div>
          <HelpButton open={help} onClick={() => setHelp((v) => !v)} controls="flow-help" />
        </div>
      </div>
      {help && <HelpPanel id="flow-help" title={`${TABS.find(([k]) => k === view)![1]} 차트 선 설명`} items={HELP[view].items} note={HELP[view].note} onClose={() => setHelp(false)} />}
      <div className="seg">{TABS.map(([k, l]) => <button key={k} className={view === k ? "on" : ""} onClick={() => setView(k)}>{l}</button>)}</div>
      {err && <p className="warn">{err}</p>}
      {!data && !err && <p className="empty">차트 불러오는 중…</p>}
      {empty && <p className="empty">아직 데이터가 없어요. 데이터를 갱신해 주세요.</p>}
      {data && !empty && (
        <div className="chart-wrap">
          <ResponsiveContainer width="100%" height={wide ? 300 : 230}>
            <ComposedChart data={rows} margin={{ left: 0, right: 8, top: 8, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--line)" />
              <XAxis dataKey="date" {...axis} minTickGap={24} />
              <YAxis {...axis} width={view === "flow" || view === "cum" ? 56 : 44} domain={view === "fown" || view === "sbal" ? ["auto", "auto"] : undefined}
                     tickFormatter={(v: number) => (view === "flow" || view === "cum" ? v.toLocaleString("ko-KR") : `${v}%`)} />
              <Tooltip {...tip} formatter={(v: number) => (view === "flow" || view === "cum" ? `${v.toLocaleString("ko-KR")}만주` : `${v}%`)} />
              {(view === "flow" || view === "cum") && <Legend wrapperStyle={{ fontSize: 12 }} />}
              {(view === "flow" || view === "cum") && <ReferenceLine y={0} stroke="#8b95a1" />}
              {view === "flow" && INV.map(([k, l, c]) => <Bar key={k} dataKey={k} name={l} fill={c} radius={[3, 3, 0, 0]} isAnimationActive={false} />)}
              {view === "cum" && INV.map(([k, l, c]) => <Line key={k} type="monotone" dataKey={k} name={l} stroke={c} strokeWidth={2.2} dot={false} isAnimationActive={false} />)}
              {view === "fown" && <Line type="monotone" dataKey="ratio" name="외국인 보유율" stroke="#00b493" strokeWidth={2.4} dot={false} isAnimationActive={false} />}
              {view === "short" && <Bar dataKey="ratio" name="공매도 비중" fill="#f59e0b" radius={[3, 3, 0, 0]} isAnimationActive={false} />}
              {view === "sbal" && <Line type="monotone" dataKey="ratio" name="공매도 잔고 비율" stroke="#8b5cf6" strokeWidth={2.4} dot={false} isAnimationActive={false} />}
            </ComposedChart>
          </ResponsiveContainer>
        </div>
      )}
      <small>{caption}</small>
    </section>
  );
}
