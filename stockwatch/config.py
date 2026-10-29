"""설정 로딩. 관심 종목은 PostgreSQL watchlist 테이블이 원본이다 (config.yaml 의 watchlist 는 최초 1회 시드용)."""
import re

import yaml

from . import db, usersettings
from .envfile import ROOT

MAX_WATCHLIST = 30  # 갱신당 KRX 호출 수(종목당 약 12회)를 제한
CODE_RE = re.compile(r"^[0-9A-Z]{6}$")
SEED_FLAG = "watchlist_seeded"


def _items(database: db.Database) -> list[dict]:
    rows = database.execute("SELECT code,name,market,grp FROM watchlist ORDER BY sort_order").fetchall()
    return [{"code": r["code"], "name": r["name"], "market": r["market"], **({"group": r["grp"]} if r["grp"] else {})} for r in rows]


def _seed(database: db.Database, cfg: dict):
    """DB 에 목록이 한 번도 만들어진 적 없으면 config.yaml 의 watchlist 로 시작한다. (전부 삭제한 뒤에는 다시 시드하지 않는다)"""
    if database.execute("SELECT 1 AS x FROM settings WHERE key=%s", (SEED_FLAG,)).fetchone():
        return
    kosdaq = set(cfg.get("kosdaq", []))
    with database.tx() as c:
        for i in cfg.get("watchlist", []):
            c.execute("INSERT INTO watchlist(code,name,market,grp) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                      (i["code"], i["name"], i.get("market") or ("KSQ" if i["code"] in kosdaq else "STK"), i.get("group")))
        c.execute("INSERT INTO settings(key,value) VALUES(%s,'1') ON CONFLICT DO NOTHING", (SEED_FLAG,))


def load() -> dict:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    database = db.get()
    _seed(database, cfg)
    cfg["watchlist"] = _items(database)
    cfg["trading_defaults"] = dict(cfg.get("trading", {}))                        # config.yaml 기본값 (화면의 '기본값 복원'/비교용)
    cfg["trading"] = {**cfg.get("trading", {}), **usersettings.get_overrides(database)}   # 화면에서 바꾼 값이 우선
    cfg["sector_limits"] = usersettings.sector_limits(database)
    return cfg


def market_of(cfg: dict, code: str) -> str:
    return next((i.get("market", "STK") for i in cfg["watchlist"] if i["code"] == code), "STK")


def add_stock(cfg: dict, code: str, name: str, market: str, group: str | None = None) -> list[dict]:
    if not CODE_RE.match(code):
        raise ValueError("종목코드는 6자리 영숫자여야 합니다")
    if market not in ("STK", "KSQ"):
        raise ValueError("지원하는 시장은 코스피/코스닥입니다")
    database = db.get()
    items = _items(database)
    if any(i["code"] == code for i in items):
        raise ValueError("이미 관심 종목에 있습니다")
    if len(items) >= MAX_WATCHLIST:
        raise ValueError(f"관심 종목은 최대 {MAX_WATCHLIST}개입니다")
    database.execute("INSERT INTO watchlist(code,name,market,grp) VALUES(%s,%s,%s,%s)",
                     (code, name.strip()[:40], market, (group or "").strip()[:20] or None))
    return _items(database)


def remove_stock(cfg: dict, code: str) -> list[dict]:
    database = db.get()
    if database.execute("DELETE FROM watchlist WHERE code=%s", (code,)).rowcount == 0:
        raise KeyError(code)
    return _items(database)
