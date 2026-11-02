import type { Check } from "../types";

/** 정량 근거 체크리스트: 조건별 통과 여부 + 실제 수치 + 기준. 참고 정보(kind=info)는 통과 여부 없이 중립 표시한다.
 *  hit=true 는 "해당하면 주의해야 하는" 조건(매도 근거)용이다: 해당(passed)은 초록 ✓ 대신 주황 !, 해당 없음은 회색으로 보여준다 */
export default function Evidence({ items, hit = false }: { items: Check[]; hit?: boolean }) {
  return (
    <ul className="evidence">
      {items.map((c) => c.kind === "info" ? (
        <li key={c.label} className="info">
          <span className="mark">ⓘ</span>
          <span className="lbl">{c.label}</span>
          <span className="val">{c.value}{c.threshold ? <small className="info-note">{c.threshold}</small> : null}</span>
        </li>
      ) : (
        <li key={c.label} className={c.passed ? (hit ? "hit" : "ok") : "no"}>
          <span className="mark">{c.passed ? (hit ? "!" : "✓") : hit ? "–" : "✗"}</span>
          <span className="lbl">{c.label}</span>
          <span className="val">{c.value}{c.threshold ? <small> (기준 {c.threshold})</small> : null}</span>
        </li>
      ))}
    </ul>
  );
}
