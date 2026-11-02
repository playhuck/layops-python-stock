import { useCallback, useEffect, useState, type MouseEvent } from "react";
import { fetchDashboard, fetchHoldings, fetchStatus, fetchTrading, setHoldingsSource, startRefresh } from "./api";
import { tabHref, useRoute, type Tab } from "./router";
import type { Dashboard, HoldingsSource, HoldingsView } from "./types";
import AddSheet from "./components/AddSheet";
import HomePage, { type Market } from "./components/HomePage";
import MarketPage from "./components/MarketPage";
import SectorsPage from "./components/SectorsPage";
import SettingsPage from "./components/SettingsPage";
import StockPage from "./components/StockPage";
import TradingPage from "./components/TradingPage";
import UsPage from "./components/UsPage";
import UsStockPage from "./components/UsStockPage";

// 영문 티커(1~5자)는 미국 종목, 6자리 숫자는 한국 종목 코드
const isUs = (code: string) => /^[A-Za-z]{1,5}$/.test(code);

const TABS: [Tab, string, string][] = [["home", "홈", "◉"], ["sector", "업종", "▦"], ["market", "시장", "▤"], ["us", "미국", "◍"], ["trade", "트레이딩", "⇄"], ["settings", "설정", "⚙"]];

export default function App() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { route, navigate, back } = useRoute();
  const tab = route.tab, selected = route.code;
  const [adding, setAdding] = useState<Market | null>(null);                    // 종목 추가 시트(열린 탭)
  const [market, setMarket] = useState<Market>(() => { try { return localStorage.getItem("home_market") === "us" ? "us" : "kr"; } catch { return "kr"; } });
  const [usVersion, setUsVersion] = useState(0);                                  // 미국 관심 종목이 바뀌면 올려서 홈 미국 표를 다시 읽게 한다
  const changeMarket = useCallback((m: Market) => { setMarket(m); try { localStorage.setItem("home_market", m); } catch { /* 저장 불가여도 화면은 동작 */ } }, []);
  const [toast, setToast] = useState<string | null>(null);
  const [pending, setPending] = useState(0);
  const [holdings, setHoldings] = useState<HoldingsView | null>(null);

  const show = useCallback((m: string) => { setToast(m); setTimeout(() => setToast(null), 3200); }, []);
  const loadHoldings = useCallback(() => { fetchHoldings().then(setHoldings).catch(() => setHoldings(null)); }, []);
  const changeSource = useCallback((s: HoldingsSource) => { setHoldingsSource(s).then(loadHoldings).catch((e) => show((e as Error).message)); }, [loadHoldings, show]);
  const load = useCallback(() => {
    loadHoldings();
    fetchDashboard().then((d) => { setData(d); setError(null); }).catch((e) => setError(String(e)));
    fetchTrading().then((t) => setPending(t.pending.length)).catch(() => undefined);
  }, [loadHoldings]);
  useEffect(load, [load]);
  useEffect(() => { if (selected && isUs(selected)) changeMarket("us"); }, [selected, changeMarket]);     // 주소창으로 미국 종목을 직접 열어도 뒤로 가면 홈의 미국 탭
  const stockName = data?.results.find((r) => r.code === route.code)?.name;
  useEffect(() => { document.title = `${stockName ?? TABS.find(([k]) => k === route.tab)![1]} · Stockwatch`; }, [route.tab, stockName]);
  // 주소창에 없는 종목 코드(삭제됐거나 오타)가 들어오면 홈으로 돌려보낸다(이력 대체)
  useEffect(() => {
    if (data && selected && !isUs(selected) && !data.results.some((r) => r.code === selected)) { navigate({ tab: "home", code: null }, true); show("관심 종목에 없는 종목이에요"); }
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
  const usSel = selected && isUs(selected) ? selected.toUpperCase() : null;
  const openStock = (code: string) => navigate({ tab: "home", code });
  const openUs = (symbol: string) => { changeMarket("us"); navigate({ tab: "home", code: symbol }); };
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
        {tab === "home" && usSel ? (
          <UsStockPage symbol={usSel} holdings={holdings} onBack={back} onToast={show} onRemoved={() => { setUsVersion((v) => v + 1); navigate({ tab: "home", code: null }, true); }} />
        ) : tab === "home" && sel ? (
          <StockPage r={sel} holdings={holdings} onSource={changeSource} onHoldings={loadHoldings} onBack={back} onToast={show} onRemoved={() => { navigate({ tab: "home", code: null }, true); load(); }} />
        ) : (
          <>
            <header className="top mob-head"><div><h1>{title}</h1><span className="sub">{statusText}</span></div>{RefreshBtn}</header>
            <div className="page-h"><h1>{title}</h1></div>
            {failed.map((s) => <p key={s.name} className="notice">⚠ {s.name}: {s.error}</p>)}
            {tab === "home" && <HomePage data={data} holdings={holdings} onSource={changeSource} onOpen={openStock} onAdd={(m) => setAdding(m)} mkt={market} onMarket={changeMarket} usVersion={usVersion} onOpenUs={openUs} onToast={show} />}
            {tab === "sector" && <SectorsPage onOpen={openStock} />}
            {tab === "market" && <MarketPage />}
            {tab === "us" && <UsPage onToast={show} holdings={holdings} onSource={changeSource} onHoldings={loadHoldings} onOpenHome={() => { changeMarket("us"); go("home"); }} />}
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

      {adding && <AddSheet market={adding} onClose={() => setAdding(null)} onToast={show} onUsAdded={(sym) => { show(`${sym}을(를) 추가했어요`); setUsVersion((v) => v + 1); loadHoldings(); }} onAdded={(n) => { setAdding(null); show(`${n}을(를) 추가했어요. 시세를 가져오는 중이에요`); load(); setTimeout(load, 2500); }} />}
      {toast && <div className="toast">{toast}</div>}
    </>
  );
}
