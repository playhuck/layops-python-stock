"""리스크 계산 (순수 함수, 네트워크/DB 없음). 손실 제한 장치를 효과가 큰 순서로 구현한다.

1) 포지션 사이징: 손절 시 손실이 자본의 risk_per_trade_pct 이내가 되도록 수량을 역산
2) 갭 리스크: 손절가에 체결되지 않고 gap_assumption_pct 만큼 갭하락해도 계좌 손실이 max_gap_loss_pct 이내가 되도록 종목당 비중 상한
3) 한도금액(사용자 설정) / 1회 최대 / 일일 총 매수한도 / 업종 편중 / 보유 종목 수 상한
(킬스위치, 중복주문 차단, 시장 국면 필터는 engine.py 에서 상태와 함께 처리한다.)
"""
import math
from dataclasses import dataclass, field


def krx_tick(price: float, market: str = "STK") -> int:
    """KRX 호가가격단위. 코스닥은 5만원 이상 구간이 모두 100원."""
    for limit, tick in ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500)):
        if price < limit:
            return tick
    return 100 if market == "KSQ" else 1_000


def round_tick(price: float, market: str = "STK", mode: str = "down") -> int:
    t = krx_tick(price, market)
    fn = math.floor if mode == "down" else math.ceil
    r = int(fn(price / t) * t)
    return r if krx_tick(r, market) == t or mode == "down" else int(math.ceil(r / krx_tick(r, market)) * krx_tick(r, market))


def atr_levels(entry: float, atr: float, stop_k: float, target_k: float, market: str = "STK") -> tuple[int, int]:
    """진입가 기준 ATR 배수 손절가/익절가 (호가단위로 정렬: 손절은 내림, 익절은 올림)."""
    return round_tick(entry - stop_k * atr, market, "down"), round_tick(entry + target_k * atr, market, "up")


@dataclass
class Sizing:
    qty: int
    amount: int
    binding: str                       # 수량을 결정한 제약
    stop: int
    target: int
    max_loss: int                      # 손절가 체결 시 예상 손실(원)
    gap_loss: int                      # gap_assumption_pct 만큼 갭하락 시 손실(원)
    limits: dict = field(default_factory=dict)


def position_size(*, entry: float, atr: float, capital: float, budget: float, held_value: float = 0.0,
                  buying_power: float | None = None, daily_remaining: float | None = None,
                  max_order: float | None = None, risk_pct: float = 1.0, max_gap_loss_pct: float = 3.0,
                  gap_assumption_pct: float = 15.0, stop_k: float = 2.0, target_k: float = 3.5,
                  market: str = "STK") -> Sizing:
    """각 제약이 허용하는 금액 중 가장 작은 값으로 수량을 정한다 (정수 주, 현금 한도 내: 신용/레버리지 없음)."""
    stop, target = atr_levels(entry, atr, stop_k, target_k, market)
    risk_per_share = max(entry - stop, 1)
    limits = {
        "한도금액 잔여": max(budget - held_value, 0),
        "손절 리스크(자본×%)": capital * risk_pct / 100 / risk_per_share * entry,
        "갭 리스크 상한": capital * max_gap_loss_pct / gap_assumption_pct,
    }
    if max_order is not None:
        limits["1회 최대금액"] = max_order
    if buying_power is not None:
        limits["현금 매수가능금액"] = max(buying_power, 0)
    if daily_remaining is not None:
        limits["일일 매수한도 잔여"] = max(daily_remaining, 0)
    binding, amount = min(limits.items(), key=lambda kv: kv[1])
    qty = int(amount // entry)
    return Sizing(qty, int(qty * entry), binding, stop, target, int(qty * risk_per_share),
                  int(qty * entry * gap_assumption_pct / 100), {k: int(v) for k, v in limits.items()})
