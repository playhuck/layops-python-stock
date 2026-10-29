"""웹 API + React 정적 서빙: python -m stockwatch.web  → http://127.0.0.1:5000 (로컬 전용 바인딩)
갱신 시점: ① 앱 기동 시 1회  ② 켜져 있는 동안 매일 01:00  ③ 화면의 '데이터 갱신' 버튼
프런트엔드는 web/ (React+Vite). 빌드 산출물 web/dist 가 있으면 / 에서 서빙한다."""
from dataclasses import asdict

import numpy as np
import json
import time
from functools import wraps

from flask import Flask, abort, jsonify, request, send_from_directory

from . import collector, indicators as ind
from . import config as cfgmod, marketflow, sectors, stockseries, usersettings
from .cli import run, ROOT, summarize_market, load_config
from .providers import DbProvider

DIST = ROOT / "web" / "dist"
app = Flask(__name__, static_folder=None)
app.json.sort_keys = False  # 투자자 구분 등 딕셔너리의 의도한 순서를 JSON 응답에서 유지 (기본값은 키 가나다순 정렬)


def _provider() -> DbProvider:
    return DbProvider()


@app.get("/api/dashboard")
def dashboard():
    results = [asdict(r) for r in run()]
    have = {r["code"] for r in results}
    # 방금 추가돼 아직 시세를 못 받았거나(수집 중) 수집에 실패한 종목
    waiting = [{"code": i["code"], "name": i["name"]} for i in load_config()["watchlist"] if i["code"] not in have]
    return jsonify({"results": results, "waiting": waiting, "market": summarize_market(_provider()),
                    "status": collector.status})


@app.get("/api/status")
def status():
    return jsonify(collector.status)


@app.post("/api/refresh")
def refresh():
    started = collector.refresh_async("manual")
    return jsonify({"started": started}), (202 if started else 409)


@app.get("/api/series/<code>")
def series(code: str):
    """차트용 지표 시계열 (최근 days 일)."""
    if code not in {i["code"] for i in load_config()["watchlist"]}:
        abort(404)
    try:
        df = ind.add_all(_provider().prices(code)).tail(120)
    except FileNotFoundError:
        abort(404)
    df = df.reset_index().rename(columns={"index": "date"})
    df["date"] = df["date"].dt.strftime("%Y-%m-%d")
    rows = df.astype(object).where(df.notna(), None).to_dict("records")  # NaN -> null (JSON 유효성)
    return jsonify(rows)


def guarded(fn):
    """상태 변경(주문 포함) POST/PUT 은 커스텀 헤더 필수: 다른 사이트가 사용자의 브라우저로 몰래 호출하는 것(CSRF)을 막는다.
    (커스텀 헤더는 CORS 사전요청이 필요하고, 이 서버는 CORS 를 허용하지 않는다.)"""
    @wraps(fn)
    def inner(*a, **k):
        if request.headers.get("X-Stockwatch") != "1":
            abort(403)
        return fn(*a, **k)
    return inner


def _push():
    from .trading import push
    return push


def _engine():
    from .trading import service
    return service.engine()


@app.get("/api/trading/state")
def trading_state():
    e = _engine(); t = e.t
    wl = load_config()["watchlist"]
    cls = sectors.classify([i["code"] for i in wl], e.store.db)
    rules = [{**e.store.rule(i["code"]), "name": i["name"], "group": cls.get(i["code"], {}).get("label") or i.get("group")} for i in wl]
    return jsonify({
        "push": {"server_configured": _push().configured(), "problem": _push().problem(), "devices": e.store.push_token_count()},
        "toss": e.gw.status(), "risk": e.risk_state(), "rules": rules,
        "limits": {k: t.get(k) for k in ("capital_krw", "risk_per_trade_pct", "max_gap_loss_pct", "gap_assumption_pct",
                                          "monthly_kill_pct", "daily_buy_limit_krw", "max_positions", "stop_atr", "target_atr",
                                          "trail_atr", "buy_min_score", "sell_min_score", "regime_filter")},
        "pending": e.store.proposals("pending"), "needs_review": e.store.proposals("needs_review"), "unreconciled": e.store.unreconciled(), "history": [p for p in e.store.proposals(limit=60) if p["status"] != "pending"],
        "screen": json.loads(e.store.get("last_screen", "{}") or "{}")})


@app.put("/api/trading/rules/<code>")
@guarded
def trading_rule(code: str):
    if code not in {i["code"] for i in load_config()["watchlist"]}:
        abort(404)
    b = request.get_json(force=True) or {}
    try:
        budget = int(b.get("budget_krw", 0)); assert budget >= 0
        opt = lambda k: (None if b.get(k) in (None, "") else float(b[k]))
        stop, target = opt("stop_atr"), opt("target_atr")
        assert (stop is None or 0.5 <= stop <= 10) and (target is None or 0.5 <= target <= 20)
    except (ValueError, AssertionError, TypeError):
        return jsonify({"error": "입력값이 올바르지 않습니다 (한도금액 ≥ 0, ATR 배수 0.5~10/20)"}), 400
    e = _engine()
    e.store.save_rule(code, budget_krw=budget, live=bool(b.get("live")), protect=bool(b.get("protect", True)),
                      stop_atr=stop, target_atr=target)
    e.store.audit("rule", {"code": code, **b})
    return jsonify({"ok": True})


@app.post("/api/trading/live")
@guarded
def trading_live():
    """전역 실주문 스위치. 켤 때는 확인 문구를 정확히 입력해야 하고, 토스 연결과 킬스위치 해제가 선행되어야 한다."""
    b = request.get_json(force=True) or {}
    e = _engine()
    if b.get("enabled"):
        if b.get("confirm") != "실주문":
            return jsonify({"error": "확인 문구 '실주문' 을 입력하세요"}), 400
        if e.killed():
            return jsonify({"error": "킬스위치가 작동 중입니다. 원인 점검 후 해제하세요"}), 409
        st = e.gw.status()
        if not st["connected"]:
            return jsonify({"error": f"토스 연결 실패: {st['error'] or 'API 키 미설정'}"}), 409
        if st["days_left"] is not None and st["days_left"] < 0:
            return jsonify({"error": "토스 API 키가 만료되었습니다"}), 409
    e.store.set("live_enabled", "1" if b.get("enabled") else "0")
    e.store.audit("live_switch", {"enabled": bool(b.get("enabled"))})
    return jsonify({"ok": True})


@app.post("/api/trading/kill/reset")
@guarded
def trading_kill_reset():
    if (request.get_json(force=True) or {}).get("confirm") != "점검완료":
        return jsonify({"error": "확인 문구 '점검완료' 를 입력하세요"}), 400
    _engine().reset_kill()
    return jsonify({"ok": True})


@app.post("/api/trading/proposals/<int:pid>/<action>")
@guarded
def trading_decide(pid: int, action: str):
    if action not in ("approve", "reject"):
        abort(404)
    e = _engine()
    r = e.approve(pid) if action == "approve" else e.reject(pid)
    return jsonify(r), (200 if r.get("ok") else 409)


@app.post("/api/push/register")
@guarded
def push_register():
    tok = (request.get_json(force=True) or {}).get("token", "")
    if not isinstance(tok, str) or not (50 <= len(tok) <= 400):
        return jsonify({"error": "토큰 형식 오류"}), 400
    e = _engine(); e.store.add_push_token(tok); e.store.audit("push_register")
    return jsonify({"ok": True})


@app.post("/api/push/test")
@guarded
def push_test():
    from .trading import push
    return jsonify(push.send(_engine().store, "테스트 알림", "푸시 연결이 정상입니다"))


@app.post("/api/trading/proposals/<int:pid>/resolve")
@guarded
def trading_resolve(pid: int):
    b = request.get_json(force=True) or {}
    r = _engine().resolve_review(pid, str(b.get("resolution", "")), b.get("order_id"))
    return jsonify(r), (200 if r.get("ok") else 409)


@app.get("/api/trading/proposals/<int:pid>/candidates")
def trading_candidates(pid: int):
    return jsonify(_engine().review_candidates(pid))


@app.post("/api/trading/unreconciled/<code>/dismiss")
@guarded
def trading_unreconciled_dismiss(code: str):
    e = _engine(); e.store.clear_unreconciled(code); e.store.audit("unreconciled_dismissed", {"code": code})
    return jsonify({"ok": True})


@app.post("/api/trading/sync")
@guarded
def trading_sync():
    return jsonify({"notes": _engine().sync_orders()})


_SEARCH_CACHE: dict = {}


def _mark_added(rows: list) -> list:
    have = {i["code"] for i in load_config()["watchlist"]}  # 캐시된 검색 결과여도 '추가됨' 표시는 항상 최신
    return [{"code": x["short_code"], "name": x["codeName"], "market": x["marketCode"], "added": x["short_code"] in have}
            for x in rows if x.get("marketCode") in ("STK", "KSQ")][:15]


@app.get("/api/stocks/<code>/series")
def stock_series(code: str):
    """종목 상세 차트용 시계열 (일 단위 수급/외국인 보유율/공매도). days: 5~120."""
    if code not in {i["code"] for i in load_config()["watchlist"]}:
        abort(404)
    try:
        days = max(5, min(int(request.args.get("days", 60)), 120))
    except ValueError:
        days = 60
    return jsonify(stockseries.build(_provider(), code, days))


def _holdings_value(e) -> dict | None:
    """보유 종목별 평가액(원). 토스 미연결/실패면 None(비중을 모른다고 표시)."""
    if not e.gw.configured():
        return None
    try:
        return {h["symbol"]: h["qty"] * h["last"] for h in e.gw.kr_holdings()}
    except Exception:
        return None


def _live_on(e) -> bool:
    return e.store.get("live_enabled", "0") == "1"


def _sector_rows(cfg: dict, e) -> list[dict]:
    """설정/업종 화면용: 내 종목이 속한 대분류·중분류와 한도·보유 비중을 한 줄씩 펼친다."""
    t = cfg["trading"]
    ov = sectors.overview(cfg["watchlist"], _holdings_value(e), float(t.get("capital_krw", 0)), cfg["sector_limits"], float(t.get("max_group_pct", 40)), database=e.store.db)
    rows = []
    for n in ov:
        rows.append({**{k: n[k] for k in ("id", "name", "depth", "size", "exposure_pct", "limit_pct", "effective_limit_pct")}, "parent": None, "stock_count": len(n["stocks"])})
        rows += [{**{k: c[k] for k in ("id", "name", "depth", "size", "exposure_pct", "limit_pct", "effective_limit_pct")}, "parent": n["id"], "stock_count": len(c["stocks"])} for c in n["children"]]
    return rows


@app.get("/api/settings")
def settings_get():
    e = _engine(); cfg = load_config(); t = cfg["trading"]
    return jsonify({
        "values": {k: t.get(k) for k in usersettings.FIELDS}, "defaults": {k: cfg["trading_defaults"].get(k) for k in usersettings.FIELDS},
        "overridden": sorted(usersettings.get_overrides(e.store.db)),
        "fields": {k: {"type": v[0], "min": v[1], "max": v[2], "label": v[3], "unit": v[4]} for k, v in usersettings.FIELDS.items()},
        "live_on": _live_on(e), "confirm_text": usersettings.CONFIRM,
        "gap": {"max_gap_loss_pct": float(t.get("max_gap_loss_pct", 3)), "gap_assumption_pct": float(t.get("gap_assumption_pct", 15))},
        "sector_rows": _sector_rows(cfg, e),
        "sector_options": [{"id": n["id"], "name": n["name"], "depth": 0, "parent": None} for n in sectors.tree(e.store.db)]
                          + [{**c} for n in sectors.tree(e.store.db) for c in n["children"]],
    })


@app.put("/api/settings")
@guarded
def settings_put():
    b = request.get_json(force=True) or {}
    e = _engine(); t = load_config()["trading"]
    try:
        changes = usersettings.save({k: t.get(k) for k in usersettings.FIELDS}, b.get("values") or {}, _live_on(e), b.get("confirm"), e.store.db)
    except ValueError as ex:
        return jsonify({"error": str(ex)}), 400
    except PermissionError as ex:
        return jsonify({"error": str(ex), "needs_confirm": True}), 409
    e.store.audit("settings_changed", changes)
    return jsonify({"ok": True, "changes": changes})


@app.put("/api/settings/sector-limit")
@guarded
def settings_sector_limit():
    b = request.get_json(force=True) or {}
    e = _engine(); t = load_config()["trading"]
    try:
        res = usersettings.set_sector_limit(str(b.get("sector_id", "")), b.get("max_pct"), float(t.get("max_group_pct", 40)), _live_on(e), b.get("confirm"), e.store.db)
    except ValueError as ex:
        return jsonify({"error": str(ex)}), 400
    except PermissionError as ex:
        return jsonify({"error": str(ex), "needs_confirm": True}), 409
    e.store.audit("sector_limit_changed", {"sector_id": b.get("sector_id"), **res})
    return jsonify({"ok": True, **res})


@app.post("/api/settings/reset")
@guarded
def settings_reset():
    e = _engine()
    if _live_on(e) and (request.get_json(force=True) or {}).get("confirm") != usersettings.CONFIRM:
        return jsonify({"error": "실주문 모드에서는 기본값 복원에도 확인이 필요해요 (한도가 느슨해질 수 있어요)", "needs_confirm": True}), 409
    usersettings.reset(e.store.db); e.store.audit("settings_reset")
    return jsonify({"ok": True})


_PERF_CACHE: dict = {}


@app.get("/api/sectors/overview")
def sectors_overview():
    e = _engine(); cfg = load_config(); t = cfg["trading"]
    cats = sectors.overview(cfg["watchlist"], _holdings_value(e), float(t.get("capital_krw", 0)), cfg["sector_limits"], float(t.get("max_group_pct", 40)),
                            show_all=request.args.get("all") == "1", database=e.store.db)
    synced = e.store.get(sectors.SYNCED_KEY, "")
    return jsonify({"categories": cats, "synced_at": synced or None, "capital": t.get("capital_krw", 0), "default_limit_pct": t.get("max_group_pct", 40)})


@app.get("/api/sectors/<sector_id>/performance")
def sector_performance(sector_id: str):
    """업종 기간별 등락률(토스 섹터 시세). 10분 캐시."""
    if not sector_id.isdigit():
        abort(404)
    hit = _PERF_CACHE.get(sector_id)
    if hit and time.time() - hit[0] < 600:
        return jsonify(hit[1])
    e = _engine()
    if not e.gw.configured():
        return jsonify({"error": "토스 API 키가 없어요"}), 409
    try:
        rates = [{"duration": r["duration"], "rate": None if r.get("changeRate") is None else round(float(r["changeRate"]) * 100, 2)}
                 for r in e.gw.client.sector_performance(sector_id)]
    except Exception as ex:
        return jsonify({"error": f"{type(ex).__name__}: {str(ex)[:100]}"}), 502
    _PERF_CACHE[sector_id] = (time.time(), {"rates": rates})
    return jsonify({"rates": rates})


@app.get("/api/market/flow")
def market_flow():
    """일 단위 시장 수급. days: 1~60 (기본 20)."""
    try:
        days = max(1, min(int(request.args.get("days", 20)), 60))
    except ValueError:
        days = 20
    return jsonify(marketflow.daily(_provider(), days))


@app.get("/api/stocks/search")
def stock_search():
    """종목명/코드 검색 (KRX 종목 검색, 로그인 불필요). 코스피/코스닥 보통주·우선주 등 KRX 가 돌려주는 상장 종목."""
    import requests
    q = (request.args.get("q") or "").strip()
    if len(q) < 1 or len(q) > 20:
        return jsonify([])
    hit = _SEARCH_CACHE.get(q)
    if hit and time.time() - hit[0] < 600:  # KRX 검색이 3~4초 걸려 같은 검색어는 10분간 재사용
        return jsonify(_mark_added(hit[1]))
    try:
        r = requests.post("https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd", timeout=10, data={
            "bld": "dbms/comm/finder/finder_stkisu", "locale": "ko_KR", "mktsel": "ALL", "typeNo": "0", "searchText": q},
            headers={"Referer": "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201",
                     "User-Agent": "stockwatch/0.1 (personal use)"})
        rows = r.json().get("block1", [])
        _SEARCH_CACHE[q] = (time.time(), rows)
        if len(_SEARCH_CACHE) > 200:
            _SEARCH_CACHE.pop(next(iter(_SEARCH_CACHE)))
    except (requests.RequestException, ValueError):
        return jsonify({"error": "KRX 종목 검색에 실패했습니다"}), 502
    return jsonify(_mark_added(rows))


@app.post("/api/watchlist")
@guarded
def watchlist_add():
    b = request.get_json(force=True) or {}
    try:
        cfgmod.add_stock(load_config(), str(b.get("code", "")).strip().upper(), str(b.get("name", "")), str(b.get("market", "")), b.get("group"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    collector.refresh_async(f"add:{b.get('code')}")  # 새 종목 시세/수급을 바로 수집
    return jsonify({"ok": True})


@app.delete("/api/watchlist/<code>")
@guarded
def watchlist_remove(code: str):
    why = _engine().removal_blockers(code)
    if why:
        return jsonify({"error": " / ".join(why)}), 409
    try:
        cfgmod.remove_stock(load_config(), code)
    except KeyError:
        abort(404)
    return jsonify({"ok": True})


@app.get("/", defaults={"path": ""})
@app.get("/<path:path>")
def spa(path: str):
    if not DIST.exists():
        return "프런트엔드 미빌드: cd web && npm install && npm run build (개발 시 npm run dev)", 503
    target = DIST / path
    return send_from_directory(DIST, path if path and target.is_file() else "index.html")


if __name__ == "__main__":
    collector.refresh_async("startup")
    collector.start_daily(1)
    app.run(host="127.0.0.1", port=5000, use_reloader=False)  # 리로더는 기동 갱신을 두 번 실행시킴
