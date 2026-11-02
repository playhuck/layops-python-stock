"""미국 보유 종목(직접 입력) 저장. 주문 기능과 무관하다: 여기 값은 매도 검토 화면의 입력일 뿐이고 어디에도 주문을 내지 않는다."""
from __future__ import annotations

import math
from datetime import date, datetime

from . import db, usmarket
from .us_judgment import ET

MAX_QTY, MAX_PRICE, MAX_NOTE = 1e9, 1e7, 200


def validate(symbol, qty, avg_price, opened_on=None, note="", today: date | None = None) -> dict:
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
    return {"symbol": sym, "qty": q, "avg_price": p, "opened_on": opened, "note": str(note or "").strip()[:MAX_NOTE]}


def list_positions() -> list[dict]:
    return [dict(r) for r in db.get().execute("SELECT symbol,qty,avg_price,opened_on,note,updated_at FROM us_positions ORDER BY symbol").fetchall()]


def get(symbol: str) -> dict | None:
    r = db.get().execute("SELECT symbol,qty,avg_price,opened_on,note,updated_at FROM us_positions WHERE symbol=%s", (symbol,)).fetchone()
    return dict(r) if r else None


def register(symbol, qty, avg_price, opened_on=None, note="", name: str = "") -> dict:
    """보유 종목을 저장(같은 티커면 덮어씀). 관심 종목에 없으면 시세 수집과 함께 추가한다 — 시세가 있어야 판단할 수 있다.
    검증을 먼저 하고, 추가에 실패하면(티커 오류·한도 초과) 보유도 저장하지 않는다."""
    v = validate(symbol, qty, avg_price, opened_on, note)
    if v["symbol"] not in usmarket.watch_symbols():
        usmarket.add_and_collect(v["symbol"], name)
    db.get().execute(
        """INSERT INTO us_positions(symbol,qty,avg_price,opened_on,note) VALUES(%s,%s,%s,%s,%s)
           ON CONFLICT(symbol) DO UPDATE SET qty=EXCLUDED.qty, avg_price=EXCLUDED.avg_price, opened_on=EXCLUDED.opened_on,
           note=EXCLUDED.note, updated_at=now()""",
        (v["symbol"], v["qty"], v["avg_price"], v["opened_on"], v["note"]))
    return get(v["symbol"])


def delete(symbol: str) -> None:
    if db.get().execute("DELETE FROM us_positions WHERE symbol=%s", (symbol,)).rowcount == 0:
        raise KeyError(symbol)
