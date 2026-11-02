import { useEffect } from "react";

export interface HelpItem { name: string; color: string; shape?: "line" | "dash" | "bar"; desc: string }

/** 차트 오른쪽 위의 "선 설명" 버튼. 누르면 HelpPanel 이 열린다 (상태는 차트 카드가 가진다). */
export function HelpButton({ open, onClick, controls }: { open: boolean; onClick: () => void; controls: string }) {
  return (
    <button type="button" className={`help-btn${open ? " on" : ""}`} onClick={onClick} aria-expanded={open} aria-controls={controls}
            title="이 차트의 선이 무슨 뜻인지 보기">ⓘ 선 설명</button>
  );
}

/** 지금 보고 있는 차트의 선마다 색·이름·뜻을 보여 준다. Esc 로 닫는다. */
export function HelpPanel({ id, title, items, note, onClose }: { id: string; title: string; items: HelpItem[]; note?: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="help-panel" id={id} role="region" aria-label={title}>
      <div className="help-head"><b>{title}</b><button type="button" className="help-x" onClick={onClose} aria-label="설명 닫기">닫기</button></div>
      <dl>
        {items.map((x) => (
          <div className="help-it" key={x.name}>
            <dt><i className={x.shape ?? "line"} style={{ ["--c" as string]: x.color }} />{x.name}</dt>
            <dd>{x.desc}</dd>
          </div>
        ))}
      </dl>
      {note && <p className="help-note">{note}</p>}
    </div>
  );
}
