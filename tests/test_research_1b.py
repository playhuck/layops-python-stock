"""1b: 자본 공유 포트폴리오, 블록 부트스트랩, 순열검정 보조, 변형 규칙."""
import numpy as np
import pandas as pd
import pytest

from stockwatch.research import metrics
from stockwatch.research.costs import CostModel
from stockwatch.research.engine import Params, simulate
from stockwatch.research.portfolio import Leg, PortfolioParams, simulate_portfolio
from stockwatch.research.stats import block_bootstrap, permutation_pvalue
from stockwatch.research.variants import RandomEntry, Trend20, Variant
from test_research import Script, frame

COST = CostModel()
P = Params(start=0, stop_k=2.0, target_k=3.5)


def walk(n=60, seed=1, drift=0.0):
    rng = np.random.default_rng(seed)
    c = 100 * np.cumprod(1 + rng.normal(drift, 0.02, n))
    o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.003, n))
    h = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.006, n))); l = np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.006, n)))
    return frame(o, h, l, c)


class Scored(Script):
    def __init__(self, entries=(), exits=(), score=0.0):
        super().__init__(entries, exits); self._s = score

    def score(self, i): return self._s


def leg(code, df, rules, group="G", atr=2.0, market="KSQ"):
    return Leg(code, group, df, np.full(len(df), float(atr)), rules, market, rules.score)


WIDE = PortfolioParams(risk_pct=1e9, max_weight=1.0, max_positions=1, group_cap=1.0)


def test_single_leg_full_weight_matches_the_sleeve_simulation_exactly():
    df = walk(80, seed=3)
    rules = Scored(entries={3, 15, 30, 50}, exits={9, 40})
    sleeve = simulate(df, np.full(len(df), 2.0), rules, P, "KSQ", COST)
    port = simulate_portfolio([leg("A", df, rules)], P, WIDE, COST, 0, len(df))
    assert port.sim.equity[0:] == pytest.approx(sleeve.equity, nan_ok=True)
    assert [(t.i0, t.i1) for t in port.sim.trades] == [(t.i0, t.i1) for t in sleeve.trades] and len(sleeve.trades) >= 3


def test_max_positions_blocks_the_lower_ranked_simultaneous_entry():
    df = walk(40, seed=4)
    r = simulate_portfolio([leg("A", df, Scored({5}, {}, score=10)), leg("B", df, Scored({5}, {}, score=90))], P,
                           PortfolioParams(risk_pct=1e9, max_weight=0.5, max_positions=1, group_cap=1.0), COST, 0, 40)
    assert r.entries == 1 and r.rejected["positions"] == 1


def test_group_cap_limits_same_group_but_not_different_groups():
    df = walk(40, seed=5)
    pp = PortfolioParams(risk_pct=1e9, max_weight=0.2, max_positions=3, group_cap=0.2)
    same = simulate_portfolio([leg("A", df, Scored({5})), leg("B", df, Scored({5}))], P, pp, COST, 0, 40)
    diff = simulate_portfolio([leg("A", df, Scored({5}), group="X"), leg("B", df, Scored({5}), group="Y")], P, pp, COST, 0, 40)
    assert same.entries == 1 and same.rejected["group"] == 1
    assert diff.entries == 2


def test_risk_sizing_loses_about_risk_pct_when_the_stop_is_hit():
    # 손절선에서 체결되면 손실 ≈ 자본의 risk_pct (+ 비중×왕복비용)
    c = [100] * 7
    df = frame(c, c, [100, 100, 100, 100, 96, 100, 100], c)                                  # 진입 다음 봉 저가 96 → 손절선(96.1) 체결, 갭 없음
    r = simulate_portfolio([leg("A", df, Scored({2}), atr=2.0)], P, PortfolioParams(risk_pct=0.01, max_weight=1.0, max_positions=3, group_cap=1.0), COST, 0, 7)
    assert r.sim.trades and r.sim.trades[0].reason.startswith("손절")
    loss = 1 - r.sim.equity[-1]
    assert 0.0095 < loss < 0.0125                                                           # 1% + 비용(≈0.43%×비중 ≈ 0.1%)


def test_portfolio_never_levers_or_goes_negative_on_random_paths():
    legs = [leg(f"S{k}", walk(120, seed=10 + k), Scored(set(range(3, 110, 4)), set(range(5, 110, 7)), score=k), group="G" if k < 3 else "H") for k in range(4)]
    r = simulate_portfolio(legs, P, PortfolioParams(), COST, 0, 120)
    assert np.all(np.isfinite(r.sim.equity)) and np.all(r.sim.equity > 0)
    assert r.sim.invested.max() <= 1.0 + 1e-9 and r.entries > 5


def test_block_bootstrap_ci_brackets_point_and_paired_identical_series_is_zero():
    rng = np.random.default_rng(0); a = rng.normal(0.001, 0.01, 300)
    ci = block_bootstrap(a, stat="sharpe", n_boot=500, seed=1)
    assert ci["lo"] < ci["point"] < ci["hi"]
    same = block_bootstrap(a, a, stat="total", n_boot=200, seed=1)
    assert same["point"] == 0 and same["lo"] == 0 and same["hi"] == 0


def test_permutation_pvalue_has_plus_one_correction():
    assert permutation_pvalue(1000.0, np.arange(99)) == pytest.approx(1 / 100)
    assert permutation_pvalue(-1.0, np.arange(99)) == 1.0


def test_variant_alt_exits_and_trend_and_random_entry_rules():
    v = Variant.__new__(Variant)
    v.tr = {}; v.exit_mode = "time5"; v.b2 = False; v.rsi_free = False
    pos = type("P", (), {"i0": 10})()
    assert not v.exit_signal(12, pos) and v.exit_signal(14, pos)                       # 체결 봉 포함 5봉 보유 후 다음 시가
    v.exit_mode = "none"; assert v.exit_signal(99, pos) is False
    base = type("B", (), {"c": np.array([1., 2., 1.]), "ma20": np.array([1.5, 1.5, 1.5]), "slope": np.array([1., 1., 1.]), "atr": np.ones(3)})()
    t = Trend20(base)
    assert not t.entry(0) and t.entry(1) and t.exit_signal(2, None) and not t.exit_signal(1, None)
    r = RandomEntry(Script(exits={4}), [3, 7])
    assert r.entry(3) and r.entry(7) and not r.entry(4) and r.exit_signal(4, None)
