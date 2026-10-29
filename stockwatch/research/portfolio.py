"""자본 공유 포트폴리오 시뮬레이션 (실전형 사이징 근사).

sleeve(종목마다 자본 100%)와 달리 한 자본 풀을 여러 종목이 나눠 쓴다. 날짜순으로 진입 후보를 모아 실전과 같은 순서(점수↓, ATR%↑, 코드)로 허용한다.
- 비중 = min(손절 시 자본의 risk_pct 만 잃는 크기, max_weight, 업종 한도 잔여, 현금) — 종목당 한도 max_gap_loss/gap_assumption 에 해당하는 max_weight 포함
- 동시 보유 수 ≤ max_positions, 같은 업종(group) 합계 ≤ group_cap (자본 대비, 시가 평가 기준)
- 포지션 손익은 종목별 sleeve 시뮬레이션(단위 자본)의 거래 수익률을 진입 금액에 곱해 반영한다(진입 후 비중 조정 없음).

모델링하지 않는 것(실전과 다름): 정수 주 단위 반올림, 월 손실 킬스위치, 시장 국면 필터, 승인 대기·재시도, 매도대금 결제(T+2) 지연(당일 재사용으로 가정), 호가단위.
"""
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from .costs import CostModel
from .engine import Params, SimResult, Trade, simulate_steps


@dataclass(frozen=True)
class PortfolioParams:
    risk_pct: float = 0.01        # 1종목 손절 시 자본 대비 손실 상한
    max_weight: float = 0.20      # 종목당 비중 상한 (= max_gap_loss_pct / gap_assumption_pct)
    max_positions: int = 3
    group_cap: float = 0.40       # 같은 업종 합계 상한 (= max_group_pct / 100)


@dataclass
class Leg:
    code: str
    group: str
    df: pd.DataFrame
    atr: np.ndarray
    rules: object
    market: str
    score: callable = field(default=lambda i: 0.0)     # 진입 우선순위 점수(높을수록 먼저)


@dataclass
class _Entry:
    k: int
    i0: int
    notional: float
    shares: float


@dataclass
class PortfolioResult:
    sim: SimResult
    entries: int
    rejected: dict


def simulate_portfolio(legs: list[Leg], params: Params, pp: PortfolioParams, cost: CostModel, lo: int, hi: int) -> PortfolioResult:
    """[lo, hi) 구간을 자본 1.0(현금)에서 시작하는 공유 자본 시뮬레이션."""
    prm = replace(params, start=lo)
    closes = [l.df["close"].to_numpy(dtype=float)[:hi] for l in legs]
    opens = [l.df["open"].to_numpy(dtype=float)[:hi] for l in legs]
    trade_lists = [[] for _ in legs]
    gens = [simulate_steps(l.df.iloc[:hi], l.atr[:hi], l.rules, prm, l.market, cost, trade_lists[k]) for k, l in enumerate(legs)]
    pending, results, accepted = {}, [None] * len(legs), []
    rejected = {"positions": 0, "group": 0, "size": 0, "cash": 0}

    def advance(k, send=None):
        try:
            pending[k] = next(gens[k]) if send is None else gens[k].send(send)
        except StopIteration as stop:
            pending.pop(k, None); results[k] = stop.value

    def exit_of(e: _Entry):
        """진입 e 의 청산 (i1, 반환금액) — 아직 안 닫혔으면 (None, None)."""
        for t in trade_lists[e.k]:
            if t.i0 == e.i0:
                return t.i1, e.notional * (1 + t.pnl_pct)
        return None, None

    def state(i: int):
        """결정일 i 종가 시점의 (자산, 업종별 평가액, i+1 시가 직전 현금, 그 시점 보유 수)."""
        cash_open, value_i, holding, groups = 1.0, 0.0, 0, {}
        cash_close = 1.0
        for e in accepted:
            i1, proceeds = exit_of(e)
            if e.i0 <= i + 1:
                cash_open -= e.notional
                if i1 is not None and i1 <= i + 1:
                    cash_open += proceeds
                else:
                    holding += 1
                    g = legs[e.k].group       # i+1 시가 이후에도 들고 있는 포지션의 업종 노출: 어제 종가 평가, 오늘 새로 승인된 진입은 진입 금액
                    groups[g] = groups.get(g, 0.0) + (e.notional if e.i0 == i + 1 else e.shares * closes[e.k][i])
            if e.i0 <= i:
                cash_close -= e.notional
                if i1 is not None and i1 <= i:
                    cash_close += proceeds
                else:
                    value_i += e.shares * closes[e.k][i]
        return cash_close + value_i, groups, cash_open, holding

    for k in range(len(gens)):
        advance(k)
    while pending:
        i = min(pending.values())
        ks = sorted((k for k, d in pending.items() if d == i), key=lambda k: (-legs[k].score(i), legs[k].atr[i] / closes[k][i], legs[k].code))
        decisions = {}
        for k in ks:
            equity, groups, cash_open, holding = state(i)
            leg, px = legs[k], closes[k][i]
            stop_frac = prm.stop_k * leg.atr[i] / px
            w = min(pp.risk_pct / stop_frac if stop_frac > 0 else 0.0, pp.max_weight)
            ok = True
            if holding >= pp.max_positions:
                rejected["positions"] += 1; ok = False
            else:
                room = pp.group_cap - groups.get(leg.group, 0.0) / equity
                if room <= 1e-9:
                    rejected["group"] += 1; ok = False
                else:
                    w = min(w, room)
                    notional = min(w * equity, cash_open)
                    if notional <= 1e-9 or w <= 0:
                        rejected["cash" if cash_open <= 1e-9 else "size"] += 1; ok = False
            if ok:
                e_fill = cost.fill_buy(opens[k][i + 1])
                accepted.append(_Entry(k, i + 1, notional, notional / (e_fill * (1 + cost.commission))))
            decisions[k] = ok
        for k in ks:
            advance(k, decisions[k])

    n, dates = hi, legs[0].df.index[:hi]
    cash = np.ones(n); value = np.zeros(n); trades = []
    for e in accepted:
        i1, proceeds = exit_of(e)
        res = results[e.k]
        end = n if i1 is None else i1
        cash[e.i0:] -= e.notional
        if i1 is not None:
            cash[i1:] += proceeds
        value[e.i0:end] += e.shares * closes[e.k][e.i0:end]
    for e in accepted:
        for t in trade_lists[e.k]:
            if t.i0 == e.i0:
                trades.append(replace(t, pnl_amt=e.notional * t.pnl_pct))
    eq = cash + value
    eq[:lo] = np.nan
    inv = np.zeros(n); inv[lo:] = value[lo:] / eq[lo:]
    return PortfolioResult(SimResult(n, lo, dates, eq, inv, trades, None), len(accepted), rejected)
