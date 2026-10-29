"""보정된 엔진(신호 우선 청산, 구간 독립 재시뮬레이션)으로 규칙 변형을 비교한다. 읽기 전용.
사용: .venv/bin/python scripts/research_variants.py
변형: 기준선 / RSI제외(RSI 진입상한·RSI≥70 청산 제거) / B1(20일선 아래 진입 금지) / B2(진입 후 20일선 위 마감을 한 번 본 뒤에만 20일선 이탈 청산) / B2+RSI제외
주의: 규칙을 전체 기간을 본 뒤 고른 비교라 독립 검증이 아니다.
"""
from dataclasses import replace

from stockwatch import config, indicators as ind
from stockwatch.backtest import row, HDR
from stockwatch.cli import load_config
from stockwatch.providers import DbProvider
from stockwatch.research import metrics
from stockwatch.research.baseline import precompute_signals
from stockwatch.research.variants import Variant
from stockwatch.research.costs import CostModel
from stockwatch.research.data import complete_bars
from stockwatch.research.engine import Params, simulate_window


VARIANTS = {"기준선": {}, "RSI제외": dict(rsi_free=True), "B1": dict(b1=True), "B2": dict(b2=True), "B2+RSI제외": dict(b2=True, rsi_free=True)}


def main():
    cfg = load_config(); prov = DbProvider(); cost = CostModel.from_config(cfg)
    tr = cfg["trading_defaults"]
    base = Params(stop_k=float(tr["stop_atr"]), target_k=float(tr["target_atr"]))
    dfs = {i["code"]: complete_bars(prov.prices(i["code"])) for i in cfg["watchlist"]}
    dfs = {c: d for c, d in dfs.items() if len(d) >= base.start + 60}
    n = min(len(d) for d in dfs.values()); mid = base.start + (n - base.start) // 2
    items = [i for i in cfg["watchlist"] if i["code"] in dfs]
    prep = []
    for it in items:
        df = dfs[it["code"]].iloc[-n:]; d = ind.add_all(df)
        prep.append((df, d, precompute_signals(df, cfg.get("thresholds"), base.start), d["atr"].to_numpy(), config.market_of(cfg, it["code"])))
    for pri in ("signal", "stop"):
        params = replace(base, exit_priority=pri)
        print(f"\n===== 청산 우선순위: {pri} =====")
        for title, lo, hi in (("전체", base.start, n), ("전반부", base.start, mid), ("후반부", mid, n)):
            print(f"\n■ {title}"); print(HDR)
            for name, kw in VARIANTS.items():
                res = [simulate_window(df, atr, Variant(df, d, R, tr, **kw), params, mk, cost, lo, hi) for df, d, R, atr, mk in prep]
                print(row(name, metrics.sleeve_portfolio(res)))


if __name__ == "__main__":
    main()
