/** 0~100 지표(RSI/스토캐스틱) 게이지: 과매도(낮은 구간)=파랑, 과매수(높은 구간)=빨강 구간과 현재 위치를 보여준다 */
export default function Gauge({ label, value, low, high, sub }: { label: string; value: number | null; low: number; high: number; sub?: string }) {
  const v = value == null ? null : Math.max(0, Math.min(100, value));
  const zone = v == null ? "" : v <= low ? "과매도권" : v >= high ? "과매수권" : "중립";
  return (
    <div className="gauge">
      <div className="gh"><span className="k">{label}</span><span className="v">{v == null ? "-" : v.toFixed(0)} <small>{zone}{sub ? ` · ${sub}` : ""}</small></span></div>
      <div className="gbar">
        <i className="z lo" style={{ width: `${low}%` }} /><i className="z hi" style={{ width: `${100 - high}%` }} />
        {v != null && <b className="pin" style={{ left: `${v}%` }} />}
      </div>
      <div className="gscale"><span>0</span><span style={{ left: `${low}%` }}>{low}</span><span style={{ left: `${high}%` }}>{high}</span><span>100</span></div>
    </div>
  );
}
