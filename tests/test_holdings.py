import json
from datetime import date, timedelta

import pytest

from stockwatch import db, holdings, marketdb, us_positions, usmarket
from stockwatch.trading import service
from stockwatch.trading.engine import Gateway
from stockwatch.trading.toss import TossError

H = {"X-Stockwatch": "1"}
TOSS_ITEMS = [{"symbol": "005930", "quantity": "10", "averagePurchasePrice": "70000", "lastPrice": "75000"},
              {"symbol": "000660", "quantity": "0", "averagePurchasePrice": "100000", "lastPrice": "110000"},
              {"symbol": "AAPL", "quantity": "3", "averagePurchasePrice": "150", "lastPrice": "170"}]


class Toss:
    """호출 여부를 세는 가짜 토스 클라이언트."""
    def __init__(self, items=None, fail=False):
        self.items, self.fail, self.calls = TOSS_ITEMS if items is None else items, fail, 0

    def holdings(self, symbol=None):
        self.calls += 1
        if self.fail:
            raise TossError(403, "access_denied", "IP address not allowed")
        return {"items": self.items}


@pytest.fixture
def api(monkeypatch):
    from pgtest import clean
    monkeypatch.setenv("TOSS_CLIENT_ID", "x"); monkeypatch.setenv("TOSS_CLIENT_SECRET", "y"); monkeypatch.setenv("TOSS_EXPIRED", "2027.10.29")
    db.use(clean())
    from stockwatch.trading.store import Store
    toss = Toss()
    service._store, service._gateway = Store(db.get()), Gateway(lambda: toss)
    for code, name in (("005930", "삼성전자"), ("000660", "SK하이닉스")):
        db.get().execute("INSERT INTO watchlist(code,name,market,grp) VALUES(%s,%s,'STK','')", (code, name))
    from stockwatch.web import app
    yield app.test_client(), toss
    db.use(None)
    service._store = service._gateway = None


def get(c):
    return json.loads(c.get("/api/holdings").data)


def test_default_source_is_manual_and_switching_is_guarded_and_validated(api):
    c, _ = api
    assert holdings.get_source() == "manual" and get(c)["source"] == "manual"
    assert c.put("/api/holdings/source", json={"source": "import"}).status_code == 403            # 상태 변경은 X-Stockwatch 헤더 필수
    assert c.put("/api/holdings/source", json={"source": "bogus"}, headers=H).status_code == 400
    assert c.put("/api/holdings/source", json={}, headers=H).status_code == 400
    assert c.put("/api/holdings/source", json={"source": "import"}, headers=H).get_json() == {"source": "import"}
    assert get(c)["source"] == "import"
    assert c.put("/api/holdings/source", json={"source": "manual"}, headers=H).status_code == 200 and get(c)["source"] == "manual"


@pytest.mark.parametrize("body", [
    {"qty": 0, "avg_price": 1000}, {"qty": -1, "avg_price": 1000}, {"qty": 1.5, "avg_price": 1000}, {"qty": "x", "avg_price": 1000},
    {"qty": 1, "avg_price": 0}, {"qty": 1, "avg_price": "x"}, {"qty": 1, "avg_price": 1000, "opened_on": "2026-13-01"},
    {"qty": 1, "avg_price": 1000, "opened_on": "2099-01-01"}, {},
])
def test_kr_manual_rejects_bad_input_and_saves_nothing(api, body):
    c, _ = api
    r = c.put("/api/kr/positions/005930", json=body, headers=H)
    assert r.status_code == 400 and r.get_json()["error"] and holdings.kr_list() == []


def test_kr_manual_requires_a_watchlist_stock_and_a_valid_code(api):
    c, _ = api
    assert c.put("/api/kr/positions/373220", json={"qty": 1, "avg_price": 1000}, headers=H).status_code == 400     # 관심 종목에 없음
    assert c.put("/api/kr/positions/ABC", json={"qty": 1, "avg_price": 1000}, headers=H).status_code == 400
    assert c.put("/api/kr/positions/005930", json={"qty": 1, "avg_price": 1000}).status_code == 403
    assert holdings.kr_list() == []


def test_kr_manual_crud(api):
    c, _ = api
    r = c.put("/api/kr/positions/005930", json={"qty": 10, "avg_price": 70000, "opened_on": "2026-10-01", "note": " 1차 "}, headers=H)
    assert r.status_code == 200 and r.get_json()["qty"] == 10 and r.get_json()["opened_on"] == "2026-10-01" and r.get_json()["note"] == "1차"
    c.put("/api/kr/positions/005930", json={"qty": 12, "avg_price": 71000}, headers=H)                     # 같은 종목은 덮어쓴다
    rows = holdings.kr_list()
    assert len(rows) == 1 and rows[0]["qty"] == 12 and rows[0]["opened_on"] is None
    assert c.delete("/api/kr/positions/005930").status_code == 403
    assert c.delete("/api/kr/positions/005930", headers=H).status_code == 200
    assert c.delete("/api/kr/positions/005930", headers=H).status_code == 404 and holdings.kr_list() == []


def test_db_rejects_nonpositive_kr_values(api):
    holdings.kr_register("005930", 1, 1000)
    with pytest.raises(Exception):
        db.get().execute("UPDATE kr_positions SET qty=0")


def test_manual_mode_shows_only_manual_data_and_never_calls_toss(api):
    c, toss = api
    holdings.kr_register("005930", 7, 68000)
    db.get().execute("INSERT INTO us_watchlist(symbol,name) VALUES('AAA','A')")
    db.get().execute("INSERT INTO us_positions(symbol,qty,avg_price) VALUES('AAA',2.5,11.2)")
    v = get(c)
    assert v["source"] == "manual" and toss.calls == 0                                                  # 토스에는 보유가 있어도 건드리지 않는다
    assert list(v["kr"]["items"]) == ["005930"] and v["kr"]["items"]["005930"]["qty"] == 7 and v["kr"]["known"] is True
    assert v["us"] == {"supported": True, "items": {"AAA": {"qty": 2.5, "avg": 11.2, "opened_on": None, "note": ""}}}
    assert "last" not in v["kr"]["items"]["005930"]


def test_import_mode_shows_only_toss_data_and_no_manual_overlap(api):
    c, toss = api
    holdings.kr_register("000660", 99, 1)                                                                # 직접 입력 값(가져오기 모드에서는 안 보여야 한다)
    db.get().execute("INSERT INTO us_positions(symbol,qty,avg_price) VALUES('AAA',2,3)")
    c.put("/api/holdings/source", json={"source": "import"}, headers=H)
    v = get(c)
    assert v["source"] == "import" and toss.calls == 1
    assert v["kr"] == {"configured": True, "known": True, "items": {"005930": {"qty": 10, "avg": 70000.0, "last": 75000.0}}}   # 수량 0 과 미국 심볼은 걸러진다
    assert v["us"] == {"supported": False, "items": {}}                                                  # 미국 가져오기는 지원하지 않는다


def test_import_failure_is_unknown_not_empty(api):
    c, toss = api
    toss.fail = True
    holdings.kr_register("005930", 7, 68000)
    c.put("/api/holdings/source", json={"source": "import"}, headers=H)
    v = get(c)
    assert v["kr"] == {"configured": True, "known": False, "items": {}}                                  # 직접 입력 값으로 대신 채우지도 않는다


def test_import_without_toss_configuration_is_reported(api, monkeypatch):
    c, toss = api
    monkeypatch.delenv("TOSS_CLIENT_ID", raising=False)
    monkeypatch.setattr("stockwatch.trading.engine.env", lambda k, *a: (_ for _ in ()).throw(KeyError(k)))
    c.put("/api/holdings/source", json={"source": "import"}, headers=H)
    v = get(c)
    assert v["kr"] == {"configured": False, "known": False, "items": {}} and toss.calls == 0
