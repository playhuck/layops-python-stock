/** 미니 추이 차트. 한국식 색: 기간 시작 대비 오르면 빨강, 내리면 파랑 */
export default function Sparkline({ values }: { values: number[] }) {
  if (!values || values.length < 2) return <div className="spark" />;
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const pts = values.map((v, i) => `${((i / (values.length - 1)) * 100).toFixed(2)},${(26 - ((v - min) / span) * 24).toFixed(2)}`);
  const up = values[values.length - 1] >= values[0];
  const color = up ? "var(--up)" : "var(--down)";
  const chg = (values[values.length - 1] / values[0] - 1) * 100;
  return (
    <div className="spark" title={`최근 ${values.length}일 ${chg >= 0 ? "+" : ""}${chg.toFixed(1)}%`}>
      <svg viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden>
        <polygon points={`0,28 ${pts.join(" ")} 100,28`} fill={color} opacity="0.1" />
        <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth="1.8" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      </svg>
    </div>
  );
}
