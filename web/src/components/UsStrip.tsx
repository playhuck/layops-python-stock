import { pct } from "../format";
import type { UsOverview } from "../types";

const cls = (v: number | null | undefined) => (v == null ? "" : v >= 0 ? "pos" : "neg");
const COMPACT = ["^IXIC", "^SOX", "^RUT", "^VIX"];     // 한국 시장 화면에는 핵심만, 미국 화면에는 전부
const NO_TREND = ["^VIX", "^TNX", "DX-Y.NYB"];       // 변동성·금리·달러는 20/60일선 배열이 주식 지수처럼 읽히지 않아 표기하지 않는다

/** 미국 시장 요약 카드. 나스닥100 선물은 '지금' 값(밤사이 흐름), 나머지는 마지막 완성 일봉. */
export default function UsStrip({ data, full = false }: { data: UsOverview; full?: boolean }) {
  const rows = data.market.filter((m) => full || COMPACT.includes(m.symbol));
  if (!rows.length && !data.live.length) return <p className="empty">미국 시장 데이터가 아직 없어요. 미국 데이터를 갱신해 주세요.</p>;
  return (
    <div className="stats">
      {data.live.map((l) => (
        <div className="stat" key={l.symbol}>
          <div className="l">{l.name} 현재값</div>
          <div className="n">{l.last.toLocaleString("en-US", { maximumFractionDigits: 1 })}</div>
          <small><span className={cls(l.chg)}>{pct(l.chg, 2)}</span> <span className="muted">{l.base_at} ({l.base.toLocaleString("en-US", { maximumFractionDigits: 1 })}) 대비 · {l.as_of} 기준</span></small>
          <small className="muted" style={{ display: "block" }}>참고용 · 정규장 매매 신호 아님</small>
        </div>
      ))}
      {rows.map((m) => (
        <div className="stat" key={m.symbol}>
          <div className="l">{m.name} ({m.date.slice(5)})</div>
          <div className="n">{m.close.toLocaleString("en-US", { maximumFractionDigits: 2 })}</div>
          <small><span className={cls(m.chg_1d)}>{pct(m.chg_1d, 2)}</span> <span className="muted">· 5일 </span><span className={cls(m.chg_5d)}>{pct(m.chg_5d)}</span>
            {full && m.trend && !NO_TREND.includes(m.symbol) && <span className="muted"> · {m.trend}</span>}</small>
        </div>
      ))}
    </div>
  );
}
