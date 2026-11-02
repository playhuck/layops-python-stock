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
    assert v == {"symbol": "SOUN", "qty": 2.5, "avg_price": 11.2, "opened_on": date(2026, 10, 1), "note": "메모", "entry_atr": None, "fx_buy": None}
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
    assert by["AAA"]["buy"]["verdict"]["code"] in {"review", "review_check", "watch", "blocked"}
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


# -- B단계: 진입 ATR 고정 --------------------------------------------------------------------------
from stockwatch import indicators as ind


@pytest.mark.parametrize("atr", [0, -1, "x", float("nan"), float("inf"), 11])               # 평균 매수가(10)보다 크거나 같지 않은 값, 숫자가 아닌 값
def test_validate_rejects_bad_entry_atr(atr):
    with pytest.raises(ValueError):
        us_positions.validate("AAA", 1, 10, entry_atr=atr)


def test_validate_accepts_entry_atr_and_blank_means_auto():
    assert us_positions.validate("AAA", 1, 10, entry_atr="1.5")["entry_atr"] == 1.5
    assert us_positions.validate("AAA", 1, 10, entry_atr="")["entry_atr"] is None


def test_snapshot_atr_uses_bars_before_the_buy_date_and_falls_back():
    df = bars()
    d = df.index[100].date()
    a, src = us_positions.snapshot_atr(df, d)
    assert src == "opened" and a == pytest.approx(float(ind.atr(df[df.index < pd.Timestamp(d)]).iloc[-1]))
    a2, src2 = us_positions.snapshot_atr(df, df.index[5].date())                  # 매수일 이전 시세가 모자라면 최근 ATR
    assert src2 == "registered" and a2 == pytest.approx(float(ind.atr(df).iloc[-1]))
    assert us_positions.snapshot_atr(df, None)[1] == "registered"
    assert us_positions.snapshot_atr(df.iloc[:10], None) == (None, None) and us_positions.snapshot_atr(None, None) == (None, None)


def test_register_stores_entry_atr_and_tracking_start(offline):
    df = bars()
    d = df.index[100].date()
    p = us_positions.register("AAA", 3, 100, d.isoformat())
    assert p["entry_atr_source"] == "opened" and p["tracked_from"] == d
    assert p["entry_atr"] == pytest.approx(float(ind.atr(df[df.index < pd.Timestamp(d)]).iloc[-1]))
    q = us_positions.register("AAA", 3, 100)                                         # 매수일 없음: 등록 시점 ATR, 추적은 오늘부터
    assert q["entry_atr_source"] == "registered" and q["opened_on"] is None and q["tracked_from"] is not None


def test_editing_note_or_qty_keeps_the_stored_entry_atr_but_changing_the_average_recomputes(offline):
    df = bars()
    d = df.index[100].date().isoformat()
    first = us_positions.register("AAA", 3, 100, d, "a")
    db.get().execute("UPDATE us_positions SET entry_atr=1.234, tracked_from='2026-09-01' WHERE symbol='AAA'")     # 저장된 값이 유지되는지 보려고 표시를 남긴다
    kept = us_positions.register("AAA", 9, 100, d, "메모만 바꿈")
    assert kept["entry_atr"] == 1.234 and str(kept["tracked_from"]) == "2026-09-01" and kept["qty"] == 9
    changed = us_positions.register("AAA", 9, 105, d)                                # 평균 매수가가 바뀌면(추가 매수) 다시 잡는다
    assert changed["entry_atr"] == pytest.approx(first["entry_atr"]) and changed["entry_atr"] != 1.234


def test_manual_entry_atr_wins_and_is_kept_on_unchanged_edits(offline):
    p = us_positions.register("AAA", 3, 100, entry_atr="2.5")
    assert p["entry_atr"] == 2.5 and p["entry_atr_source"] == "manual"
    again = us_positions.register("AAA", 4, 100, note="x")
    assert again["entry_atr"] == 2.5 and again["entry_atr_source"] == "manual"


def test_old_table_without_the_new_columns_is_migrated():
    import psycopg
    schema = "test_mig_" + __import__("uuid").uuid4().hex[:8]
    assert schema.startswith("test_")
    try:
        with psycopg.connect(db.Database._conninfo(), autocommit=True) as c:
            c.execute(f"CREATE SCHEMA {schema}")
            c.execute(f"CREATE TABLE {schema}.us_positions(symbol TEXT PRIMARY KEY, qty DOUBLE PRECISION NOT NULL, avg_price DOUBLE PRECISION NOT NULL, "
                      "opened_on DATE, note TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now())")
            c.execute(f"INSERT INTO {schema}.us_positions(symbol,qty,avg_price) VALUES('OLD',1,10)")
        d2 = db.Database(schema=schema)                                              # 생성 시 스키마 적용(멱등)
        try:
            row = d2.execute("SELECT symbol, entry_atr, entry_atr_source, tracked_from, fx_buy FROM us_positions").fetchone()
            assert row == {"symbol": "OLD", "entry_atr": None, "entry_atr_source": None, "tracked_from": None, "fx_buy": None}     # 옛 기록은 그대로, 새 컬럼은 비어 있다
            with pytest.raises(Exception):
                d2.execute("UPDATE us_positions SET fx_buy=0")
            with pytest.raises(Exception):
                d2.execute("UPDATE us_positions SET entry_atr=-1")
        finally:
            d2.close()
    finally:
        with psycopg.connect(db.Database._conninfo(), autocommit=True) as c:
            c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def test_api_entry_atr_roundtrip_and_judgment_uses_the_fixed_value(offline):
    from stockwatch.web import app
    c = app.test_client()
    marketdb.upsert_prices("^IXIC", bars(close0=1000.0, step=0.05))
    marketdb.upsert_prices("^VIX", bars(close0=16.0, step=0.0))
    r = c.put("/api/us/positions/AAA", json={"qty": 2, "avg_price": 130, "entry_atr": 2.0}, headers=H)
    body = strict(r)
    assert r.status_code == 200 and body["entry_atr"] == 2.0 and body["entry_atr_source"] == "manual" and body["tracked_from"]
    sell = strict(c.get("/api/us/judgment"))["stocks"][0]["sell"]
    assert sell["levels"]["entry_atr"] == 2.0 and sell["levels"]["stop"] == pytest.approx(130 - 2.0 * 2.0)
    assert any(e["label"] == "진입 ATR" and "직접 입력" in e["value"] for e in sell["evidence"])
    assert c.put("/api/us/positions/AAA", json={"qty": 2, "avg_price": 130, "entry_atr": -1}, headers=H).status_code == 400


def test_api_add_by_typed_ticker_normalizes_names_it_by_symbol_and_explains_failures(offline):
    from stockwatch.web import app
    c = app.test_client()
    r = c.post("/api/us/watchlist", json={"symbol": " aaa ", "name": ""}, headers=H)             # 화면이 보내는 형태: 공백·소문자, 이름 없음
    assert r.status_code == 200 and usmarket.watch_list() == [{"symbol": "AAA", "name": "AAA"}]
    dup = c.post("/api/us/watchlist", json={"symbol": "AAA", "name": ""}, headers=H)
    assert dup.status_code == 400 and "이미 관심 종목" in strict(dup)["error"]
    nope = c.post("/api/us/watchlist", json={"symbol": "nopex", "name": ""}, headers=H)
    assert nope.status_code == 400 and "야후에서 시세를 가져오지 못했어요" in strict(nope)["error"]
    assert usmarket.watch_symbols() == ["AAA"]                                                    # 실패한 티커는 남지 않는다


# -- 매수 시점 환율과 원화 손익 ---------------------------------------------------------------------
from stockwatch import us_judgment as uj


@pytest.mark.parametrize("fx", [0, -1, "x", float("nan"), float("inf"), 0.00073, 50, 20_000])      # 달러/원을 거꾸로 넣은 값, 자릿수 오타 포함
def test_validate_rejects_bad_fx(fx):
    with pytest.raises(ValueError):
        us_positions.validate("AAA", 1, 10, fx_buy=fx)


def test_validate_accepts_fx_and_blank_means_unknown():
    assert us_positions.validate("AAA", 1, 10, fx_buy="1350.5")["fx_buy"] == 1350.5
    assert us_positions.validate("AAA", 1, 10, fx_buy="")["fx_buy"] is None and us_positions.validate("AAA", 1, 10)["fx_buy"] is None


def test_register_stores_updates_and_clears_fx(offline):
    assert us_positions.register("AAA", 3, 100, fx_buy=1320)["fx_buy"] == 1320
    assert us_positions.register("AAA", 3, 100, fx_buy="1388.4")["fx_buy"] == 1388.4              # 같은 종목은 덮어쓴다
    assert us_positions.register("AAA", 3, 100)["fx_buy"] is None                                   # 비우면 모름으로 돌아간다


def test_krw_view_splits_the_krw_result_into_price_and_fx():
    pos = {"qty": 10, "avg_price": 100.0, "fx_buy": 1300.0}
    r = us_review.krw_view(110.0, pos, {"rate": 1400.0, "as_of": "2026-10-30", "reason": None})
    assert r["available"] and r["cost_krw"] == 1_300_000 and r["value_krw"] == 1_540_000 and r["pnl_krw"] == 240_000
    assert r["price_pct"] == pytest.approx(10.0) and r["fx_pct"] == pytest.approx((1400 / 1300 - 1) * 100)
    assert r["pnl_pct"] == pytest.approx(((1 + r["price_pct"] / 100) * (1 + r["fx_pct"] / 100) - 1) * 100)      # 곱으로 합쳐진다
    assert r["fx_buy"] == 1300.0 and r["fx_now"] == 1400.0 and r["fx_as_of"] == "2026-10-30"


def test_krw_view_can_be_negative_even_when_the_dollar_price_is_up():
    pos = {"qty": 10, "avg_price": 100.0, "fx_buy": 1400.0}                                          # 비싼 환율에 샀고 그 뒤 환율이 내려감
    r = us_review.krw_view(105.0, pos, {"rate": 1300.0, "as_of": "2026-10-30", "reason": None})
    assert r["price_pct"] == pytest.approx(5.0) and r["pnl_krw"] < 0 and r["pnl_pct"] < 0 and r["fx_pct"] < 0


def test_krw_view_explains_why_it_is_unavailable():
    fx_ok = {"rate": 1400.0, "as_of": "2026-10-30", "reason": None}
    no_buy = us_review.krw_view(110.0, {"qty": 1, "avg_price": 100.0, "fx_buy": None}, fx_ok)
    assert not no_buy["available"] and "매수 시점 환율" in no_buy["reason"] and no_buy["price_pct"] == pytest.approx(10.0)    # 달러 기준 값은 그대로 준다
    no_fx = us_review.krw_view(110.0, {"qty": 1, "avg_price": 100.0, "fx_buy": 1300.0}, {"rate": None, "as_of": None, "reason": "환율 없음"})
    assert not no_fx["available"] and no_fx["reason"] == "환율 없음"
    assert not us_review.krw_view(None, {"qty": 1, "avg_price": 100.0, "fx_buy": 1300.0}, fx_ok)["available"]


def test_fx_now_states(offline):
    today = date(2026, 10, 30)
    assert us_review.fx_now(today)["rate"] is None and "아직 받지 못" in us_review.fx_now(today)["reason"]
    marketdb.upsert_prices("KRW=X", bars(n=60, end="2026-10-29", close0=1300.0, step=1.0, volume=0.0))
    got = us_review.fx_now(today)
    assert got["rate"] == pytest.approx(1300.0 + 59 * 1.0 + 3.0 * np.sin(59)) and got["as_of"] == "2026-10-29" and got["reason"] is None
    old = us_review.fx_now(date(2026, 11, 20))                                                       # 2026-10-29 값이 22일 전
    assert old["rate"] is None and "22일 전" in old["reason"] and old["as_of"] == "2026-10-29"


def test_judgments_attach_krw_and_fx_for_held_positions(offline):
    us_positions.register("AAA", 2, 90, fx_buy=1300)
    marketdb.upsert_prices("AAA", bars())
    marketdb.upsert_prices("KRW=X", bars(n=60, end="2026-10-29", close0=1400.0, step=0.0, volume=0.0))
    j = us_review.judgments()
    s = j["stocks"][0]
    assert j["fx"]["as_of"] == "2026-10-29" and s["position"]["fx_buy"] == 1300
    assert s["krw"]["available"] and s["krw"]["fx_buy"] == 1300 and s["krw"]["pnl_krw"] == pytest.approx(2 * s["sell"]["price"] * j["fx"]["rate"] - 2 * 90 * 1300)
    assert s["sell"]["pnl_pct"] == pytest.approx(s["krw"]["price_pct"])                                # 판단 쪽 손익은 그대로 달러 기준이다
    usmarket.add_watch("BBB", "B")
    assert [x["krw"] for x in us_review.judgments()["stocks"] if x["symbol"] == "BBB"] == [None]     # 보유가 없으면 원화 손익도 없다


def test_krw_symbol_is_collected_with_the_market_indicators():
    assert usmarket.MARKET["KRW=X"] == "원/달러 환율"


def test_api_fx_roundtrip_and_validation(offline):
    from stockwatch.web import app
    c = app.test_client()
    r = c.put("/api/us/positions/AAA", json={"qty": 2, "avg_price": 130, "fx_buy": 1352.5}, headers=H)
    assert r.status_code == 200 and strict(r)["fx_buy"] == 1352.5
    assert strict(c.get("/api/us/positions"))[0]["fx_buy"] == 1352.5
    bad = c.put("/api/us/positions/AAA", json={"qty": 2, "avg_price": 130, "fx_buy": 0.00073}, headers=H)
    assert bad.status_code == 400 and "환율" in strict(bad)["error"]
    assert strict(c.get("/api/us/positions"))[0]["fx_buy"] == 1352.5                                 # 거절된 요청은 기존 값을 바꾸지 않는다


def test_judgments_attach_the_add_opinion_only_for_held_positions(offline):
    us_positions.register("AAA", 2, 90, fx_buy=1300)
    marketdb.upsert_prices("AAA", bars())
    usmarket.add_watch("BBB", "B")
    j = us_review.judgments()
    by = {x["symbol"]: x for x in j["stocks"]}
    a = by["AAA"]["add"]
    assert a["code"] in {"pyramid", "avg_down", "hold", "avoid"} and a["label"] and a["reason"] and a["note"] and a["evidence"]
    assert any(e["label"].startswith("시뮬레이션") for e in a["evidence"]) and a["evidence"][0]["label"] == "내 위치"
    assert by["BBB"]["add"] is None                                                                  # 보유가 없으면 의견도 없다
    from stockwatch.web import app
    c = app.test_client()
    body = strict(c.get("/api/us/judgment"))                                                       # NaN 이 섞이면 strict JSON 파싱에서 실패한다
    assert [x for x in body["stocks"] if x["symbol"] == "AAA"][0]["add"]["label"] == a["label"]
