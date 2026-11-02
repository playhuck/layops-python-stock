"""미국 보유 종목(직접 입력) 저장. 주문 기능과 무관하다: 여기 값은 매도 검토 화면의 입력일 뿐이고 어디에도 주문을 내지 않는다."""
from __future__ import annotations

import math
from datetime import date, datetime

import pandas as pd

from . import db, usmarket
from . import indicators as ind
from .us_judgment import ET

MAX_QTY, MAX_PRICE, MAX_NOTE = 1e9, 1e7, 200
ATR_MIN_ROWS = 30                    # 진입 ATR 을 계산하려면 그 시점까지 이만큼의 시세가 있어야 한다
COLS = "symbol,qty,avg_price,opened_on,note,updated_at,entry_atr,entry_atr_source,tracked_from,fx_buy"
FX_MIN, FX_MAX = 100.0, 10_000.0      # 원/달러. 달러/원을 거꾸로 넣은 실수(예: 0.00073)나 자릿수 오타를 걸러낸다


def validate(symbol, qty, avg_price, opened_on=None, note="", today: date | None = None, entry_atr=None, fx_buy=None) -> dict:
    """입력을 검증해 정규화한 dict 를 돌려준다. 잘못된 값은 ValueError (메시지는 화면에 그대로 보인다)."""
    sym = str(symbol or "").strip().upper()
    if not usmarket.SYMBOL_RE.match(sym):
        raise ValueError("티커는 영문 1~5자여야 합니다 (예: SOUN)")
    try:
        q, p = float(qty), float(avg_price)
    except (TypeError, ValueError):
        raise ValueError("수량과 평균 매수가는 숫자여야 합니다") from None
    if not (math.isfinite(q) and 0 < q <= MAX_QTY):
        raise ValueError("수량은 0보다 커야 합니다")
    if not (math.isfinite(p) and 0 < p <= MAX_PRICE):
        raise ValueError("평균 매수가는 0보다 커야 합니다")
    opened = None
    if opened_on not in (None, ""):
        try:
            opened = date.fromisoformat(str(opened_on))
        except ValueError:
            raise ValueError("매수일은 YYYY-MM-DD 형식이어야 합니다") from None
        if opened > (today or datetime.now(ET).date()):
            raise ValueError("매수일이 미래입니다")
    atr = None
    if entry_atr not in (None, ""):
        try:
            atr = float(entry_atr)
        except (TypeError, ValueError):
            raise ValueError("진입 ATR 은 숫자여야 합니다") from None
        if not (math.isfinite(atr) and 0 < atr <= p):
            raise ValueError("진입 ATR 은 0보다 크고 평균 매수가보다 작아야 합니다")
    fx = None
    if fx_buy not in (None, ""):
        try:
            fx = float(fx_buy)
        except (TypeError, ValueError):
            raise ValueError("매수 시점 환율은 숫자여야 합니다") from None
        if not (math.isfinite(fx) and FX_MIN <= fx <= FX_MAX):
            raise ValueError(f"매수 시점 환율은 원/달러로 {FX_MIN:,.0f}~{FX_MAX:,.0f} 사이여야 합니다 (예: 1,350)")
    return {"symbol": sym, "qty": q, "avg_price": p, "opened_on": opened, "note": str(note or "").strip()[:MAX_NOTE], "entry_atr": atr, "fx_buy": fx}


def snapshot_atr(df: pd.DataFrame | None, opened_on: date | None) -> tuple[float | None, str | None]:
    """(진입 ATR, 출처). 매수일이 있고 그 이전 시세가 충분하면 '매수일 직전 거래일 종가까지'의 ATR(출처 opened),
    아니면 가장 최근 ATR(출처 registered). 시세가 모자라면 (None, None) — 판단 쪽이 현재 ATR 로 대신 계산하고 그렇다고 표시한다."""
    if df is None or df.empty:
        return None, None

    def last_atr(x: pd.DataFrame) -> float | None:
        if len(x) < ATR_MIN_ROWS:
            return None
        a = float(ind.atr(x).iloc[-1])
        return a if math.isfinite(a) and a > 0 else None
    if opened_on:
        a = last_atr(df[df.index < pd.Timestamp(opened_on)])
        if a:
            return a, "opened"
    a = last_atr(df)
    return (a, "registered") if a else (None, None)


def list_positions() -> list[dict]:
    return [dict(r) for r in db.get().execute(f"SELECT {COLS} FROM us_positions ORDER BY symbol").fetchall()]


def get(symbol: str) -> dict | None:
    r = db.get().execute(f"SELECT {COLS} FROM us_positions WHERE symbol=%s", (symbol,)).fetchone()
    return dict(r) if r else None


def register(symbol, qty, avg_price, opened_on=None, note="", name: str = "", entry_atr=None, fx_buy=None) -> dict:
    """보유 종목을 저장(같은 티커면 덮어씀). 관심 종목에 없으면 시세 수집과 함께 추가한다 — 시세가 있어야 판단할 수 있다.
    검증을 먼저 하고, 추가에 실패하면(티커 오류·한도 초과) 보유도 저장하지 않는다.

    진입 ATR: 직접 입력하면 그 값(manual), 아니면 자동 계산해 저장한다(snapshot_atr). 평균 매수가·매수일이 그대로고 직접 입력도 없으면
    기존에 저장한 진입 ATR·트레일링 시작일을 유지한다 — 메모나 수량만 고쳤다고 손절선이 오늘 변동성으로 다시 잡히면 안 된다."""
    v = validate(symbol, qty, avg_price, opened_on, note, entry_atr=entry_atr, fx_buy=fx_buy)
    sym = v["symbol"]
    if sym not in usmarket.watch_symbols():
        usmarket.add_and_collect(sym, name)
    prev = get(sym)
    keep = bool(prev and prev["entry_atr"] and v["entry_atr"] is None and prev["avg_price"] == v["avg_price"] and prev["opened_on"] == v["opened_on"])
    if keep:
        atr, src, tracked = prev["entry_atr"], prev["entry_atr_source"], prev["tracked_from"]
    else:
        if v["entry_atr"] is not None:
            atr, src = v["entry_atr"], "manual"
        else:
            try:
                atr, src = snapshot_atr(usmarket._read(sym), v["opened_on"])
            except Exception:
                atr, src = None, None
        tracked = v["opened_on"] or datetime.now(ET).date()
    db.get().execute(
        """INSERT INTO us_positions(symbol,qty,avg_price,opened_on,note,entry_atr,entry_atr_source,tracked_from,fx_buy) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT(symbol) DO UPDATE SET qty=EXCLUDED.qty, avg_price=EXCLUDED.avg_price, opened_on=EXCLUDED.opened_on,
           note=EXCLUDED.note, entry_atr=EXCLUDED.entry_atr, entry_atr_source=EXCLUDED.entry_atr_source, tracked_from=EXCLUDED.tracked_from,
           fx_buy=EXCLUDED.fx_buy, updated_at=now()""",
        (sym, v["qty"], v["avg_price"], v["opened_on"], v["note"], atr, src, tracked, v["fx_buy"]))
    return get(sym)


def delete(symbol: str) -> None:
    if db.get().execute("DELETE FROM us_positions WHERE symbol=%s", (symbol,)).rowcount == 0:
        raise KeyError(symbol)
