import pytest
from pgtest import clean

from stockwatch import config, db, usersettings
from stockwatch.web import app

c = app.test_client()
H = {"X-Stockwatch": "1"}


@pytest.fixture(autouse=True)
def isolated():
    d = clean(); d.execute("INSERT INTO sectors VALUES('169','반도체',0,NULL),('171','반도체부품소재',1,'169')")
    db.use(d)
    yield d
    db.use(None)


def eff(d): return {k: config.load()["trading"].get(k) for k in usersettings.FIELDS}


def test_overrides_win_over_yaml_defaults_and_defaults_stay_visible():
    base = config.load()["trading"]["capital_krw"]
    usersettings.save(eff(db.get()), {"capital_krw": 10_000_000, "risk_per_trade_pct": 0.5}, False, None)
    cfg = config.load()
    assert cfg["trading"]["capital_krw"] == 10_000_000 and cfg["trading"]["risk_per_trade_pct"] == 0.5
    assert cfg["trading_defaults"]["capital_krw"] == base           # 기본값은 그대로 보존


@pytest.mark.parametrize("bad", [{"capital_krw": -1}, {"risk_per_trade_pct": 50}, {"max_positions": 2.5}, {"capital_krw": "abc"}, {"foo": 1},
                                 {"max_group_pct": float("nan")}, {"monthly_kill_pct": 0}])
def test_invalid_values_rejected(bad):
    with pytest.raises(ValueError):
        usersettings.save(eff(db.get()), bad, False, None)


def test_live_mode_blocks_loosening_without_confirm_but_allows_tightening():
    usersettings.save(eff(db.get()), {"capital_krw": 10_000_000, "max_group_pct": 30}, False, None)
    with pytest.raises(PermissionError):
        usersettings.save(eff(db.get()), {"capital_krw": 20_000_000}, True, None)            # 자본 증가 = 느슨
    with pytest.raises(PermissionError):
        usersettings.save(eff(db.get()), {"max_order_krw": 0, "max_group_pct": 50}, True, None)
    usersettings.save(eff(db.get()), {"capital_krw": 5_000_000, "max_group_pct": 20}, True, None)   # 조이는 변경은 허용
    usersettings.save(eff(db.get()), {"capital_krw": 20_000_000}, True, usersettings.CONFIRM)       # 확인 문구가 있으면 허용
    assert config.load()["trading"]["capital_krw"] == 20_000_000


def test_sector_limit_set_clear_and_live_guard():
    d = db.get()
    assert usersettings.set_sector_limit("169", 15, 40, False, None)["after"] == 15
    assert config.load()["sector_limits"] == {"169": 15.0}
    with pytest.raises(PermissionError):
        usersettings.set_sector_limit("169", 30, 40, True, None)                              # 15 → 30 상향
    usersettings.set_sector_limit("169", 10, 40, True, None)                                 # 하향은 허용
    with pytest.raises(PermissionError):
        usersettings.set_sector_limit("169", None, 40, True, None)                           # 해제하면 기본 40%로 느슨해짐
    assert usersettings.set_sector_limit("171", 12, 40, False, None)["before"] is None       # 중분류는 기본 한도가 없다(무제한)
    with pytest.raises(ValueError):
        usersettings.set_sector_limit("999", 10, 40, False, None)
    with pytest.raises(ValueError):
        usersettings.set_sector_limit("169", 0, 40, False, None)


def test_api_guarded_and_roundtrip():
    assert c.put("/api/settings", json={"values": {"capital_krw": 1}}).status_code == 403
    assert c.put("/api/settings/sector-limit", json={"sector_id": "169", "max_pct": 10}).status_code == 403
    assert c.post("/api/settings/reset").status_code == 403
    r = c.put("/api/settings", json={"values": {"capital_krw": 7_000_000}}, headers=H)
    assert r.status_code == 200 and r.json["changes"]["capital_krw"][1] == 7_000_000
    assert c.put("/api/settings", json={"values": {"capital_krw": -5}}, headers=H).status_code == 400
    g = c.get("/api/settings").json
    assert g["values"]["capital_krw"] == 7_000_000 and "capital_krw" in g["overridden"] and g["fields"]["capital_krw"]["min"] == 0
    assert c.post("/api/settings/reset", json={}, headers=H).status_code == 200
    assert "capital_krw" not in c.get("/api/settings").json["overridden"]
