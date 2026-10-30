import { useCallback, useEffect, useState, type MouseEvent } from "react";
import { fetchDashboard, fetchStatus, fetchTrading, startRefresh } from "./api";
import { tabHref, useRoute, type Tab } from "./router";
import type { Dashboard } from "./types";
import AddSheet from "./components/AddSheet";
import HomePage from "./components/HomePage";
import MarketPage from "./components/MarketPage";
import SectorsPage from "./components/SectorsPage";
import SettingsPage from "./components/SettingsPage";
import StockPage from "./components/StockPage";
import TradingPage from "./components/TradingPage";
import UsPage from "./components/UsPage";

const TABS: [Tab, string, string][] = [["home", "홈", "◉"], ["sector", "업종", "▦"], ["market", "시장", "▤"], ["us", "미국", "◍"], ["trade", "트레이딩", "⇄"], ["settings", "설정", "⚙"]];

export default function App() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { route, navigate, back } = useRoute();
  const tab = route.tab, selected = route.code;
  const [adding, setAdding] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [pending, setPending] = useState(0);

  const show = useCallback((m: string) => { setToast(m); setTimeout(() => setToast(null), 3200); }, []);
  const load = useCallback(() => {
    fetchDashboard().then((d) => { setData(d); setError(null); }).catch((e) => setError(String(e)));
    fetchTrading().then((t) => setPending(t.pending.length)).catch(() => undefined);
  }, []);
  useEffect(load, [load]);
  const stockName = data?.results.find((r) => r.code === route.code)?.name;
  useEffect(() => { document.title = `${stockName ?? TABS.find(([k]) => k === route.tab)![1]} · Stockwatch`; }, [route.tab, stockName]);
  // 주소창에 없는 종목 코드(삭제됐거나 오타)가 들어오면 홈으로 돌려보낸다(이력 대체)
  useEffect(() => {
    if (data && selected && !data.results.some((r) => r.code === selected)) { navigate({ tab: "home", code: null }, true); show("관심 종목에 없는 종목이에요"); }
  }, [data, selected, navigate, show]);

  // 갱신 중에는 2초마다 상태 확인, 끝나면 다시 불러온다
  const running = data?.status.running ?? false;
  useEffect(() => {
    if (!running) return;
    const t = setInterval(async () => { try { if (!(await fetchStatus()).running) load(); } catch { /* 다음 주기에 재시도 */ } }, 2000);
    return () => clearInterval(t);
  }, [running, load]);

  if (error && !data) return <div className="app"><p className="notice err" style={{ marginTop: 40 }}>서버에 연결하지 못했어요: {error}</p></div>;
  if (!data) return <div className="app"><p className="empty" style={{ marginTop: 80 }}>불러오는 중…</p></div>;

  const sel = data.results.find((r) => r.code === selected);
  const openStock = (code: string) => navigate({ tab: "home", code });
  const failed = data.status.steps.filter((s) => !s.ok);
  const lastRun = data.status.last_run ? data.status.last_run.slice(5, 16).replace("-", "/") : null;
  const statusText = running ? "데이터 갱신 중…" : lastRun ? `${lastRun} 기준` : "아직 갱신 전이에요";
  const refresh = async () => { await startRefresh(); load(); show("데이터를 갱신하고 있어요"); };
  const go = (t: Tab) => { navigate({ tab: t, code: null }); if (t === "trade") load(); };
  // 일반 클릭은 앱 안 이동, Ctrl/⌘/가운데 클릭은 브라우저 기본(새 탭)
  const onNav = (t: Tab) => (e: MouseEvent) => { if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return; e.preventDefault(); go(t); };
  const title = TABS.find(([k]) => k === tab)![1];
  const RefreshBtn = <button className={`icon-btn${running ? " spin" : ""}`} onClick={refresh} disabled={running} aria-label="데이터 갱신" title="데이터 갱신">↻</button>;

  return (
    <>
      {/* 웹: 상단 내비게이션 */}
      <nav className="topnav"><div className="in">
        <a className="brand" href={tabHref("home")} onClick={onNav("home")} aria-label="홈으로 이동">Stockwatch</a>
        {TABS.map(([k, l]) => (
          <a key={k} href={tabHref(k)} className={`navlink${tab === k ? " on" : ""}`} aria-current={tab === k ? "page" : undefined} onClick={onNav(k)}>{l}{k === "trade" && pending > 0 && <span className="badge">{pending}</span>}</a>
        ))}
        <span className="sp" /><span className="stat-t">{statusText}</span>{RefreshBtn}
      </div></nav>

      <div className="app">
        {tab === "home" && sel ? (
          <StockPage r={sel} onBack={back} onToast={show} onRemoved={() => { navigate({ tab: "home", code: null }, true); load(); }} />
        ) : (
          <>
            <header className="top mob-head"><div><h1>{title}</h1><span className="sub">{statusText}</span></div>{RefreshBtn}</header>
            <div className="page-h"><h1>{title}</h1></div>
            {failed.map((s) => <p key={s.name} className="notice">⚠ {s.name}: {s.error}</p>)}
            {tab === "home" && <HomePage data={data} onOpen={openStock} onAdd={() => setAdding(true)} />}
            {tab === "sector" && <SectorsPage onOpen={openStock} />}
            {tab === "market" && <MarketPage />}
            {tab === "us" && <UsPage onToast={show} />}
            {tab === "trade" && <TradingPage onChanged={load} />}
            {tab === "settings" && <SettingsPage onChanged={load} />}
          </>
        )}
      </div>

      {/* 폰: 하단 탭바 */}
      <nav className="tabbar"><div className="in">
        {TABS.map(([k, l, ic]) => (
          <a key={k} href={tabHref(k)} className={tab === k ? "on" : ""} aria-current={tab === k ? "page" : undefined} onClick={onNav(k)}><span className="ic">{ic}</span>{l}{k === "trade" && pending > 0 && <span className="badge">{pending}</span>}</a>
        ))}
      </div></nav>

      {adding && <AddSheet onClose={() => setAdding(false)} onAdded={(n) => { setAdding(false); show(`${n}을(를) 추가했어요. 시세를 가져오는 중이에요`); load(); setTimeout(load, 2500); }} />}
      {toast && <div className="toast">{toast}</div>}
    </>
  );
}
