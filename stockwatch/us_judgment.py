"""미국(나스닥) 종목 판단: 매수 검토(관심종목)와 매도 검토(직접 등록한 보유 종목). 주문 기능은 없다 — 화면에 근거를 보여줄 뿐이다.

한국 판정(signals.py·trading/engine.py)과 따로 만든다. 한국 판정은 수급·토스 보유·주문 승인에 묶여 있고, 미국에는
실적 발표 갭·거래대금·나스닥 대비 상대강도·시장 환경이라는 다른 위험이 있다. 지표 계산(indicators)과 추정 매물대(volume_profile)만 읽어서 쓴다.

규칙의 숫자는 한국에서 쓰는 값을 처음 값으로 가져온 '미검증 가설'이다 (RULES_VERSION=1). 미국 시세로 검증하기 전에는 참고용이다.
DB·네트워크 없이 DataFrame 만 받는 순수 계산이라 테스트하기 쉽고, 호출하는 쪽(us_review.py)이 데이터를 읽어 넣는다.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import indicators as ind
from . import volume_profile

RULES_VERSION = 1
ET = ZoneInfo("America/New_York")
STOP_K, TARGET_K, TRAIL_K = 2.0, 3.5, 2.5      # ATR 배수: 손절(진입가 아래), 목표(진입가 위), 트레일링(고점 아래)
MIN_ROWS = 65                                  # 60일선 계산에 필요한 최소 거래일. 120일선은 125일 이상일 때만 쓴다
EXT_MIN, EXT_MAX = -1.0, 1.5                   # 눌림 구간: 종가가 20일선보다 이만큼(ATR 단위) 아래~위
RSI_BUY_MAX, RSI_SELL = 65, 70
VIX_MAX, NASDAQ_MA = 25.0, 50                  # 시장 환경: VIX 가 이 값 미만 + 나스닥 종합이 50일선 위
EARNINGS_DAYS, LOW_LIQUIDITY_USD, BIG_GAP_PCT, STALE_DAYS = 7, 5_000_000, 10.0, 5
NOTE = (f"규칙 v{RULES_VERSION} · 손절 −{STOP_K:g} ATR · 목표 +{TARGET_K:g} ATR · 트레일링 {TRAIL_K:g} ATR · "
        "한국 전략의 값을 그대로 가져온 미검증 가설이라 참고용이에요. 주문 기능은 없고 투자 판단과 책임은 본인에게 있어요")

VERDICTS = {
    "review": "매수 검토", "watch": "관망", "blocked": "대기 (위험 구간)", "na": "판단 보류 (데이터 부족)",
    "sell": "매도 검토", "caution": "주의", "hold": "보유 유지",
}


def _chk(label: str, passed: bool, value: str, threshold: str = "") -> dict:
    return {"label": label, "passed": bool(passed), "value": value, "threshold": threshold}


def _info(label: str, value: str, threshold: str = "") -> dict:
    return {"label": label, "kind": "info", "value": value, "threshold": threshold}


def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _verdict(code: str, reason: str | None = None) -> dict:
    return {"code": code, "label": VERDICTS[code], "reason": reason}


def _unavailable(reason: str) -> dict:
    return {"verdict": _verdict("na", reason), "evidence": [], "levels": None, "price": None, "as_of": None, "unmet": 0,
            "note": NOTE, "rules_version": RULES_VERSION}


def regime(vix_close: pd.Series | None, nasdaq: pd.DataFrame | None) -> dict:
    """시장 환경: VIX < VIX_MAX 이고 나스닥 종합이 50일선 위. ok=None 이면 확인 불가(데이터 부족)."""
    vix = _f(vix_close.dropna().iloc[-1]) if vix_close is not None and len(vix_close.dropna()) else None
    above = None
    if nasdaq is not None and len(nasdaq) >= NASDAQ_MA:
        c = nasdaq["close"]
        ma = _f(c.rolling(NASDAQ_MA).mean().iloc[-1])
        above = None if ma is None else bool(c.iloc[-1] > ma)
    parts = [("VIX", None if vix is None else vix < VIX_MAX), ("나스닥 50일선", above)]
    ok = False if any(v is False for _, v in parts) else (None if any(v is None for _, v in parts) else True)
    text = " · ".join([f"VIX {vix:.1f}" if vix is not None else "VIX 확인 불가",
                       "나스닥 종합 50일선 " + ("위" if above else "아래") if above is not None else "나스닥 50일선 확인 불가"])
    return {"ok": ok, "vix": vix, "nasdaq_above_ma50": above, "text": text}


def _ret(c: pd.Series, n: int) -> float | None:
    return float((c.iloc[-1] / c.iloc[-1 - n] - 1) * 100) if len(c) > n and c.iloc[-1 - n] else None


def _earnings(meta: dict, today: date) -> tuple[int | None, str]:
    """(실적까지 남은 일수, 표시 문구). 일정이 없거나 이미 지났으면 일수는 None."""
    ne = meta.get("next_earnings")
    if isinstance(ne, datetime):
        ne = ne.date()
    if not isinstance(ne, date):
        return None, "실적일 확인 안 됨"
    days = (ne - today).days
    if days < 0:
        return None, f"저장된 실적일 {ne} 이 지났음 (갱신 필요)"
    return days, f"다음 실적 {ne} (D-{days})"


def _profile_info(df: pd.DataFrame) -> dict:
    label, note = "위 주요 추정 매물대", "일봉 범위 기반 추정치 · 판단에 반영하지 않음"
    try:
        p = volume_profile.compute(df)
    except Exception as e:
        return _info(label, f"계산 오류 — {e}", note)
    if not p["ok"]:
        return _info(label, f"계산 불가 — {p['reason']}", note)
    if p["inside"]:
        return _info(label, f"현재가가 두꺼운 매물대 {p['inside']['lo']:,.2f}~{p['inside']['hi']:,.2f} 안", note)
    u = p["up"]
    if not u:
        return _info(label, "위쪽 두꺼운 영역 없음", note)
    atr = f"(+{u['enter_atr']:.1f} ATR)" if u.get("enter_atr") is not None else ""
    return _info(label, f"위 {u['lo']:,.2f}~{u['hi']:,.2f} · 진입 {u['enter_pct']:+.1f}%{atr}", note)


def _common_info(d: pd.DataFrame, meta: dict) -> list[dict]:
    out = []
    gaps = ((d["open"] / d["close"].shift(1) - 1) * 100).iloc[-60:].dropna()
    if len(gaps):
        g = float(gaps.loc[gaps.abs().idxmax()])
        out.append(_info("최근 60일 최대 갭", f"{g:+.1f}%", f"{BIG_GAP_PCT:g}% 이상이면 손절선을 뛰어넘어 체결될 수 있어요" if abs(g) >= BIG_GAP_PCT else ""))
    sp = _f(meta.get("short_pct_float"))
    if sp is not None:
        out.append(_info("공매도 비중(유통주식 대비)", f"{sp:.1f}%", "20% 이상이면 급등락이 커질 수 있어요" if sp >= 20 else ""))
    out.append(_profile_info(d))
    return out


def _prepare(df: pd.DataFrame | None):
    """(지표 포함 프레임, 종가, ATR) 또는 (None, None, 사유)."""
    if df is None or len(df) < MIN_ROWS:
        return None, None, f"시세 {0 if df is None else len(df)}일치뿐이라 판단할 수 없어요 (최소 {MIN_ROWS}일)"
    d = ind.add_all(df)
    price, atr = _f(d["close"].iloc[-1]), _f(d["atr"].iloc[-1])
    if price is None or not atr or atr <= 0:
        return None, None, "종가 또는 ATR 을 계산할 수 없어요"
    return d, price, atr


def buy_review(df: pd.DataFrame | None, meta: dict | None = None, mkt: dict | None = None, bench: pd.DataFrame | None = None,
               today: date | None = None) -> dict:
    """관심종목의 매수 검토. 조건 미충족이어도 근거를 모두 돌려준다. 판정: 매수 검토 / 관망 / 대기(위험 구간) / 판단 보류."""
    meta, mkt = meta or {}, mkt or {"ok": None, "text": "시장 환경 확인 불가"}
    today = today or datetime.now(ET).date()
    d, price, atr = _prepare(df)
    if d is None:
        return _unavailable(atr)
    last = d.iloc[-1]
    as_of = d.index[-1].date()
    core, risk, missing = [], [], []

    ma60, ma120, ma20 = _f(last.ma60), _f(last.ma120), _f(last.ma20)
    long_ok = price > ma60 and (ma120 is None or price > ma120)
    core.append(_chk("중장기 추세: 60일선(·120일선) 위", long_ok, f"종가 {price:,.2f} / 60일선 {ma60:,.2f}" + (f" / 120일선 {ma120:,.2f}" if ma120 else ""),
                     "종가 > 60일선" + ("·120일선" if ma120 else " (120일선은 시세가 모자라 제외)")))
    slope = _f(ind.slope_pct(d["ma20"], 5))
    if slope is None:
        missing.append("20일선 방향")
    else:
        core.append(_chk("20일선 상승", slope > 0, f"최근 5일 기울기 {slope:+.2f}%/일", "> 0"))
    if ma20 is None:
        missing.append("20일선 눌림")
    else:
        ext = (price - ma20) / atr
        core.append(_chk("눌림목: 20일선 근처", EXT_MIN <= ext <= EXT_MAX, f"20일선 대비 {ext:+.1f} ATR", f"{EXT_MIN:+g} ~ {EXT_MAX:+g} ATR"))
    rsi = _f(last.rsi)
    if rsi is None:
        missing.append("RSI")
    else:
        core.append(_chk("RSI 과열 아님", rsi < RSI_BUY_MAX, f"{rsi:.0f}", f"< {RSI_BUY_MAX}"))
    rs = None
    if bench is not None and len(bench) > 20:
        b = bench["close"].loc[:d.index[-1]].dropna()
        r1, r2 = _ret(d["close"], 20), _ret(b, 20)
        rs = None if r1 is None or r2 is None else r1 - r2
    if rs is None:
        missing.append("나스닥 대비 상대강도")
    else:
        core.append(_chk("나스닥보다 강함 (20일 상대강도)", rs >= 0, f"{rs:+.1f}%p", ">= 0%p"))
    if mkt.get("ok") is None:
        missing.append("시장 환경")
    else:
        core.append(_chk(f"시장 환경: VIX < {VIX_MAX:g} · 나스닥 {NASDAQ_MA}일선 위", mkt["ok"], mkt["text"]))

    days, etext = _earnings(meta, today)
    earn_info = []
    if days is None:
        earn_info.append(_info("실적 발표 일정", etext, "실적 갭 위험을 직접 확인하세요"))
    else:
        risk.append(_chk(f"실적 발표 {EARNINGS_DAYS}일 이내 아님", not 0 <= days <= EARNINGS_DAYS, etext, f"D-{EARNINGS_DAYS} 보다 멀리"))
    dollar_vol = float((d["close"] * d["volume"]).iloc[-20:].mean())
    risk.append(_chk("유동성", dollar_vol >= LOW_LIQUIDITY_USD, f"20일 평균 거래대금 ${dollar_vol / 1e6:.1f}M", f">= ${LOW_LIQUIDITY_USD / 1e6:.0f}M"))
    stale = (today - as_of).days
    risk.append(_chk("시세 최신성", stale <= STALE_DAYS, f"마지막 완성 봉 {as_of} ({stale}일 전)", f"{STALE_DAYS}일 이내"))

    levels = {"ref": price, "stop": price - STOP_K * atr, "target": price + TARGET_K * atr, "atr": atr, "atr_pct": atr / price * 100,
              "stop_pct": -STOP_K * atr / price * 100, "target_pct": TARGET_K * atr / price * 100, "rr": TARGET_K / STOP_K}
    unmet = sum(not e["passed"] for e in core)
    if any(not e["passed"] for e in risk):
        verdict = _verdict("blocked", "실적·유동성·시세 중 위험 조건이 걸려 있어요")
    elif missing:
        verdict = _verdict("na", "확인하지 못한 항목: " + ", ".join(missing))
    elif unmet == 0:
        verdict = _verdict("review")
    else:
        verdict = _verdict("watch", f"조건 {unmet}개 미충족")
    ev = core + risk + earn_info + [_info(f"참고 {m}", "확인 불가", "이 항목을 못 구해 판정을 보류했어요") for m in missing] + _common_info(d, meta)
    ev.append(_info("참고 가격 (종가 기준)", f"손절 {levels['stop']:,.2f} ({levels['stop_pct']:+.1f}%) · 목표 {levels['target']:,.2f} ({levels['target_pct']:+.1f}%) · 손익비 1:{levels['rr']:.2f}",
                    "ATR 배수로 잡은 참고값이에요. 실제 체결가와 다를 수 있어요"))
    return {"verdict": verdict, "evidence": ev, "levels": levels, "price": price, "as_of": as_of.isoformat(), "unmet": unmet,
            "note": NOTE, "rules_version": RULES_VERSION}


def sell_review(df: pd.DataFrame | None, position: dict, meta: dict | None = None, mkt: dict | None = None, today: date | None = None) -> dict:
    """직접 등록한 보유 종목의 매도 검토. 근거의 passed=True 는 '그 조건에 해당함'이다(화면에서는 주황 !). 판정: 매도 검토 / 주의 / 보유 유지.
    손절·트레일링·20일선 이탈·목표가 중 하나라도 해당하면 매도 검토, 과열·실적 임박·시장 악화는 주의."""
    meta, mkt = meta or {}, mkt or {"ok": None, "text": "시장 환경 확인 불가"}
    today = today or datetime.now(ET).date()
    d, price, atr = _prepare(df)
    if d is None:
        return _unavailable(atr)
    qty, avg = float(position["qty"]), float(position["avg_price"])
    opened = position.get("opened_on")
    last = d.iloc[-1]
    as_of = d.index[-1].date()

    closes = d["close"]
    if opened:                                              # 진입 이전의 고점이 트레일링 기준에 섞이지 않게 진입일 이후만 본다
        since = closes[closes.index >= pd.Timestamp(opened)]
        hi = max(float(since.tail(20).max()), avg) if len(since) else max(avg, price)
        hi_label = "진입 이후 20일 고점"
    else:
        hi, hi_label = float(closes.tail(20).max()), "20일 고점"
    stop, trail, target = avg - STOP_K * atr, hi - TRAIL_K * atr, avg + TARGET_K * atr
    ma20, rsi = _f(last.ma20), _f(last.rsi)

    hard = [
        _chk(f"손절선 이탈 (평단 −{STOP_K:g} ATR)", price <= stop, f"현재 {price:,.2f} / 손절선 {stop:,.2f}", f"ATR×{STOP_K:g}"),
        _chk(f"트레일링 스탑 ({hi_label} −{TRAIL_K:g} ATR)", price <= trail, f"현재 {price:,.2f} / 기준 {trail:,.2f}", f"ATR×{TRAIL_K:g}"),
        _chk(f"목표가 도달 (평단 +{TARGET_K:g} ATR)", price >= target, f"현재 {price:,.2f} / 목표 {target:,.2f}", f"ATR×{TARGET_K:g}"),
    ]
    miss = []
    if ma20 is None:
        miss.append("20일선")
    else:
        hard.insert(2, _chk("20일선 이탈", price < ma20, f"종가 {price:,.2f} / 20일선 {ma20:,.2f}", "종가 < 20일선"))
    soft = []
    if rsi is None:
        miss.append("RSI")
    else:
        soft.append(_chk("RSI 과열", rsi >= RSI_SELL, f"{rsi:.0f}", f">= {RSI_SELL}"))
    days, etext = _earnings(meta, today)
    earn_info = []
    if days is None:
        earn_info.append(_info("실적 발표 일정", etext, "보유 중 실적 갭 위험을 직접 확인하세요"))
    else:
        soft.append(_chk(f"실적 발표 {EARNINGS_DAYS}일 이내", 0 <= days <= EARNINGS_DAYS, etext, f"D-{EARNINGS_DAYS} 이내"))
    if mkt.get("ok") is None:
        miss.append("시장 환경")
    else:
        soft.append(_chk("시장 환경 악화", not mkt["ok"], mkt["text"], f"VIX >= {VIX_MAX:g} 또는 나스닥 {NASDAQ_MA}일선 아래"))

    pnl_pct, pnl_val = (price / avg - 1) * 100, (price - avg) * qty
    hit_hard = [e["label"] for e in hard if e["passed"]]
    hit_soft = [e["label"] for e in soft if e["passed"]]
    if hit_hard:
        verdict = _verdict("sell", "해당: " + ", ".join(hit_hard))
    elif hit_soft:
        verdict = _verdict("caution", "해당: " + ", ".join(hit_soft))
    else:
        verdict = _verdict("hold", "확인하지 못한 항목: " + ", ".join(miss) if miss else None)
    sign = "-" if pnl_val < 0 else "+"
    head = _info("보유", f"{qty:g}주 @ {avg:,.2f} (평가손익 {pnl_pct:+.1f}% · {sign}${abs(pnl_val):,.0f})", "아래에서 주황 ! 표시가 현재 해당하는 조건이에요")
    ev = [head] + hard + soft + earn_info + [_info(f"참고 {m}", "확인 불가", "이 항목을 못 구했어요") for m in miss] + _common_info(d, meta)
    levels = {"stop": stop, "trail": trail, "target": target, "ma20": ma20, "atr": atr, "ref": price}
    return {"verdict": verdict, "evidence": ev, "levels": levels, "price": price, "as_of": as_of.isoformat(), "unmet": len(hit_hard) + len(hit_soft),
            "pnl_pct": pnl_pct, "pnl_value": pnl_val, "value": price * qty, "note": NOTE, "rules_version": RULES_VERSION}
