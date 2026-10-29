import pytest
from pgtest import clean, new_store
from test_trading import CODE, FakeClient, FakeProvider, CFG, env, make, first_buy  # noqa: F401  (픽스처/헬퍼 재사용)

from stockwatch import sectors


def load_sectors(d):
    d.execute("INSERT INTO sectors VALUES('169','반도체',0,NULL),('171','반도체부품소재',1,'169'),('707','로봇',1,'8'),('8','기계',0,NULL)")
    d.execute("INSERT INTO sector_members VALUES('169','A'),('169','B'),('171','B'),('171','A'),('8','C'),('707','C'),('169','000660')")


def test_classify_links_by_shared_top_sector_not_by_exchange():
    d = clean(); load_sectors(d)
    cls = sectors.classify(["A", "B", "C", "ZZZ"], d)
    assert cls["A"]["tags"] == ["반도체"] and cls["A"]["label"] == "반도체부품소재" and cls["C"]["label"] == "로봇"
    assert cls["A"]["primary"] == "반도체" and cls["C"]["primary"] == "기계"
    assert "ZZZ" not in cls                                                    # 분류 없는 종목은 키 자체가 없다
    assert sectors.linked("A", "B", cls) and not sectors.linked("A", "C", cls)  # 반도체끼리는 연결, 로봇은 별개
    assert sectors.linked("X", "Y", {}, {"X": "원전", "Y": "원전"})              # 수동 업종이 같아도 연결


def test_sync_replaces_atomically_and_marks_fresh():
    d = clean()

    class C:
        def sectors(self): return [{"sectorId": "1", "name": "반도체", "depth": 0, "parentSectorId": None}]
        def sector_stocks(self, sid): return ["005930", "000660"]

    assert sectors.is_stale(d)
    assert sectors.sync(C(), d) == {"sectors": 1, "pairs": 2} and not sectors.is_stale(d)

    class Boom(C):
        def sector_stocks(self, sid): raise RuntimeError("429")

    with pytest.raises(RuntimeError):
        sectors.sync(Boom(), d)
    assert d.execute("SELECT COUNT(*) AS c FROM sector_members").fetchone()["c"] == 2   # 실패해도 기존 분류 유지


def _engine_with_holding(env, held_value):
    e, store, fake = make(env)
    load_sectors(store.db)
    store.db.execute("DELETE FROM sector_members WHERE symbol='000660'"); store.db.execute(f"INSERT INTO sector_members VALUES('169','{CODE}'),('169','000660')")
    fake.items = [{"symbol": "000660", "quantity": "1", "averagePurchasePrice": "1", "lastPrice": str(held_value)}]
    return e, store, fake


def test_group_check_counts_the_new_purchase_not_only_holdings(env):
    # 같은 업종 보유 0%여도 신규 매수만으로 한도를 넘으면 막는다 (이전 로직은 신규 금액을 빼먹었다)
    e, store, fake = _engine_with_holding(env, held_value=1)
    e.t = {**e.t, "max_group_pct": 5}                                  # 한도 5% < 신규 약 11.6%
    blk = e.generate()[CODE]["buy"]
    chk = next(c for c in blk["evidence"] if c["label"].startswith("업종 쏠림"))
    assert not chk["passed"] and not blk["passed"]


def test_group_check_blocks_when_linked_holding_pushes_over_limit_but_not_unlinked(env):
    e, store, fake = _engine_with_holding(env, held_value=3_000_000)   # 같은 업종 보유 30% + 신규 ≈11.6% = 41.6% > 40%
    c = next(x for x in e.generate()[CODE]["buy"]["evidence"] if x["label"].startswith("업종 쏠림"))
    assert not c["passed"] and "반도체" in c["label"]
    store.db.execute("DELETE FROM sector_members WHERE symbol='000660'"); store.db.execute("INSERT INTO sector_members VALUES('8','000660')")
    e.gw.invalidate()
    c2 = next(x for x in e.generate()[CODE]["buy"]["evidence"] if x["label"].startswith("업종 쏠림"))
    assert c2["passed"]                                                # 다른 업종(기계) 보유는 쏠림에 포함되지 않는다


def test_unknown_sector_is_not_waved_through(env):
    e, store, fake = make(env)
    e.cfg = {**e.cfg, "watchlist": [{"code": CODE, "name": "삼성전자"}]}   # 수동 업종 없음 + 분류 데이터 없음
    c = next(x for x in e.generate()[CODE]["buy"]["evidence"] if "업종" in x["label"])
    assert not c["passed"] and "동기화" in c["value"]


def test_per_sector_limit_override_decides_pass_or_fail(env):
    # 업종별 개별 한도: 반도체 대분류에 한도 5%를 걸면 신규(≈한도금액 30%)로 실패, 35% 로 올리면 통과
    e, store, fake = _engine_with_holding(env, held_value=1)
    e.cfg = {**e.cfg, "sector_limits": {"169": 5.0}}
    c1 = next(x for x in e.generate()[CODE]["buy"]["evidence"] if x["label"].startswith("업종 쏠림"))
    assert not c1["passed"] and "<= 5%" == c1["threshold"]
    e.cfg = {**e.cfg, "sector_limits": {"169": 35.0}}
    assert next(x for x in e.generate()[CODE]["buy"]["evidence"] if x["label"].startswith("업종 쏠림"))["passed"]


def test_mid_level_sector_limit_is_enforced_and_reported_as_tightest(env):
    e, store, fake = _engine_with_holding(env, held_value=1)
    store.db.execute("INSERT INTO sector_members VALUES('171','%s')" % CODE)           # 중분류(반도체부품소재)에도 소속
    e.cfg = {**e.cfg, "sector_limits": {"171": 4.0}}                                   # 대분류는 기본 40%, 중분류만 4%
    c = next(x for x in e.generate()[CODE]["buy"]["evidence"] if x["label"].startswith("업종 쏠림"))
    assert not c["passed"] and "반도체부품소재" in c["label"]


def test_overview_tree_shows_our_stocks_exposure_and_limits():
    d = clean(); load_sectors(d); d.execute("INSERT INTO sector_limits VALUES('169',20)")
    wl = [{"code": "A", "name": "에이"}, {"code": "C", "name": "씨"}]
    out = sectors.overview(wl, {"A": 2_000_000, "B": 1_000_000}, 10_000_000, {"169": 20.0}, 40.0, database=d)
    semi = next(n for n in out if n["name"] == "반도체")
    assert semi["exposure_pct"] == 30.0 and semi["limit_pct"] == 20.0 and [s["code"] for s in semi["stocks"]] == ["A"]   # B 는 관심종목 아님이지만 보유액은 합산
    assert semi["children"][0]["name"] == "반도체부품소재" and semi["children"][0]["effective_limit_pct"] is None
    assert [n["name"] for n in out] == ["반도체", "기계"] and sectors.overview(wl, None, 0, {}, 40.0, database=d)[0]["exposure_pct"] is None
