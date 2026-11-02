import json
import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pgtest import clean

from stockwatch import db, marketdb, us_positions, us_review, usmarket

H = {"X-Stockwatch": "1"}


def setup_function():
    db.use(clean())


def teardown_function():
    db.use(None)


def bars(n=140, end="2026-10-29", close0=100.0, step=0.3, volume=3_000_000.0):
    idx = pd.bdate_range(end=end, periods=n)
    c = close0 + np.arange(n) * step + 3.0 * np.sin(np.arange(n))
    return pd.DataFrame({"open": c * 0.999, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": volume}, index=idx)


def strict(r):
    return json.loads(r.data, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))


@pytest.fixture
def offline(monkeypatch):
    """야후 호출 없이 AAA 만 시세가 있는 것처럼 한다."""
    monkeypatch.setattr(usmarket, "_collect_meta", lambda s: None)
    monkeypatch.setattr(usmarket, "fetch_bars", lambda syms, **k: ({s: bars() for s in syms if s == "AAA"}, {s: "데이터 없음" for s in syms if s != "AAA"}))


# -- 입력 검증 ----------------------------------------------------------------------------------
@pytest.mark.parametrize("args", [
    ("", 1, 10), ("toolong", 1, 10), ("A1", 1, 10), ("AAA", 0, 10), ("AAA", -1, 10), ("AAA", "x", 10), ("AAA", float("nan"), 10),
    ("AAA", float("inf"), 10), ("AAA", 1, 0), ("AAA", 1, -5), ("AAA", 1, "abc"), ("AAA", 1e12, 10), ("AAA", 1, 1e9),
])
def test_validate_rejects_bad_input(args):
    with pytest.raises(ValueError):
        us_positions.validate(*args)


def test_validate_dates_and_normalization():
    today = date(2026, 10, 30)
    v = us_positions.validate(" soun ", "2.5", "11.20", "2026-10-01", "  메모  ", today=today)
    assert v == {"symbol": "SOUN", "qty": 2.5, "avg_price": 11.2, "opened_on": date(2026, 10, 1), "note": "메모"}
    assert us_positions.validate("AAA", 1, 1, None, today=today)["opened_on"] is None
    assert us_positions.validate("AAA", 1, 1, "", today=today)["opened_on"] is None
    for bad in ("2026-13-01", "10/01/2026", "2026-10-31"):                       # 형식 오류, 미래
        with pytest.raises(ValueError):
            us_positions.validate("AAA", 1, 1, bad, today=today)
    assert len(us_positions.validate("AAA", 1, 1, note="x" * 500)["note"]) == us_positions.MAX_NOTE


# -- 저장 ---------------------------------------------------------------------------------------
def test_register_adds_watch_overwrites_and_lists(offline):
    p = us_positions.register("aaa", 3, 100, "2026-10-01", "첫 매수", name="Aaa")
    assert p["symbol"] == "AAA" and p["qty"] == 3 and "AAA" in usmarket.watch_symbols()
    us_positions.register("AAA", 5.5, 95)                                          # 같은 티커는 덮어쓴다 (평단·수량 수정)
    rows = us_positions.list_positions()
    assert len(rows) == 1 and rows[0]["qty"] == 5.5 and rows[0]["avg_price"] == 95 and rows[0]["opened_on"] is None
    us_positions.delete("AAA")
    assert us_positions.list_positions() == [] and "AAA" in usmarket.watch_symbols()    # 보유만 지우고 관심 종목은 남긴다
    with pytest.raises(KeyError):
        us_positions.delete("AAA")


def test_register_with_unknown_ticker_saves_nothing(offline):
    with pytest.raises(ValueError):
        us_positions.register("NOPE", 1, 10)
    assert us_positions.list_positions() == [] and usmarket.watch_symbols() == []


def test_register_over_watch_limit_saves_nothing(offline, monkeypatch):
    monkeypatch.setattr(usmarket, "MAX_WATCH", 0)
    with pytest.raises(ValueError):
        us_positions.register("AAA", 1, 10)
    assert us_positions.list_positions() == []


def test_db_constraints_reject_nonpositive_values(offline):
    us_positions.register("AAA", 1, 10)
    with pytest.raises(Exception):
        db.get().execute("UPDATE us_positions SET qty=0 WHERE symbol='AAA'")


# -- 판단 조립 ----------------------------------------------------------------------------------
def test_judgments_without_market_data_hold_verdicts_and_do_not_crash(offline):
    us_positions.register("AAA", 2, 90)
    marketdb.upsert_prices("AAA", bars())
    j = us_review.judgments()
    assert j["market"]["ok"] is None
    s = j["stocks"][0]
    assert s["symbol"] == "AAA" and s["buy"]["verdict"]["code"] == "na"            # 시장·상대강도를 몰라 보류
    assert s["position"]["qty"] == 2 and s["sell"]["verdict"]["code"] in {"hold", "caution", "sell"}


def test_judgments_with_market_data_and_pending_symbol(offline):
    usmarket.add_watch("BBB", "B")                                                  # 시세 없는 관심 종목
    us_positions.register("AAA", 2, 90)
    marketdb.upsert_prices("AAA", bars())
    marketdb.upsert_prices("^IXIC", bars(close0=1000.0, step=0.05))
    marketdb.upsert_prices("^VIX", bars(close0=16.0, step=0.0))
    j = us_review.judgments()
    assert j["market"]["ok"] is True
    by = {s["symbol"]: s for s in j["stocks"]}
    assert by["AAA"]["buy"]["verdict"]["code"] in {"review", "watch", "blocked"}
    assert by["AAA"]["sell"]["pnl_pct"] == pytest.approx((by["AAA"]["sell"]["price"] / 90 - 1) * 100)
    assert by["BBB"]["buy"]["verdict"]["code"] == "na" and by["BBB"]["sell"] is None and by["BBB"]["position"] is None


# -- API ----------------------------------------------------------------------------------------
def test_api_guard_flow_and_strict_json(offline):
    from stockwatch.web import app
    c = app.test_client()
    marketdb.upsert_prices("^IXIC", bars(close0=1000.0, step=0.05))
    marketdb.upsert_prices("^VIX", bars(close0=16.0, step=0.0))
    body = {"qty": 4, "avg_price": 95.5, "opened_on": "2026-10-01", "note": "n", "name": "Aaa"}
    assert c.put("/api/us/positions/AAA", json=body).status_code == 403            # 상태 변경은 X-Stockwatch 헤더 필수
    assert c.delete("/api/us/positions/AAA").status_code == 403
    r = c.put("/api/us/positions/AAA", json=body, headers=H)
    assert r.status_code == 200 and strict(r)["opened_on"] == "2026-10-01"
    assert strict(c.get("/api/us/positions"))[0]["avg_price"] == 95.5
    j = strict(c.get("/api/us/judgment"))
    assert j["stocks"][0]["sell"]["verdict"]["code"] in {"hold", "caution", "sell"} and "미검증" in j["note"]
    # 보유 중인 종목은 관심 종목에서 못 뺀다. 보유를 먼저 지우면 뺄 수 있다
    r = c.delete("/api/us/watchlist/AAA", headers=H)
    assert r.status_code == 409 and "보유" in strict(r)["error"]
    assert c.delete("/api/us/positions/AAA", headers=H).status_code == 200
    assert c.delete("/api/us/positions/AAA", headers=H).status_code == 404
    assert c.delete("/api/us/watchlist/AAA", headers=H).status_code == 200


@pytest.mark.parametrize("body", [{}, {"qty": 0, "avg_price": 10}, {"qty": 1, "avg_price": "x"}, {"qty": 1, "avg_price": 10, "opened_on": "2099-01-01"}])
def test_api_rejects_bad_body_with_400_and_saves_nothing(offline, body):
    from stockwatch.web import app
    r = app.test_client().put("/api/us/positions/AAA", json=body, headers=H)
    assert r.status_code == 400 and strict(r)["error"]
    assert us_positions.list_positions() == []


def test_api_bad_ticker_in_path_is_400(offline):
    from stockwatch.web import app
    assert app.test_client().put("/api/us/positions/TOOLONGX", json={"qty": 1, "avg_price": 1}, headers=H).status_code == 400


def test_us_judgment_code_never_touches_orders():
    """미국 판단은 화면 표시 전용이다: 주문·토스·제안 코드를 import 하지 않는다 (docstring 의 설명 문구는 무관)."""
    import ast
    root = Path(__file__).resolve().parent.parent / "stockwatch"
    for name in ("us_judgment.py", "us_review.py", "us_positions.py"):
        mods = set()
        for n in ast.walk(ast.parse((root / name).read_text())):
            if isinstance(n, ast.Import):
                mods |= {a.name for a in n.names}
            elif isinstance(n, ast.ImportFrom):
                mods |= {(n.module or "") + "." + a.name for a in n.names} | {n.module or ""}
        assert not any("trading" in m or "toss" in m for m in mods), f"{name} 이(가) 주문 코드를 가져옴: {sorted(mods)}"
