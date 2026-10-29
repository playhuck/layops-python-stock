"""시계열 읽기/쓰기 (PostgreSQL). 모든 쓰기는 upsert 라서 갱신할 때마다 이력이 누적된다 (CSV 시절엔 매번 덮어써서 40일/2년치만 남았다)."""
import json

import numpy as np
import pandas as pd

from . import db

PRICE_COLS = ["open", "high", "low", "close", "volume"]
FLOW_COLS = {"institution": "institution", "other_corp": "other_corp", "individual": "individual", "foreign": "foreign_net"}  # foreign 은 SQL 예약어


def _dates(df: pd.DataFrame) -> pd.Series:
    d = df["date"] if "date" in df.columns else df.index.to_series()
    return pd.to_datetime(d).dt.date.reset_index(drop=True)


def _nz(v):
    """NaN/NaT/numpy 스칼라 → DB 에 넣을 수 있는 파이썬 값 (NaN 은 NULL)."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    return v.item() if hasattr(v, "item") else v


def _batch(sql: str, rows: list[tuple], database: db.Database | None = None):
    if not rows:
        return
    with (database or db.get()).tx() as c, c.cursor() as cur:
        cur.executemany(sql, rows)


# -- 시세 --------------------------------------------------------------------------------------
def upsert_prices(code: str, df: pd.DataFrame):
    d, v = _dates(df), df.reset_index(drop=True)
    _batch("""INSERT INTO prices(code,date,open,high,low,close,volume) VALUES(%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT(code,date) DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
              close=EXCLUDED.close, volume=EXCLUDED.volume""",
           [(code, d[i], *[_nz(v.at[i, c]) for c in PRICE_COLS]) for i in range(len(v))])


def read_prices(code: str) -> pd.DataFrame:
    rows = db.get().execute("SELECT date,open,high,low,close,volume FROM prices WHERE code=%s ORDER BY date", (code,)).fetchall()
    if not rows:
        return pd.DataFrame(columns=PRICE_COLS)
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df.pop("date")); df.index.name = "date"
    return df[PRICE_COLS].astype(float)


# -- 개인/기관/외국인 수급 -----------------------------------------------------------------------
def upsert_flows(code: str, df: pd.DataFrame):
    d, v = _dates(df), df.reset_index(drop=True)
    _batch("""INSERT INTO flows(code,date,institution,other_corp,individual,foreign_net) VALUES(%s,%s,%s,%s,%s,%s)
              ON CONFLICT(code,date) DO UPDATE SET institution=EXCLUDED.institution, other_corp=EXCLUDED.other_corp,
              individual=EXCLUDED.individual, foreign_net=EXCLUDED.foreign_net""",
           [(code, d[i], *[_nz(v.at[i, c]) for c in FLOW_COLS]) for i in range(len(v))])


def read_flows(code: str) -> pd.DataFrame | None:
    rows = db.get().execute("SELECT date,institution,other_corp,individual,foreign_net FROM flows WHERE code=%s ORDER BY date", (code,)).fetchall()
    if not rows:
        return None
    df = pd.DataFrame(rows).rename(columns={"foreign_net": "foreign"})
    df.index = pd.to_datetime(df.pop("date")); df.index.name = "date"
    return df.astype(float)


# -- VIX ---------------------------------------------------------------------------------------
def upsert_vix(df: pd.DataFrame):
    d, v = _dates(df), df.reset_index(drop=True)
    _batch("INSERT INTO vix(date,close) VALUES(%s,%s) ON CONFLICT(date) DO UPDATE SET close=EXCLUDED.close",
           [(d[i], _nz(v.at[i, "close"])) for i in range(len(v)) if _nz(v.at[i, "close"]) is not None])


def latest_vix() -> float | None:
    r = db.get().execute("SELECT close FROM vix ORDER BY date DESC LIMIT 1").fetchone()
    return float(r["close"]) if r else None


# -- 보조 데이터셋 (JSONB) -----------------------------------------------------------------------
def save_dataset(dataset: str, code: str, df: pd.DataFrame):
    """날짜별 행을 JSONB 로 upsert. df 는 date 컬럼(또는 날짜 인덱스) + 임의의 숫자 컬럼."""
    if df is None or df.empty:
        return
    d = _dates(df)
    body = df.drop(columns=["date"], errors="ignore").reset_index(drop=True)
    recs = json.loads(body.to_json(orient="records"))  # NaN -> null, numpy -> 파이썬 숫자
    _batch("""INSERT INTO datasets(dataset,code,date,data) VALUES(%s,%s,%s,%s::jsonb)
              ON CONFLICT(dataset,code,date) DO UPDATE SET data=EXCLUDED.data, updated_at=now()""",
           [(dataset, code, d[i], json.dumps(recs[i])) for i in range(len(recs))])


def read_dataset(dataset: str, code: str) -> pd.DataFrame | None:
    rows = db.get().execute("SELECT date,data FROM datasets WHERE dataset=%s AND code=%s ORDER BY date", (dataset, code)).fetchall()
    if not rows:
        return None
    df = pd.DataFrame([r["data"] for r in rows], index=pd.to_datetime([r["date"] for r in rows]))
    df.index.name = "date"
    return df.apply(pd.to_numeric, errors="coerce")  # JSON null(None) → NaN: 컬럼이 전부 null 이어도 숫자형으로 통일
