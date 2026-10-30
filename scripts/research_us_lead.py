"""미국 지표가 다음 날 한국 종목 수익률을 설명하는가? (읽기 전용: 한국 시세는 DB 에서 읽기만, 미국은 야후에서 직접 받는다)
사용: PYTHONPATH=. .venv/bin/python scripts/research_us_lead.py [순열횟수=2000]

시간 정렬: 한국 거래일 D 의 장 시작(09:00) 전에 이미 끝난 가장 최근 미국 세션 = 날짜가 D 보다 앞선 마지막 미국 거래일.
  (미국 세션 날짜 D-1 은 한국시간 D 일 05~06시에 마감한다)
  예측 변수: 그 미국 세션의 종가 대비 등락률.   목표: 한국 D 일의 갭(시가/전일종가), 장중(종가/시가), 종가-종가.
  플라시보: 한 세션 뒤(날짜가 D 이하인 마지막 세션 = 한국 D 일 장중~장 마감 이후에 열리는 세션)의 미국 등락률.
            갭은 이 값과 무관해야 한다. 플라시보에서도 상관이 크면 '미국이 한국을 이끈다'가 아니라 공통 요인/표본 특성 탓이다.
주의: 종목 8개·약 2년·한 번의 시장 국면, 종목들은 서로 같이 움직여서(반도체 5개) 독립 표본이 아니다. 순열 p값은 수익률이 독립이라는 가정이다.
      검정 수가 많아(종목×지표×목표) 개별 p<0.05 는 우연으로도 나온다 → 아래에 Bonferroni 기준을 함께 찍는다.
"""
import math
import sys

import numpy as np
import pandas as pd

from stockwatch import usmarket
from stockwatch.cli import load_config
from stockwatch.providers import DbProvider
from stockwatch.research.data import complete_bars

PRED = {"^SOX": "필라델피아 반도체", "^IXIC": "나스닥 종합", "^NDX": "나스닥100", "IWM": "러셀2000 ETF"}
TARGETS = ("gap", "intraday", "c2c")


def spearman_perm(x, y, n, seed=0):
    rx, ry = pd.Series(x).rank().to_numpy(), pd.Series(y).rank().to_numpy()
    r = float(np.corrcoef(rx, ry)[0, 1])
    rng = np.random.default_rng(seed)
    hits = sum(abs(np.corrcoef(rx, rng.permutation(ry))[0, 1]) >= abs(r) for _ in range(n))
    return r, (hits + 1) / (n + 1)


def normal_p(r: float, n: int) -> float:
    """스피어만 상관의 정규 근사 양측 p값 (n≈500 이면 충분). 순열 p값은 1/(순열횟수+1) 밑으로 내려가지 못해 Bonferroni 기준과 비교할 수 없다."""
    t = r * math.sqrt((n - 2) / max(1 - r * r, 1e-12))
    return math.erfc(abs(t) / math.sqrt(2))


def kr_targets(df: pd.DataFrame) -> pd.DataFrame:
    prev = df["close"].shift(1)
    return pd.DataFrame({"gap": df["open"] / prev - 1, "intraday": df["close"] / df["open"] - 1, "c2c": df["close"] / prev - 1}).dropna()


def us_for(days: pd.DatetimeIndex, us_ret: pd.Series, shift: int) -> pd.Series:
    """한국 거래일마다 대응하는 미국 세션 수익률. shift=0: D 이전 마지막 세션(예측), shift=1: 한 세션 뒤(플라시보)."""
    pos = us_ret.index.searchsorted(days, side="left") - 1 + shift
    ok = (pos >= 0) & (pos < len(us_ret))
    return pd.Series(np.where(ok, us_ret.to_numpy()[np.clip(pos, 0, len(us_ret) - 1)], np.nan), index=days)


def main():
    perms = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    cfg = load_config(); prov = DbProvider()
    bars, err = usmarket.fetch_bars(list(PRED), period="5y")
    if err:
        print("미국 데이터 실패:", err)
    us = {s: b["close"].pct_change().dropna() for s, b in bars.items()}
    kr = {i["code"]: (i["name"], kr_targets(complete_bars(prov.prices(i["code"])))) for i in cfg["watchlist"]}
    n_tests = len(kr) * len(us) * len(TARGETS)
    print(f"한국 {len(kr)}종목 × 미국 {len(us)}지표 × 목표 {len(TARGETS)} = {n_tests}개 검정 → Bonferroni 기준 p < {0.05 / n_tests:.4f} (개별 p<0.05 는 약 {0.05 * n_tests:.0f}개가 우연으로도 나옴)\n")

    pooled = {s: [] for s in us}
    for sym, name in PRED.items():
        if sym not in us:
            continue
        print(f"=== {sym} {name} → 한국 종목 (스피어만 상관, 괄호는 순열 p값) ===")
        print(f"{'종목':<14}{'n':>5} {'갭':>16} {'장중':>16} {'종가-종가':>16} {'갭(플라시보)':>16}")
        sig = 0
        for code, (kname, t) in kr.items():
            x = us_for(t.index, us[sym], 0); xp = us_for(t.index, us[sym], 1)
            cells = []
            for tgt in TARGETS:
                m = x.notna(); r, p = spearman_perm(x[m], t.loc[m, tgt], perms); cells.append(f"{r:+.2f} ({p:.3f})"); sig += normal_p(r, int(m.sum())) < 0.05 / n_tests
            m = xp.notna(); r, p = spearman_perm(xp[m], t.loc[m, "gap"], perms); cells.append(f"{r:+.2f} ({p:.3f})")
            print(f"{kname:<14}{int(x.notna().sum()):>5} " + " ".join(f"{c:>16}" for c in cells))
            pooled[sym].append(pd.DataFrame({"us": x, **{k: t[k] for k in TARGETS}}).dropna())
        print(f"  Bonferroni 통과 {sig}/{len(kr) * len(TARGETS)}건 (정규 근사 p 기준. 순열 p는 최소 {1 / (perms + 1):.4f}라 0.001은 '그보다 작다'는 뜻)\n")

    print("=== 조건부: 전날 미국 지표가 급락했을 때 한국 종목의 같은 날 평균 (전 종목 합산, 종목 간 상관 있음) ===")
    print(f"{'지표':<10}{'조건':<12}{'n':>5} {'갭 평균':>9} {'종가-종가 평균':>14} | {'그 외 n':>7} {'갭 평균':>9} {'종가-종가 평균':>14}")
    for sym, frames in pooled.items():
        a = pd.concat(frames)
        for label, cond in (("≤ -2%", a["us"] <= -0.02), ("≤ -1%", a["us"] <= -0.01), ("≥ +1%", a["us"] >= 0.01)):
            on, off = a[cond], a[~cond]
            if len(on) < 5:
                continue
            print(f"{sym:<10}{label:<12}{len(on):>5} {on['gap'].mean() * 100:>+8.2f}% {on['c2c'].mean() * 100:>+13.2f}% | {len(off):>7} {off['gap'].mean() * 100:>+8.2f}% {off['c2c'].mean() * 100:>+13.2f}%")


if __name__ == "__main__":
    main()
