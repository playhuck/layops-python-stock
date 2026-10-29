"""업종 분류 (토스증권 섹터 계층). 종목을 업종에 자동 매핑하고, '같은 업종 = 같이 흔들리는 종목'을 판단한다.

연결 규칙: 두 종목이 **같은 대분류(depth 0)** 에 함께 속하면 같은 위험 묶음이다. 하위 업종의 종목은 상위 대분류에도 포함되므로 대분류 기준이면 중·소분류 공유도 모두 잡힌다.
(원전 사고 같은 업종 이벤트는 개별 종목이 아니라 묶음 전체를 때린다 → 비중은 종목이 아니라 묶음 단위로 본다)
수동 지정(watchlist.grp)이 같아도 연결한다.
"""
import time
from datetime import datetime, timedelta

from . import db as dbm

STALE_DAYS = 14
SYNCED_KEY = "sectors_synced_at"


def is_stale(database: dbm.Database | None = None) -> bool:
    d = database or dbm.get()
    r = d.execute("SELECT value FROM settings WHERE key=?", (SYNCED_KEY,)).fetchone()
    if not r or d.execute("SELECT 1 AS x FROM sector_members LIMIT 1").fetchone() is None:
        return True
    return datetime.now() - datetime.fromisoformat(r["value"]) > timedelta(days=STALE_DAYS)


def sync(client, database: dbm.Database | None = None) -> dict:
    """토스에서 전체 분류와 구성 종목을 받아 교체 저장. 도중에 실패하면 기존 데이터를 그대로 둔다(트랜잭션)."""
    d = database or dbm.get()
    secs = client.sectors()
    members = {s["sectorId"]: client.sector_stocks(s["sectorId"]) for s in secs}   # 약 300회 호출, 1~2분
    with d.tx() as c:
        c.execute("DELETE FROM sector_members"); c.execute("DELETE FROM sectors")
        with c.cursor() as cur:
            cur.executemany("INSERT INTO sectors(sector_id,name,depth,parent_id) VALUES(%s,%s,%s,%s)",
                            [(s["sectorId"], s["name"], s["depth"], s.get("parentSectorId")) for s in secs])
            cur.executemany("INSERT INTO sector_members(sector_id,symbol) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                            [(sid, sym) for sid, syms in members.items() for sym in syms])
        c.execute("INSERT INTO settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value",
                  (SYNCED_KEY, datetime.now().isoformat(timespec="seconds")))
    return {"sectors": len(secs), "pairs": sum(len(v) for v in members.values())}


def ensure_fresh(client, database: dbm.Database | None = None) -> dict | None:
    return sync(client, database) if is_stale(database) else None


def classify(codes: list[str], database: dbm.Database | None = None) -> dict[str, dict]:
    """{code: {"label": 화면용 업종명, "tags": [대분류명...], "top_ids": {대분류 id}}}. 분류 데이터가 없는 종목은 키 자체가 없다."""
    if not codes:
        return {}
    d = database or dbm.get()
    rows = d.execute(
        "SELECT m.symbol, s.sector_id, s.name, s.depth, (SELECT COUNT(*) FROM sector_members x WHERE x.sector_id=s.sector_id) AS size "
        "FROM sector_members m JOIN sectors s ON s.sector_id=m.sector_id WHERE m.symbol = ANY(%s)", (list(codes),)).fetchall()
    out: dict[str, dict] = {}
    for sym in {r["symbol"] for r in rows}:
        mine = [r for r in rows if r["symbol"] == sym]
        top = sorted([r for r in mine if r["depth"] == 0], key=lambda r: r["size"])
        mid = sorted([r for r in mine if r["depth"] == 1], key=lambda r: r["size"])
        pick = (mid or top)[:2]                       # 가장 구체적인(구성 종목 수가 적은) 중분류 2개를 표시용 이름으로
        primary = max(top, key=lambda r: r["size"])["name"] if top else ""     # 대표 업종 = 소속 대분류 중 가장 큰 것 (삼성전자: 가전제품·스마트폰·반도체 → 반도체)
        out[sym] = {"label": " · ".join(r["name"] for r in pick), "primary": primary, "tags": [r["name"] for r in top],
                    "top_ids": {r["sector_id"] for r in top},
                    # 비중 한도를 거는 단위: 대분류(0)와 중분류(1). 소분류는 너무 잘게 쪼개져 한도 대상에서 제외
                    "sec": {r["sector_id"]: {"name": r["name"], "depth": r["depth"], "size": r["size"]} for r in mine if r["depth"] <= 1}}
    return out


def linked(a: str, b: str, cls: dict[str, dict], manual: dict[str, str | None] | None = None) -> bool:
    """같은 위험 묶음인가: 같은 대분류를 공유하거나 수동 업종명이 같으면 True."""
    if a == b:
        return True
    manual = manual or {}
    if manual.get(a) and manual.get(a) == manual.get(b):
        return True
    return bool(cls.get(a, {}).get("top_ids", set()) & cls.get(b, {}).get("top_ids", set()))


def shared_tags(a: str, b_list: list[str], cls: dict[str, dict]) -> list[str]:
    ids = cls.get(a, {}).get("top_ids", set())
    names = {t for b in b_list for t in cls.get(b, {}).get("tags", []) if cls.get(b, {}).get("top_ids", set()) & ids}
    return sorted(names)


def tree(database: dbm.Database | None = None) -> list[dict]:
    """대분류 → 중분류 트리 (구성 종목 수 포함). 소분류는 제외."""
    d = database or dbm.get()
    rows = d.execute("SELECT s.sector_id, s.name, s.depth, s.parent_id, (SELECT COUNT(*) FROM sector_members m WHERE m.sector_id=s.sector_id) AS size "
                     "FROM sectors s WHERE s.depth <= 1 ORDER BY s.depth, s.sector_id").fetchall()
    tops = {r["sector_id"]: {"id": r["sector_id"], "name": r["name"], "depth": 0, "size": r["size"], "children": []} for r in rows if r["depth"] == 0}
    for r in rows:
        if r["depth"] == 1 and r["parent_id"] in tops:
            tops[r["parent_id"]]["children"].append({"id": r["sector_id"], "name": r["name"], "depth": 1, "size": r["size"], "parent": r["parent_id"]})
    return list(tops.values())


def overview(watchlist: list[dict], holdings_value: dict[str, float] | None, capital: float, limits: dict[str, float],
             default_pct: float, show_all: bool = False, database: dbm.Database | None = None) -> list[dict]:
    """업종 탭용: 내 관심 종목이 속한 대분류 > 중분류 트리 + 보유 비중/한도. holdings_value 가 None 이면 비중은 모름(None)."""
    d = database or dbm.get()
    names = {i["code"]: i["name"] for i in watchlist}
    held = holdings_value or {}
    cls = classify(list({*names, *held}), d)
    members: dict[str, list[str]] = {}
    for code, c in cls.items():
        for sid in c.get("sec", {}):
            members.setdefault(sid, []).append(code)

    def node(sec: dict, depth: int) -> dict:
        mine = [c for c in members.get(sec["id"], []) if c in names]
        exposure = None if holdings_value is None or capital <= 0 else round(sum(held.get(c, 0) for c in members.get(sec["id"], [])) / capital * 100, 1)
        lim = limits.get(sec["id"])
        return {"id": sec["id"], "name": sec["name"], "depth": depth, "size": sec["size"], "exposure_pct": exposure,
                "limit_pct": lim, "effective_limit_pct": lim if lim is not None else (default_pct if depth == 0 else None),
                "stocks": [{"code": c, "name": names[c], "primary": cls[c].get("primary") == sec["name"]} for c in sorted(mine, key=lambda c: names[c])]}

    out = []
    for top in tree(d):
        n = node(top, 0)
        n["children"] = [x for x in (node(ch, 1) for ch in top["children"]) if x["stocks"] or show_all or x["limit_pct"] is not None]
        if n["stocks"] or show_all or n["limit_pct"] is not None:
            out.append(n)
    return sorted(out, key=lambda n: (-len(n["stocks"]), -n["size"]))
