"""보유 종목의 추가 매수(물타기·불타기) 의견 — 한국·미국 공용 순수 계산(참고용, 주문 없음).
각 시장의 신규 매수 조건(buy)과 매도 검토(sell) 결과를 받아 '위험 우선' 순서로 의견을 정한다. DB·네트워크 없음."""
from __future__ import annotations

RULES_VERSION = 1
ADD_LABELS = {"pyramid": "불타기 검토", "avg_down": "물타기 검토", "hold": "추가 매수 보류", "avoid": "추가 매수 비권장"}
ADD_NEAR_STOP_ATR = 0.5                    # 손절선까지 이 배수(ATR) 안이면 추가 매수를 권하지 않는다
ADD_RATIOS = (0.5, 1.0)                    # 시뮬레이션: 보유 수량의 50%, 100% 를 지금 가격에 더 산다고 가정
US_CONDS = "추세·눌림·RSI·상대강도·시장 환경"                     # 미국 신규 매수 조건의 이름
KR_CONDS = "매수 점수·추세·눌림·RSI·VIX·수급·시장 국면"             # 한국 신규 매수 조건의 이름
ADD_NOTE = ("물타기(평단보다 낮을 때 추가 매수)는 손실을 키울 수 있어요. 그래서 신규 매수와 같은 조건이 다시 맞을 때만 의견을 내고, "
            "매도 검토 신호가 있거나 손절선이 가까우면, 시세가 오래됐거나 실적일을 모르면 권하지 않아요. 판단은 마지막 완성 봉 종가 기준이라 장중 가격·야간 갭과 달라요. "
            "규칙은 검증 전 가설이고 시뮬레이션은 예시(수수료·세금 제외)예요. 투자 판단과 책임은 본인에게 있어요")


def _info(label: str, value: str, threshold: str = "") -> dict:
    return {"label": label, "kind": "info", "value": value, "threshold": threshold}


def add_opinion(buy: dict, sell: dict, position: dict, cur: str = "usd", as_of: str | None = None, conds: str = US_CONDS) -> dict:
    """보유 종목의 추가 매수 의견(참고용). buy/sell 은 같은 종목의 신규 매수 조건·매도 검토 결과, position 은 {'qty','avg_price'}.
    cur='usd'|'krw': 금액 표기와 수량 단위(원화는 정수 주). as_of: 판단에 쓴 마지막 완성 봉(종가) 날짜 — 장중 현재가가 아니므로 화면에 같이 보여준다.
    conds: 시장별 신규 매수 조건의 이름(이유 문구용). 순서(위험 우선): 매도 검토 신호 → 데이터·위험 구간(실적일 미확인 포함) → 손절선 근접 → 신규 매수 조건.
      avoid(비권장) / hold(보류) / avg_down(물타기 검토: 평단보다 낮고 조건 충족) / pyramid(불타기 검토: 평단 이상이고 조건 충족)."""
    krw = cur == "krw"
    m2 = (lambda v: f"{v:,.0f}원") if krw else (lambda v: f"${v:,.2f}")
    m0 = (lambda v: f"{v:,.0f}원") if krw else (lambda v: f"${v:,.0f}")
    out = lambda code, reason, ev=None: {"code": code, "label": ADD_LABELS[code], "reason": reason, "as_of": as_of, "evidence": ev or [], "note": ADD_NOTE, "rules_version": RULES_VERSION}
    when = f"{as_of} 종가" if as_of else "기준 종가"
    price, lv = sell.get("price"), sell.get("levels") or {}
    if price is None or not lv.get("stop"):
        return out("hold", sell["verdict"].get("reason") or "시세가 부족해 판단할 수 없어요")
    qty, avg = float(position["qty"]), float(position["avg_price"])
    k_atr = lv.get("entry_atr") or lv.get("atr")
    stop = float(lv["stop"])
    pnl = (price / avg - 1) * 100
    gap = (price - stop) / k_atr if k_atr else None
    ev = [_info("내 위치", f"평단 대비 {pnl:+.1f}% — {'손실 중(물타기 구간)' if price < avg else '수익 중(불타기 구간)'}" + (f" · 손절선 {m2(stop)} 까지 {gap:.1f} ATR" if gap is not None else ""),
                "손절선·ATR 은 매도 검토와 같은 값(진입 ATR 고정)")]
    if as_of:
        ev.append(_info("판단 기준", f"{as_of} 종가 (마지막 완성 봉)", "장중 현재가가 아니에요. 야간 갭·장중 급변은 반영되지 않아요"))
    for r in ADD_RATIOS:
        add_qty = max(1, round(qty * r)) if krw else qty * r              # 한국은 정수 주
        new_qty = qty + add_qty
        new_avg = (qty * avg + add_qty * price) / new_qty
        loss_before, loss_after = max(avg - stop, 0) * qty, max(new_avg - stop, 0) * new_qty
        be = (new_avg / price - 1) * 100
        ev.append(_info(f"시뮬레이션: 보유의 {int(r * 100)}%({add_qty:g}주)를 {when}에 추가한다고 가정",
                        f"평단 {m2(avg)} → {m2(new_avg)} ({(new_avg / avg - 1) * 100:+.1f}%) · {when} 대비 본전까지 필요한 변화 {be:+.1f}% · 현재 손절선({m2(stop)}) 도달 시 예상손실 {m0(loss_before)} → {m0(loss_after)}",
                        "예시예요. 체결가는 기준 종가로 가정하고 수수료·세금은 빼요. 갭·슬리피지가 있으면 실제 손실은 이보다 클 수 있어요"))
    ev += list(buy.get("evidence") or [])
    sell_code, buy_code = sell["verdict"]["code"], buy["verdict"]["code"]
    if sell_code == "sell":
        return out("avoid", f"이미 매도 검토 신호가 있어요 ({sell['verdict'].get('reason') or '매도 조건 해당'}). 이때 추가 매수는 손실을 키울 수 있어요", ev)
    if sell_code == "na":
        return out("hold", sell["verdict"].get("reason") or "시세가 오래돼 판단할 수 없어요", ev)
    if gap is not None and gap < ADD_NEAR_STOP_ATR:
        return out("avoid", f"손절선까지 {gap:.1f} ATR 밖에 안 남았어요 ({ADD_NEAR_STOP_ATR:g} ATR 안). 추가 매수는 손실을 키울 수 있어요", ev)
    if buy_code in ("blocked", "na"):
        return out("hold", buy["verdict"].get("reason") or "위험 구간이거나 데이터가 부족해요", ev)
    if buy_code == "review_check":          # 실적일을 모르면 갭 위험을 확인할 수 없다 → 긍정 의견을 내지 않고 보류
        return out("hold", "실적일을 확인할 수 없어 추가 매수 의견을 보류해요. 실적일(갭 위험)을 직접 확인한 뒤 판단하세요", ev)
    if buy_code == "review":
        if price < avg:
            return out("avg_down", f"평단보다 낮지만 {conds} 조건이 다시 맞아요. 추가 매수 후 손절선은 새 평단 기준으로 다시 정하세요", ev)
        return out("pyramid", f"수익 중이고 {conds} 조건이 맞아요. 추격 매수를 피하는 눌림 구간이에요", ev)
    unmet = buy.get("unmet", 0)
    why = f"신규 매수 조건이 {unmet}개 맞지 않아요" if unmet else (buy["verdict"].get("reason") or "추가 매수 조건이 맞지 않아요")
    tail = " 손실 중 추가 매수(물타기)는 추세·눌림 조건이 다시 맞을 때만 의견을 내요." if price < avg else " 수익 중 추가 매수(불타기)는 눌림 구간에서만 의견을 내요(추격 매수 방지)."
    return out("hold", why + "." + tail, ev)
