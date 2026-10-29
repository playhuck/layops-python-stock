import { useCallback, useEffect, useState } from "react";

// 얇은 History API 라우터: 새로고침해도 같은 화면, 브라우저 뒤로/앞으로 가기 지원. (서버는 모든 경로에 index.html 을 돌려준다)
export type Tab = "home" | "sector" | "market" | "trade" | "settings";
export interface Route { tab: Tab; code: string | null }

const PATH: Record<Tab, string> = { home: "/", sector: "/sectors", market: "/market", trade: "/trading", settings: "/settings" };

export function parse(pathname: string): Route {
  const p = pathname.replace(/\/+$/, "") || "/";
  const m = /^\/stocks\/([^/]+)$/.exec(p);
  if (m) return { tab: "home", code: decodeURIComponent(m[1]) };
  const tab = (Object.keys(PATH) as Tab[]).find((t) => PATH[t] === p);
  return { tab: tab ?? "home", code: null };     // 모르는 경로는 홈
}

export const hrefOf = (r: Route) => (r.code ? `/stocks/${encodeURIComponent(r.code)}` : PATH[r.tab]);
export const tabHref = (t: Tab) => PATH[t];

const idx = () => (history.state && typeof history.state.idx === "number" ? history.state.idx : 0);

export function useRoute() {
  const [route, setRoute] = useState<Route>(() => parse(location.pathname));
  useEffect(() => {
    if (history.state?.idx === undefined) history.replaceState({ idx: 0 }, "");       // 앱 안에서 쌓인 이력 깊이(뒤로 갈 곳이 앱 안에 있는지 판단)
    const onPop = () => setRoute(parse(location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const navigate = useCallback((to: Route, replace = false) => {
    const url = hrefOf(to);
    if (url === location.pathname) replace = true;                                    // 같은 화면을 다시 눌러도 이력이 쌓이지 않게
    if (replace) history.replaceState({ idx: idx() }, "", url); else history.pushState({ idx: idx() + 1 }, "", url);
    setRoute(to);
    if (!replace) window.scrollTo(0, 0);
  }, []);
  // 앱 안에서 온 경우는 진짜 뒤로가기(이전 화면·스크롤 유지), 직접 URL 로 들어온 경우는 홈으로
  const back = useCallback(() => { if (idx() > 0) history.back(); else navigate({ tab: "home", code: null }, true); }, [navigate]);
  return { route, navigate, back };
}
