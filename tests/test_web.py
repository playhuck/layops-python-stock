import json

from stockwatch.web import app

c = app.test_client()


def test_dashboard_json_strict():
    r = c.get("/api/dashboard")
    assert r.status_code == 200
    # NaN/Infinity 는 브라우저 JSON.parse 가 거부하므로 엄격 파싱으로 확인
    body = json.loads(r.data, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    assert body["results"] and "verdict" in body["results"][0]


def test_series_has_null_not_nan():
    r = c.get("/api/series/005930")
    assert r.status_code == 200
    rows = json.loads(r.data, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    assert len(rows) == 120 and "ma120" in rows[-1] and rows[-1]["ma120"] is not None


def test_series_rejects_unknown_code():
    assert c.get("/api/series/999999").status_code == 404


def test_refresh_conflict_when_running():
    from stockwatch import collector
    collector.status["running"] = True
    try:
        assert c.post("/api/refresh").status_code == 409
    finally:
        collector.status["running"] = False


def test_spa_served():
    r = c.get("/")
    assert r.status_code == 200 and b'id="root"' in r.data


def test_trading_mutations_require_guard_header():
    assert c.post("/api/trading/live", json={"enabled": True, "confirm": "실주문"}).status_code == 403
    assert c.put("/api/trading/rules/005930", json={"budget_krw": 1}).status_code == 403
    assert c.post("/api/trading/proposals/1/approve").status_code == 403
    assert c.post("/api/trading/kill/reset", json={"confirm": "점검완료"}).status_code == 403


def test_push_endpoints_guarded():
    assert c.post("/api/push/register", json={"token": "x" * 100}).status_code == 403
    assert c.post("/api/push/test").status_code == 403


def test_watchlist_mutations_guarded():
    assert c.post("/api/watchlist", json={"code": "222800", "name": "심텍", "market": "KSQ"}).status_code == 403
    assert c.delete("/api/watchlist/005930").status_code == 403


def test_search_empty_query():
    assert c.get("/api/stocks/search?q=").json == []


def test_json_responses_never_contain_nan_or_infinity():
    import json
    import numpy as np
    from stockwatch import web
    payload = {"price": float("nan"), "xs": [np.float64("nan"), float("inf"), -np.inf, 1.5], "t": (np.float32("nan"), 2), "ok": {"v": 3.25, "s": "NaN"}}
    text = web.app.json.dumps(payload)
    out = json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(AssertionError(f"비표준 JSON 상수 {c}")))   # 표준 파서처럼 NaN/Infinity 를 거부
    assert out == {"price": None, "xs": [None, None, None, 1.5], "t": [None, 2], "ok": {"v": 3.25, "s": "NaN"}}
