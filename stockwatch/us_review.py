"""미국 종목 판단 화면용 조립: DB 에서 시세·메타·보유를 읽어 us_judgment(순수 계산)에 넣고 JSON 으로 내려도 되는 dict 로 돌려준다.
읽기 전용이다 — 주문도, 쓰기도 없다."""
from __future__ import annotations

import pandas as pd

from . import us_judgment as uj
from . import us_positions, usmarket


def _read_or_none(symbol: str) -> pd.DataFrame | None:
    try:
        df = usmarket._read(symbol)
    except Exception:
        return None
    return df if len(df) else None


def market_regime() -> dict:
    vix, nasdaq = _read_or_none("^VIX"), _read_or_none("^IXIC")
    return uj.regime(None if vix is None else vix["close"], nasdaq)


def _position_json(p: dict) -> dict:
    iso = lambda d: d.isoformat() if d else None
    return {"symbol": p["symbol"], "qty": p["qty"], "avg_price": p["avg_price"], "note": p["note"], "opened_on": iso(p["opened_on"]),
            "entry_atr": p["entry_atr"], "entry_atr_source": p["entry_atr_source"], "tracked_from": iso(p["tracked_from"])}


def judgments() -> dict:
    mkt, bench = market_regime(), _read_or_none("^IXIC")
    held = {p["symbol"]: p for p in us_positions.list_positions()}
    stocks = []
    for w in usmarket.watch_list():
        sym = w["symbol"]
        df, meta = _read_or_none(sym), usmarket.read_meta(sym)
        row = {"symbol": sym, "name": w["name"], "buy": uj.buy_review(df, meta, mkt, bench), "position": None, "sell": None}
        if sym in held:
            row["position"] = _position_json(held[sym])
            row["sell"] = uj.sell_review(df, held[sym], meta, mkt)
        stocks.append(row)
    return {"market": mkt, "stocks": stocks, "note": uj.NOTE, "rules_version": uj.RULES_VERSION}
