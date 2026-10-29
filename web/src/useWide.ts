import { useEffect, useState } from "react";

/** 데스크톱 폭(900px 이상) 여부: 차트 높이 등 CSS 로 못 정하는 값에 사용 */
export function useWide(): boolean {
  const q = "(min-width: 900px)";
  const [wide, setWide] = useState(() => window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const on = () => setWide(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, []);
  return wide;
}
