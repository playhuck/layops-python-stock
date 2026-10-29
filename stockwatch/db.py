"""PostgreSQL 접속/스키마. 접속 정보는 .env 의 POSTGRES_HOST/PORT/USER/PASSWORD (+선택: POSTGRES_DB, POSTGRES_SCHEMA, POSTGRES_SSLMODE).

- 이 앱의 테이블은 전용 스키마(기본 stockwatch) 안에만 만든다: 같은 서버의 다른 서비스 DB/스키마는 건드리지 않는다.
- 커넥션 풀은 autocommit: 단일 문장은 즉시 커밋. 여러 문장을 원자적으로 묶을 때는 with db.tx() 를 쓴다.
- 비밀번호는 로그/예외 메시지에 싣지 않는다.
"""
import re
import threading
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .envfile import env

SCHEMA_RE = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")

DDL = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS watchlist(
  code TEXT PRIMARY KEY, name TEXT NOT NULL, market TEXT NOT NULL DEFAULT 'STK', grp TEXT,
  sort_order BIGSERIAL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS prices(
  code TEXT NOT NULL, date DATE NOT NULL, open DOUBLE PRECISION, high DOUBLE PRECISION, low DOUBLE PRECISION,
  close DOUBLE PRECISION, volume DOUBLE PRECISION, PRIMARY KEY(code, date));
CREATE TABLE IF NOT EXISTS flows(
  code TEXT NOT NULL, date DATE NOT NULL, institution DOUBLE PRECISION, other_corp DOUBLE PRECISION,
  individual DOUBLE PRECISION, foreign_net DOUBLE PRECISION, PRIMARY KEY(code, date));
CREATE TABLE IF NOT EXISTS vix(date DATE PRIMARY KEY, close DOUBLE PRECISION NOT NULL);
-- 보조 데이터셋(외국인보유/시총/공매도/PER·PBR/지수/시장수급/투자자 세부수급): 컬럼이 데이터셋마다 달라 JSONB 로 보관
CREATE TABLE IF NOT EXISTS datasets(
  dataset TEXT NOT NULL, code TEXT NOT NULL, date DATE NOT NULL, data JSONB NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(dataset, code, date));
CREATE TABLE IF NOT EXISTS rules(
  code TEXT PRIMARY KEY, budget_krw BIGINT NOT NULL DEFAULT 0, live INTEGER NOT NULL DEFAULT 0,
  protect INTEGER NOT NULL DEFAULT 1, stop_atr DOUBLE PRECISION, target_atr DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS proposals(
  id BIGSERIAL PRIMARY KEY, created_at TEXT NOT NULL, decided_at TEXT, code TEXT NOT NULL, name TEXT,
  side TEXT NOT NULL, status TEXT NOT NULL, qty INTEGER NOT NULL, price BIGINT NOT NULL, stop BIGINT, target BIGINT,
  evidence TEXT NOT NULL, sizing TEXT, dry_run INTEGER NOT NULL, order_id TEXT, protect_order_id TEXT, result TEXT);
CREATE INDEX IF NOT EXISTS ix_prop_status ON proposals(status, code);
-- 승인 시도 기록: 주문이 나갔을 수 있는 상황을 중단 후에도 정확히 복구하기 위한 것 (모드/확정 수량·가격/시각)
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS attempt_mode TEXT;
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS attempt_qty INTEGER;
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS attempt_price BIGINT;
ALTER TABLE proposals ADD COLUMN IF NOT EXISTS attempt_at TEXT;
CREATE TABLE IF NOT EXISTS ledger(
  id BIGSERIAL PRIMARY KEY, ts TEXT NOT NULL, code TEXT NOT NULL, side TEXT NOT NULL,
  qty INTEGER NOT NULL, price DOUBLE PRECISION NOT NULL, fee DOUBLE PRECISION NOT NULL DEFAULT 0, proposal_id BIGINT);
CREATE TABLE IF NOT EXISTS audit(id BIGSERIAL PRIMARY KEY, ts TEXT NOT NULL, event TEXT NOT NULL, detail TEXT);
-- 토스증권 업종 분류(대 39 / 중 158 / 소 103). 종목은 여러 업종에 속할 수 있다
CREATE TABLE IF NOT EXISTS sectors(sector_id TEXT PRIMARY KEY, name TEXT NOT NULL, depth INTEGER NOT NULL, parent_id TEXT);
CREATE TABLE IF NOT EXISTS sector_members(sector_id TEXT NOT NULL, symbol TEXT NOT NULL, PRIMARY KEY(sector_id, symbol));
CREATE INDEX IF NOT EXISTS ix_sector_members_symbol ON sector_members(symbol);
-- 업종(대/중분류)별 비중 한도 개별 설정. 없으면 대분류는 기본 한도(max_group_pct)를 따르고 중분류는 한도 없음
CREATE TABLE IF NOT EXISTS sector_limits(sector_id TEXT PRIMARY KEY, max_pct DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS push_tokens(token TEXT PRIMARY KEY, created_at TEXT NOT NULL);
"""


class Result:
    """sqlite3 커서와 비슷하게 쓰는 얇은 결과 객체 (fetchone/fetchall/rowcount/반복)."""
    def __init__(self, rows, rowcount):
        self._rows, self.rowcount = rows, rowcount

    def fetchone(self): return self._rows[0] if self._rows else None
    def fetchall(self): return list(self._rows)
    def __iter__(self): return iter(self._rows)


class Database:
    def __init__(self, schema: str | None = None):
        self.schema = schema or env("POSTGRES_SCHEMA", "stockwatch")
        if not SCHEMA_RE.match(self.schema):
            raise ValueError("POSTGRES_SCHEMA 는 소문자/숫자/밑줄만 허용합니다")
        self.pool = ConnectionPool(
            conninfo=self._conninfo(), min_size=1, max_size=8, open=False, timeout=15,
            kwargs={"autocommit": True, "row_factory": dict_row, "options": f"-c search_path={self.schema}"})
        self.pool.open(wait=True, timeout=15)
        with self.pool.connection() as c:  # 스키마/테이블 생성은 멱등
            c.execute(f"CREATE SCHEMA IF NOT EXISTS {self.schema}")
            c.execute(DDL)

    @staticmethod
    def _conninfo() -> str:
        return psycopg.conninfo.make_conninfo(
            host=env("POSTGRES_HOST"), port=env("POSTGRES_PORT", "5432"), user=env("POSTGRES_USER"),
            password=env("POSTGRES_PASSWORD"), dbname=env("POSTGRES_DB", "postgres"),
            sslmode=env("POSTGRES_SSLMODE", "prefer"), connect_timeout="8")

    def execute(self, sql: str, params=()) -> Result:
        """`?` 와 `%s` 플레이스홀더 모두 허용 (기존 SQLite 스타일 호출과 호환)."""
        with self.pool.connection() as c:
            cur = c.execute(sql.replace("?", "%s") if params else sql, params or None)  # 파라미터가 없으면 % 를 해석하지 않는다
            return Result(cur.fetchall() if cur.description else [], cur.rowcount)

    @contextmanager
    def tx(self):
        """여러 문장을 한 트랜잭션으로 (실패 시 전부 롤백)."""
        with self.pool.connection() as c, c.transaction():
            yield c

    def close(self):
        self.pool.close()


_default: Database | None = None
_lock = threading.Lock()


def get() -> Database:
    global _default
    with _lock:
        if _default is None:
            _default = Database()
        return _default


def use(database: Database | None):
    """기본 DB 교체 (테스트에서 임시 스키마로 바꿀 때)."""
    global _default
    _default = database
