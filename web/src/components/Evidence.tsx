import type { Check } from "../types";

/** 정량 근거 체크리스트: 조건별 통과 여부 + 실제 수치 + 기준 */
export default function Evidence({ items }: { items: Check[] }) {
  return (
    <ul className="evidence">
      {items.map((c) => (
        <li key={c.label} className={c.passed ? "ok" : "no"}>
          <span className="mark">{c.passed ? "✓" : "✗"}</span>
          <span className="lbl">{c.label}</span>
          <span className="val">{c.value}{c.threshold ? <small> (기준 {c.threshold})</small> : null}</span>
        </li>
      ))}
    </ul>
  );
}
