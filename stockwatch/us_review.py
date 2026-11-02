"""미국 종목 판단 화면용 조립: DB 에서 시세·메타·보유를 읽어 us_judgment(순수 계산)에 넣고 JSON 으로 내려도 되는 dict 로 돌려준다.
읽기 전용이다 — 주문도, 쓰기도 없다."""
from __future__ import annotations

import math
from datetime import date, datetime

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


FX_SYMBOL = "KRW=X"            # 야후의 원/달러 환율 (1달러당 원). 시장 지표와 함께 매일 수집된다


def fx_now(today: date | None = None) -> dict:
    """현재 원/달러 환율(마지막 완성 봉 종가). 없거나 오래됐으면 rate=None 과 사유."""
    today = today or datetime.now(uj.ET).date()
    df = _read_or_none(FX_SYMBOL)
    if df is None:
        return {"rate": None, "as_of": None, "reason": "원/달러 환율을 아직 받지 못했어요 (미국 갱신 후 표시돼요)"}
    rate, as_of = float(df["close"].iloc[-1]), df.index[-1].date()
    if not (math.isfinite(rate) and rate > 0):
        return {"rate": None, "as_of": as_of.isoformat(), "reason": "환율 값이 비정상이에요"}
    if (today - as_of).days > uj.STALE_DAYS:
        return {"rate": None, "as_of": as_of.isoformat(), "reason": f"환율이 {(today - as_of).days}일 전 값이라 쓰지 않아요 (미국 갱신 필요)"}
    return {"rate": rate, "as_of": as_of.isoformat(), "reason": None}


def krw_view(price: float | None, position: dict, fx: dict) -> dict:
    """원화 기준 손익(표시 전용 — 판단·손절·목표는 달러 기준 그대로다). (1+주가 손익)×(1+환율 변화)−1 = 원화 손익률.
    매수 시점 환율이 없거나 현재 환율을 못 쓰면 available=False 와 사유."""
    qty, avg, fxb = float(position["qty"]), float(position["avg_price"]), position.get("fx_buy")
    out = {"available": False, "reason": None, "fx_buy": fxb, "fx_now": fx.get("rate"), "fx_as_of": fx.get("as_of"), "price_pct": None}
    if price is None:
        return {**out, "reason": "시세를 알 수 없어요"}
    out["price_pct"] = (price / avg - 1) * 100
    if fxb is None:
        return {**out, "reason": "매수 시점 환율을 입력하면 원화 손익을 볼 수 있어요"}
    if fx.get("rate") is None:
        return {**out, "reason": fx.get("reason")}
    cost, value = qty * avg * fxb, qty * price * fx["rate"]
    return {**out, "available": True, "cost_krw": cost, "value_krw": value, "pnl_krw": value - cost, "pnl_pct": (value / cost - 1) * 100,
            "fx_pct": (fx["rate"] / fxb - 1) * 100}


def _position_json(p: dict) -> dict:
    iso = lambda d: d.isoformat() if d else None
    return {"symbol": p["symbol"], "qty": p["qty"], "avg_price": p["avg_price"], "note": p["note"], "opened_on": iso(p["opened_on"]),
            "entry_atr": p["entry_atr"], "entry_atr_source": p["entry_atr_source"], "tracked_from": iso(p["tracked_from"]),
            "fx_buy": p["fx_buy"]}


def judgments() -> dict:
    mkt, bench, fx = market_regime(), _read_or_none("^IXIC"), fx_now()
    held = {p["symbol"]: p for p in us_positions.list_positions()}
    stocks = []
    for w in usmarket.watch_list():
        sym = w["symbol"]
        df, meta = _read_or_none(sym), usmarket.read_meta(sym)
        row = {"symbol": sym, "name": w["name"], "buy": uj.buy_review(df, meta, mkt, bench), "position": None, "sell": None, "krw": None, "add": None}
        if sym in held:
            row["position"] = _position_json(held[sym])
            row["sell"] = uj.sell_review(df, held[sym], meta, mkt)
            row["krw"] = krw_view(row["sell"]["price"], held[sym], fx)
            row["add"] = uj.add_opinion(row["buy"], row["sell"], held[sym], as_of=row["sell"]["as_of"])         # 물타기·불타기 의견(참고용, 주문 없음)
        stocks.append(row)
    return {"market": mkt, "fx": fx, "stocks": stocks, "note": uj.NOTE, "rules_version": uj.RULES_VERSION}
