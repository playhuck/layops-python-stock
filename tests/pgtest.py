"""테스트용 PostgreSQL 헬퍼: 실행마다 임시 스키마(test_<hex>)를 만들고 종료 시 삭제한다. 실제 stockwatch 스키마는 절대 건드리지 않는다."""
import atexit
import uuid

from stockwatch import db
from stockwatch.trading.store import Store

_db = None
TABLES = ["sectors", "sector_members", "settings", "watchlist", "prices", "flows", "vix", "datasets", "rules", "proposals", "ledger", "audit", "push_tokens", "us_watchlist", "us_meta"]


def test_db() -> db.Database:
    global _db
    if _db is None:
        _db = db.Database(schema=f"test_{uuid.uuid4().hex[:10]}")
        atexit.register(_drop)
    return _db


def _drop():
    d = db.Database.__new__(db.Database)  # 풀을 닫은 뒤 별도 연결로 스키마 삭제
    schema = _db.schema
    assert schema.startswith("test_")     # 안전장치: 테스트 스키마만 삭제
    _db.close()
    import psycopg
    with psycopg.connect(db.Database._conninfo(), autocommit=True) as c:
        c.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def clean() -> db.Database:
    d = test_db()
    d.execute("TRUNCATE " + ",".join(TABLES) + " RESTART IDENTITY")
    return d


def new_store() -> Store:
    return Store(clean())
