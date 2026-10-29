"""백테스트 CLI.  사용: python -m stockwatch.backtest [종목코드 ...]

신호 규칙은 현행 전략의 가격 기반 부분(research/baseline.py)이다. 계산 엔진·지표·비용은 stockwatch/research/ 에 있다.
- 비용: config.yaml 의 backtest_costs(수수료·슬리피지) + research/tax_schedule.yaml(연도·시장별 세금, 양도일 기준). 실주문 설정과 분리.
- 전반부/후반부는 구간마다 현금에서 새로 시뮬레이션한다(경계 포지션 이월 없음, 구간 밖 정보 없음). '안정성 확인'용이다. 규칙을 전체 기간을 본 뒤 고르는 일이 있어 독립적인 아웃오브샘플 검증이 아니다.
- 수급·VIX·KOSPI 국면·업종 한도·보유 종목 수는 재현하지 않는다(과거 이력 부족 또는 1b 범위). 신호의 수급 가점은 임계값에서 15점을 빼 근사한다.
"""
import sys

from . import config, indicators as ind
from .cli import load_config
from .providers import DbProvider
from .research import metrics
from .research.baseline import BaselineRules, precompute_signals
from .research.costs import CostModel
from .research.data import complete_bars
from .research.engine import Params, buy_and_hold, simulate_window

HDR = f"  {'':<22}{'총수익':>8}{'연환산':>8}{'MDD':>8}{'Sharpe':>7}{'Sortino':>8}{'거래':>5}{'승률':>6}{'평균':>8}{'중앙':>8}{'PF':>6}{'보유(평균/중앙)':>14}{'노출':>6}{'연패':>5}"


def row(label: str, m: dict) -> str:
    pf = "∞" if m["pf"] == float("inf") else f"{m['pf']:.2f}"
    return (f"  {label:<22}{m['total']:>+8.1%}{m['cagr']:>+8.1%}{m['mdd']:>8.1%}{m['sharpe']:>7.2f}{m['sortino']:>8.2f}{m['trades']:>5}"
            f"{m['win']:>6.0%}{m['avg']:>+8.1%}{m['median']:>+8.1%}{pf:>6}{m['hold_mean']:>8.1f}/{m['hold_median']:<4.0f}{m['exposure']:>6.0%}{m['max_loss_streak']:>5}")


def main():
    cfg = load_config(); prov = DbProvider(); cost = CostModel.from_config(cfg)
    tr = cfg["trading_defaults"]            # 화면에서 바꾼 자본·한도(trading 덮어쓰기)가 연구 결과를 바꾸지 않도록 yaml 기본값 사용
    params = Params(stop_k=float(tr["stop_atr"]), target_k=float(tr["target_atr"]))
    codes = set(sys.argv[1:])
    items = [i for i in cfg["watchlist"] if not codes or i["code"] in codes]
    dfs = {}
    for it in items:
        df = complete_bars(prov.prices(it["code"]))
        if len(df) >= params.start + 60:
            dfs[it["code"]] = df
        else:
            print(f"[건너뜀] {it['code']} {it['name']}: 데이터 {len(df)}일 (필요 {params.start + 60}일 이상)")
    if not dfs:
        return
    n = min(len(d) for d in dfs.values())   # 서로 다른 길이는 최근 구간으로 맞춰 sleeve 평균이 같은 날짜를 보게 한다
    prepared, names = [], []
    for it in items:
        if it["code"] not in dfs:
            continue
        df = dfs[it["code"]].iloc[-n:]
        d = ind.add_all(df)
        rules = BaselineRules(df, d, precompute_signals(df, cfg.get("thresholds"), params.start), tr)
        prepared.append((df, d["atr"].to_numpy(), rules, config.market_of(cfg, it["code"]))); names.append(it["name"])
    dates = prepared[0][0].index; mid = params.start + (n - params.start) // 2
    print(f"기간 {dates[params.start].date()} ~ {dates[-1].date()} ({n - params.start}거래일) · 수수료 {cost.commission:.3%} · 슬리피지 편도 {cost.slippage_buy:.2%} · "
          f"세금: tax_schedule.yaml(양도일 기준, 2차 출처 기반)")
    for title, lo, hi in (("전체", params.start, n), (f"전반부 ({dates[params.start].date()}~{dates[mid - 1].date()})", params.start, mid), (f"후반부 ({dates[mid].date()}~{dates[-1].date()})", mid, n)):
        results = [simulate_window(df, atr, rules, params, mk, cost, lo, hi) for df, atr, rules, mk in prepared]
        bhs = [buy_and_hold(df.iloc[:hi], lo, cost) for df, _, _, _ in prepared]
        print(f"\n■ {title}  — 종목별 sleeve(각 종목에 자본 100% 투입, 이 구간을 현금에서 새로 시작)")
        print(HDR)
        for nm, r in zip(names, results):
            print(row(nm, metrics.single(r)))
        print(f"  {'-' * 20}")
        print(row("등가중 sleeve 포트폴리오", metrics.sleeve_portfolio(results)))
        print(row("Buy&Hold 등가중", metrics.sleeve_portfolio(bhs)))
        if lo == params.start and hi == n:
            full = results
    opens = [(nm, r.open_trade) for nm, r in zip(names, full) if r.open_trade]
    if opens:
        print("\n■ 미청산 포지션(거래 통계에서 제외, 평가손익만 자산곡선에 반영): " + ", ".join(f"{nm} {t.entry_date}~ {t.pnl_pct:+.1%}" for nm, t in opens))
    print("\n※ 노출 = 평균 투자 비중. 거래가 적고 종목이 한 업종에 몰려 있으면 통계적 의미가 약합니다. 참고용이며 수익을 보장하지 않습니다.")


if __name__ == "__main__":
    main()
