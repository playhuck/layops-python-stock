import pytest
from pgtest import clean

from stockwatch import config, db


@pytest.fixture(autouse=True)
def isolated_db():
    db.use(clean())          # 임시 스키마를 기본 DB 로
    yield
    db.use(None)


def codes():
    return [i["code"] for i in config.load()["watchlist"]]


def test_seeded_once_from_yaml_then_db_is_source_of_truth():
    assert codes() == ["005930", "000660"]
    cfg = config.load()
    config.remove_stock(cfg, "005930"); config.remove_stock(cfg, "000660")
    assert codes() == []                       # 전부 삭제해도 yaml 로 되살아나지 않는다


def test_add_and_remove_persist_with_market_and_group():
    cfg = config.load()
    config.add_stock(cfg, "222800", "심텍", "KSQ", "반도체")
    item = next(i for i in config.load()["watchlist"] if i["code"] == "222800")
    assert item == {"code": "222800", "name": "심텍", "market": "KSQ", "group": "반도체"}
    config.remove_stock(config.load(), "222800")
    assert "222800" not in codes()


@pytest.mark.parametrize("code,market,err", [("12345", "STK", "6자리"), ("005930", "STK", "이미"), ("111111", "NASDAQ", "시장")])
def test_add_validation(code, market, err):
    with pytest.raises(ValueError, match=err):
        config.add_stock(config.load(), code, "x", market)


def test_max_watchlist():
    cfg = config.load()
    for i in range(config.MAX_WATCHLIST - 2):
        config.add_stock(cfg, f"{i + 100:06d}", "x", "STK")
    with pytest.raises(ValueError, match="최대"):
        config.add_stock(cfg, "999999", "x", "STK")


def test_remove_unknown_raises():
    with pytest.raises(KeyError):
        config.remove_stock(config.load(), "123456")


def test_market_of():
    cfg = config.load()
    config.add_stock(cfg, "222800", "심텍", "KSQ")
    cfg = config.load()
    assert config.market_of(cfg, "222800") == "KSQ" and config.market_of(cfg, "005930") == "STK"
