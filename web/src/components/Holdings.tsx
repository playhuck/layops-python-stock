import type { HoldingItem, HoldingsSource, HoldingsView } from "../types";

export const SOURCE_LABEL: Record<HoldingsSource, string> = { manual: "직접 입력", import: "가져와서 반영" };

/** 보유 정보 출처 선택: 고른 쪽의 보유만 보여준다(겹치지 않음). 화면 표시만 바꾸고 거래 엔진에는 영향이 없다 */
export function SourceToggle({ source, onChange }: { source: HoldingsSource; onChange: (s: HoldingsSource) => void }) {
  return (
    <div className="seg src-seg" role="group" aria-label="보유 정보 출처" title="직접 입력한 보유만 보거나, 토스에서 가져온 보유만 봐요 (두 가지를 섞어 보여주지 않아요)">
      {(["manual", "import"] as const).map((k) => <button key={k} className={source === k ? "on" : ""} aria-pressed={source === k} onClick={() => source !== k && onChange(k)}>{SOURCE_LABEL[k]}</button>)}
    </div>
  );
}

/** 한국 종목의 보유 (모름이면 undefined) */
export const krHolding = (v: HoldingsView | null, code: string): HoldingItem | undefined => (v?.kr.known ? v.kr.items[code] : undefined);

/** 가져오기 모드에서 보유를 알 수 없을 때의 안내 (미보유로 오해하지 않게) */
export function krUnknownNote(v: HoldingsView | null): string | null {
  if (!v || v.source !== "import") return null;
  if (!v.kr.configured) return "토스가 연결되지 않아 보유를 가져올 수 없어요";
  if (!v.kr.known) return "토스에서 보유를 가져오지 못했어요 (연결을 확인해 주세요)";
  return null;
}

/** 평단 대비 현재가 등락률(%) — 현재가는 가져온 값이 있으면 그것, 없으면 화면의 현재가 */
export const pnlPct = (h: HoldingItem, price: number | null | undefined): number | null => {
  const last = h.last ?? price;
  return last && h.avg ? (last / h.avg - 1) * 100 : null;
};
