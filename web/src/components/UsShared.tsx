import type { UsVerdict } from "../types";

export const cls = (v: number | null | undefined) => (v == null ? "" : v >= 0 ? "pos" : "neg");
// 판정 → 칩 색: 매수 검토=빨강, 매도 검토=파랑, 주의·실적일 확인 필요=주황, 나머지는 회색 (한국 화면과 같은 색 약속)
const CHIP: Record<string, string> = { review: "buy", review_check: "split", sell: "sell", caution: "split" };
export const Verdict = ({ v }: { v: UsVerdict }) => <span className={`chip ${CHIP[v.code] ?? "wait"}`}>{v.label}</span>;
export const px2 = (n: number | null | undefined) => (n == null ? "-" : n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
