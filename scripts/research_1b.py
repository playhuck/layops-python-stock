"""1b 연구: 비교 전략·대체 청산·자본 공유 사이징·순열검정·부트스트랩. 읽기 전용(DB 쓰기 없음).
사용: PYTHONPATH=. .venv/bin/python scripts/research_1b.py [순열횟수=1000]

모든 비교는 같은 기간을 보고 규칙을 고른 탐색이다(독립 검증 아님). 표본은 약 6종목(대부분 반도체), 상승장 한 번, 거래 20~40건.
오늘 봉이 미완성이면 제외한다(research/data.py).
"""
import sys
import time
from dataclasses import replace

import numpy as np

from stockwatch import config, indicators as ind, sectors
from stockwatch.backtest import HDR, row
from stockwatch.cli import load_config
from stockwatch.providers import DbProvider
from stockwatch.research import metrics, stats
from stockwatch.research.baseline import precompute_signals
from stockwatch.research.costs import CostModel
from stockwatch.research.data import complete_bars
from stockwatch.research.engine import Params, buy_and_hold, simulate_window
from stockwatch.research.portfolio import Leg, PortfolioParams, simulate_portfolio
from stockwatch.research.variants import RandomEntry, Trend20, Variant

N_PERM = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
cfg = load_config(); prov = DbProvider(); cost = CostModel.from_config(cfg); tr = cfg["trading_defaults"]
base = Params(stop_k=float(tr["stop_atr"]), target_k=float(tr["target_atr"]))
NOSTOP = replace(base, stop_k=1e9, target_k=1e9)       # 손절·목표가 없는 순수 규칙 청산

raw = {i["code"]: complete_bars(prov.prices(i["code"])) for i in cfg["watchlist"]}
raw = {c: d for c, d in raw.items() if len(d) >= base.start + 60}
n = min(len(d) for d in raw.values()); mid = base.start + (n - base.start) // 2
items = [i for i in cfg["watchlist"] if i["code"] in raw]
CLS = sectors.classify([i["code"] for i in items])      # 실전 업종 한도와 같은 토스 업종 분류(대분류)
t0 = time.time()
PREP = []
for it in items:
    df = raw[it["code"]].iloc[-n:]; d = ind.add_all(df)
    PREP.append(dict(code=it["code"], name=it["name"], group=(CLS.get(it["code"]) or {}).get("primary") or it.get("group") or it["code"], df=df, d=d, R=precompute_signals(df, cfg.get("thresholds"), base.start),
                     atr=d["atr"].to_numpy(), mk=config.market_of(cfg, it["code"])))
WINDOWS = (("전체", base.start, n), ("전반부", base.start, mid), ("후반부", mid, n))
print(f"종목 {len(items)}개 · 기간 {PREP[0]['df'].index[base.start].date()}~{PREP[0]['df'].index[-1].date()} · 업종 구성 {sorted({p['group'] for p in PREP})} · 신호 준비 {time.time() - t0:.0f}s")


def rules_for(p, kind, **kw):
    v = Variant(p["df"], p["d"], p["R"], tr, **kw)
    return Trend20(v) if kind == "trend" else v


def sleeve(kind_kw, params, lo, hi, kind="variant"):
    return [simulate_window(p["df"], p["atr"], rules_for(p, kind, **kind_kw), params, p["mk"], cost, lo, hi) for p in PREP]


def section(title):
    print(f"\n{'=' * 8} {title} {'=' * 8}")


# ---- A. 비교 전략 + 같은 진입·다른 청산 -----------------------------------------------------------------
for pri in ("signal", "stop"):
    prm = replace(base, exit_priority=pri); prm0 = replace(NOSTOP, exit_priority=pri)
    section(f"A. sleeve 비교 — 청산 우선순위 {pri}")
    for title, lo, hi in WINDOWS:
        print(f"\n■ {title}"); print(HDR)
        print(row("Buy&Hold", metrics.sleeve_portfolio([buy_and_hold(p["df"].iloc[:hi], lo, cost) for p in PREP])))
        print(row("20일선 추세(순수)", metrics.sleeve_portfolio(sleeve({}, prm0, lo, hi, "trend"))))
        print(row("20일선 추세+손절/익절", metrics.sleeve_portfolio(sleeve({}, prm, lo, hi, "trend"))))
        print(row("기준선(현행)", metrics.sleeve_portfolio(sleeve({}, prm, lo, hi))))
        print(row("B2", metrics.sleeve_portfolio(sleeve(dict(b2=True), prm, lo, hi))))
        print("  -- 같은 진입(현행), 다른 청산 --")
        for mode in ("ma20_only", "trail_only", "sell_score_only", "time5", "time10", "none"):
            print(row(f"청산:{mode}", metrics.sleeve_portfolio(sleeve(dict(exit_mode=mode), prm, lo, hi))))

# ---- B. 자본 공유 사이징 --------------------------------------------------------------------------------
pp = PortfolioParams(risk_pct=float(tr["risk_per_trade_pct"]) / 100, max_weight=float(tr["max_gap_loss_pct"]) / float(tr["gap_assumption_pct"]),
                     max_positions=int(tr["max_positions"]), group_cap=float(tr["max_group_pct"]) / 100)
print(f"\n{'=' * 8} B. 자본 공유 사이징 (위험 {pp.risk_pct:.1%}/건, 종목 ≤{pp.max_weight:.0%}, 동시 {pp.max_positions}종목, 업종 ≤{pp.group_cap:.0%}) {'=' * 8}")
print("※ 정수 주·킬스위치·시장 국면 필터·T+2 결제 지연·호가단위 미반영. 후보일 단위 거절 횟수(같은 종목이 며칠 연속 거절되면 중복 집계).")
for pri in ("signal", "stop"):
    prm = replace(base, exit_priority=pri)
    for title, lo, hi in WINDOWS:
        print(f"\n■ {title} — {pri}"); print(HDR)
        for nm, kind, kw in (("20일선 추세", "trend", {}), ("기준선(현행)", "variant", {}), ("B2", "variant", dict(b2=True))):
            legs = [Leg(p["code"], p["group"], p["df"], p["atr"], rules_for(p, kind, **kw), p["mk"], getattr(rules_for(p, kind, **kw), "score")) for p in PREP]
            r = simulate_portfolio(legs, prm, pp, cost, lo, hi)
            print(row(nm, metrics.single(r.sim)), f"  진입 {r.entries} · 거절 {r.rejected}")

# ---- C. 제약 있는 순열검정 ------------------------------------------------------------------------------
section(f"C. 제약 있는 순열검정 (랜덤 진입 {N_PERM}회, 같은 종목별 진입 횟수·같은 청산 규칙·같은 비용, 신호 우선, 전체 구간)")
prm = replace(base, exit_priority="signal"); lo, hi = base.start, n
rng = np.random.default_rng(20260429)
for nm, kw in (("기준선(현행)", {}), ("B2", dict(b2=True))):
    actual = sleeve(kw, prm, lo, hi)
    obs_total = metrics.sleeve_portfolio(actual)["total"]
    allt = [t.pnl_pct for r in actual for t in r.trades]; obs_mean = float(np.mean(allt))
    counts = [len(r.trades) + (1 if r.open_trade else 0) for r in actual]
    rules = [rules_for(p, "variant", **kw) for p in PREP]
    all_days = [np.array([i for i in range(lo, hi - 1) if not np.isnan(p["atr"][i])]) for p in PREP]
    struct = [np.array([i for i in range(lo, hi - 1) if not np.isnan(p["atr"][i]) and rules[k].slope[i] > 0 and (rules[k].c[i] - rules[k].ma20[i]) / rules[k].atr[i] <= 1.5
                        and rules[k].rsi[i] < 65]) for k, p in enumerate(PREP)]
    print(f"\n■ {nm}: 관측 sleeve 총수익 {obs_total:+.1%}, 거래당 평균 {obs_mean:+.2%}, 종목별 진입 {counts}, 구조 조건 통과일 {[len(x) for x in struct]}")
    for label, pool in (("A) 아무 날이나", all_days), ("B) 추세·눌림 구조 조건을 통과한 날만(점수 효과 분리)", struct)):
        tot, mean = [], []
        for _ in range(N_PERM):
            res = [simulate_window(p["df"], p["atr"], RandomEntry(rules[k], rng.choice(pool[k], size=min(counts[k], len(pool[k])), replace=False)), prm, p["mk"], cost, lo, hi)
                   for k, p in enumerate(PREP)]
            tot.append(metrics.sleeve_portfolio(res)["total"]); ts = [t.pnl_pct for r in res for t in r.trades]; mean.append(float(np.mean(ts)) if ts else 0.0)
        tot, mean = np.array(tot), np.array(mean)
        print(f"   {label}: 총수익 null 중앙 {np.median(tot):+.1%} (5~95% {np.quantile(tot, .05):+.1%}~{np.quantile(tot, .95):+.1%}) p={stats.permutation_pvalue(obs_total, tot):.3f} | "
              f"거래당 평균 null 중앙 {np.median(mean):+.2%} p={stats.permutation_pvalue(obs_mean, mean):.3f}")

# ---- D. 블록 부트스트랩 ---------------------------------------------------------------------------------
section("D. 블록 부트스트랩 95% 신뢰구간 (원형 블록 10일, 3000회, sleeve 포트폴리오 일별 수익률, 신호 우선, 전체 구간)")
eqs = {"기준선": metrics.sleeve_equity(sleeve({}, prm, lo, hi))[0], "B2": metrics.sleeve_equity(sleeve(dict(b2=True), prm, lo, hi))[0],
       "20일선 추세": metrics.sleeve_equity(sleeve({}, replace(NOSTOP, exit_priority="signal"), lo, hi, "trend"))[0],
       "Buy&Hold": metrics.sleeve_equity([buy_and_hold(p["df"].iloc[:hi], lo, cost) for p in PREP])[0]}
rets = {k: stats.daily_returns(v) for k, v in eqs.items()}
m = min(len(v) for v in rets.values()); rets = {k: v[-m:] for k, v in rets.items()}
for k, v in rets.items():
    s_, t_ = stats.block_bootstrap(v, stat="sharpe", n_boot=3000, seed=1), stats.block_bootstrap(v, stat="total", n_boot=3000, seed=1)
    print(f"  {k:<12} Sharpe {s_['point']:+.2f} [{s_['lo']:+.2f}, {s_['hi']:+.2f}] · 총수익 {t_['point']:+.1%} [{t_['lo']:+.1%}, {t_['hi']:+.1%}]")
print("  -- 짝지은 차이(같은 날짜 인덱스를 공유) --")
for a_, b_ in (("B2", "기준선"), ("기준선", "20일선 추세"), ("B2", "20일선 추세"), ("B2", "Buy&Hold")):
    s_, t_ = stats.block_bootstrap(rets[a_], rets[b_], stat="sharpe", n_boot=3000, seed=2), stats.block_bootstrap(rets[a_], rets[b_], stat="total", n_boot=3000, seed=2)
    print(f"  {a_} − {b_:<10} Sharpe {s_['point']:+.2f} [{s_['lo']:+.2f}, {s_['hi']:+.2f}] P(≤0)={s_['p_le_0']:.2f} · 총수익 {t_['point']:+.1%} [{t_['lo']:+.1%}, {t_['hi']:+.1%}]")
