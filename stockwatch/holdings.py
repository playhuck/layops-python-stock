"""보유 정보의 출처 선택과 화면용 조회. 출처는 둘 중 하나만 보여준다(겹치지 않게):
  manual = 사용자가 직접 입력한 보유 (한국 kr_positions, 미국 us_positions) — 기본값
  import = 토스에서 가져온 보유 (한국만. 미국은 가져오기를 지원하지 않는다)
이 설정은 화면 표시만 바꾼다. 한국 거래 엔진(매도 제안 등)은 언제나 토스 보유를 기준으로 하며 이 모듈을 쓰지 않는다.
manual 모드에서는 토스를 조회하지 않는다(접속 실패·호출 낭비 방지)."""
from __future__ import annotations

import math
from datetime import date, datetime

from . import db, us_positions
from .us_judgment import ET

SOURCES = ("manual", "import")
KEY = "holdings_source"
MAX_PRICE, MAX_QTY, MAX_NOTE = 1e9, 1e9, 200
CODE_COLS = "code,qty,avg_price,opened_on,note,updated_at"


def get_source() -> str:
    r = db.get().execute("SELECT value FROM settings WHERE key=%s", (KEY,)).fetchone()
    return r["value"] if r and r["value"] in SOURCES else "manual"


def set_source(source: str) -> str:
    if source not in SOURCES:
        raise ValueError("보유 정보 출처는 manual(직접 입력) 또는 import(가져와서 반영)여야 합니다")
    db.get().execute("INSERT INTO settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value", (KEY, source))
    return source


# -- 한국 직접 입력 ----------------------------------------------------------------------------------
def kr_validate(code, qty, avg_price, opened_on=None, note="", today: date | None = None) -> dict:
    c = str(code or "").strip()
    if not (c.isdigit() and len(c) == 6):
        raise ValueError("종목 코드는 6자리 숫자여야 합니다")
    if not db.get().execute("SELECT 1 FROM watchlist WHERE code=%s", (c,)).fetchone():
        raise ValueError("관심 종목에 없는 종목입니다. 먼저 관심 종목에 추가하세요")
    try:
        q, p = float(qty), float(avg_price)
    except (TypeError, ValueError):
        raise ValueError("수량과 평균 매수가는 숫자여야 합니다") from None
    if not (math.isfinite(q) and 0 < q <= MAX_QTY and q == int(q)):
        raise ValueError("수량은 1 이상의 정수여야 합니다")
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
    return {"code": c, "qty": int(q), "avg_price": p, "opened_on": opened, "note": str(note or "").strip()[:MAX_NOTE]}


def kr_list() -> list[dict]:
    return [dict(r) for r in db.get().execute(f"SELECT {CODE_COLS} FROM kr_positions ORDER BY code").fetchall()]


def kr_register(code, qty, avg_price, opened_on=None, note="") -> dict:
    v = kr_validate(code, qty, avg_price, opened_on, note)
    db.get().execute(
        """INSERT INTO kr_positions(code,qty,avg_price,opened_on,note) VALUES(%s,%s,%s,%s,%s)
           ON CONFLICT(code) DO UPDATE SET qty=EXCLUDED.qty, avg_price=EXCLUDED.avg_price, opened_on=EXCLUDED.opened_on,
           note=EXCLUDED.note, updated_at=now()""", (v["code"], v["qty"], v["avg_price"], v["opened_on"], v["note"]))
    return next(p for p in kr_list() if p["code"] == v["code"])


def kr_delete(code: str) -> None:
    if db.get().execute("DELETE FROM kr_positions WHERE code=%s", (code,)).rowcount == 0:
        raise KeyError(code)


# -- 화면용 조회 -------------------------------------------------------------------------------------
def _iso(d) -> str | None:
    return d.isoformat() if d else None


def view(gateway=None) -> dict:
    """선택된 출처의 보유만 돌려준다. kr.known=False 는 '모름'(가져오기 실패/미연결)이라 화면이 '미보유'로 오해하지 않게 한다."""
    src = get_source()
    if src == "manual":
        return {"source": src,
                "kr": {"configured": True, "known": True, "items": {p["code"]: {"qty": p["qty"], "avg": p["avg_price"], "opened_on": _iso(p["opened_on"]), "note": p["note"]} for p in kr_list()}},
                "us": {"supported": True, "items": {p["symbol"]: {"qty": p["qty"], "avg": p["avg_price"], "opened_on": _iso(p["opened_on"]), "note": p["note"]} for p in us_positions.list_positions()}}}
    kr = {"configured": False, "known": False, "items": {}}
    if gateway is not None and gateway.configured():
        try:
            kr = {"configured": True, "known": True, "items": {h["symbol"]: {"qty": h["qty"], "avg": h["avg"], "last": h["last"]} for h in gateway.kr_holdings() if h["qty"] > 0}}
        except Exception:
            kr = {"configured": True, "known": False, "items": {}}
    return {"source": src, "kr": kr, "us": {"supported": False, "items": {}}}
