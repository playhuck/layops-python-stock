"""추정 매물대(위쪽 저항 근접도)가 진입 후 가격 행동과 관련 있는가? (읽기 전용: DB 읽기만, 쓰기 없음)
사용: PYTHONPATH=. .venv/bin/python scripts/research_volume_profile.py [부트스트랩횟수=2000]

설계(사전 고정):
- 사건 = 현행 전략의 '가격 기반 매수 조건 통과일'(BaselineRules.entry). 수급·VIX·국면·업종 한도는 재현하지 않는다(research/baseline.py 와 같은 약속).
  표본이 작아 보조로 '구조 조건 통과일'(20일선 상승·눌림 ≤1.5ATR·RSI<65, 점수 조건 제외)도 같이 본다.
- 결정일 i 의 종가까지의 데이터로만 매물대를 계산한다(미래 정보 없음). 체결은 i+1 시가(+슬리피지), 비용 포함.
- 탐색 구간(1~5절) 주 비교: 위 저항(두꺼운 영역 하단)까지 거리 ≤ 2ATR(=손절폭) 인 '가까움' vs 그 밖('멈'). 영역 안이면 거리 0으로 '가까움'.
  (독립 표본 6절의 주 비교는 아래 '독립 표본' 항목: 가까움 vs 위 영역은 있으나 멂)
  주 결과: 10일 MFE(ATR), 목표 선도달 확률(같은 봉에서 둘 다 닿으면 손절 먼저). 나머지는 기술 통계.
- 기본 설정(120일·24구간·1.5배·범위 균등) 하나만 '주 결과'이고, 나머지 조합은 결과가 뒤집히는지 보는 민감도다. 가장 좋은 조합을 고르지 않는다.
- 불확실성: 날짜 단위 원형 블록 부트스트랩(같은 날 여러 종목이 함께 움직임). 종목 고정효과·ATR%·20일선/60일선 이격 보정, 종목별 10일 간격 비중첩 표본도 병기.
독립 표본: 사건일이 사양 파일(scripts/research_volume_profile_spec.json)의 oos_start(2026-11-02) 이상인 사건만 '독립 표본'이다.
  사양 파일에는 종목·시장 구분, 계산법, 임계값·비용 해시, 주 모집단(현행 매수 조건 통과일)·주 비교(가까움 vs 위 영역은 있으나 멂)·최소 표본, 그리고
  이 스크립트 자신과 핵심 코드의 해시가 들어 있다. 하나라도 어긋나면 6번 절이 '독립 검증 아님'으로 표시한다(규칙을 바꾸려면 새 사양·새 기준일).
  '위 영역 없음'은 비교군이 아니라 기술 통계다(고점 모멘텀과 섞이기 때문). 사양 파일은 스크립트와 함께 커밋해야 변경 이력이 남는다.
한계: 9종목·약 2년·한 번의 국면, 같은 종목의 연속 신호는 겹친다. 같은 기간을 보고 설계한 탐색이라 독립 검증이 아니다.
"""
import hashlib
import json
import sys
import time
from dataclasses import replace
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from stockwatch import config, indicators as ind, sectors, volume_profile as vp
from stockwatch.cli import load_config
from stockwatch.providers import DbProvider
from stockwatch.research.baseline import BaselineRules, precompute_signals
from stockwatch.research.costs import CostModel, settlement_date
from stockwatch.research.data import complete_bars
from stockwatch.research.engine import Params, simulate
from stockwatch.research.profile_events import forward_outcomes, zone_after

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "scripts" / "research_volume_profile_spec.json"      # 독립 표본 사양(종목·시장·계산법·규칙·주 분석·코드 해시). 스크립트와 함께 커밋한다
SPEC = json.loads(SPEC_PATH.read_text(encoding="utf-8"))
OOS_START = date.fromisoformat(SPEC["oos_start"])
NEAR_ATR, HORIZON, MIN_GROUP = SPEC["near_atr"], SPEC["horizon"], SPEC["min_group"]
N_BOOT = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
CONFIGS = {   # 이름: compute 인자. 'main' 만 주 결과
    "main": {},
    "기간60": {"window": 60}, "기간250": {"window": 250}, "구간12": {"bins": 12}, "구간36": {"bins": 36},
    "두께1.25": {"thick": 1.25}, "두께2.0": {"thick": 2.0}, "대표가격": {"allocation": "close"},
}

cfg = load_config(); prov = DbProvider(); cost = CostModel.from_config(cfg); tr = cfg["trading_defaults"]
base = Params(stop_k=float(tr["stop_atr"]), target_k=float(tr["target_atr"]))
sha = lambda b: hashlib.sha256(b).hexdigest()[:16]


def spec_drift() -> list[str]:
    """고정 사양과 지금 실행 환경의 차이. 비어 있으면 독립 표본 조건을 만족한다."""
    out = []
    blob = json.dumps({"thresholds": cfg.get("thresholds"), "trading_defaults": cfg["trading_defaults"], "backtest_costs": cfg.get("backtest_costs")}, sort_keys=True, ensure_ascii=False, default=str)
    if sha(blob.encode()) != SPEC["cfg_sha"]:
        out.append("임계값·거래 기본값·비용 설정이 고정값과 다름")
    for f, h in SPEC["files"].items():                       # 분석 스크립트 자신도 포함: 표본 선택·분류·검정 로직이 바뀌면 여기서 드러난다
        if sha((ROOT / f).read_bytes()) != h:
            out.append(f"코드 변경: {f}")
    cur = {"method_version": vp.METHOD_VERSION, "window": vp.WINDOW, "bins": vp.BINS, "thickness_multiple": vp.THICK, "allocation": "range_overlap"}
    if cur != SPEC["profile"]:
        out.append(f"매물대 기본 파라미터 변경: {cur}")
    watch = {i["code"]: i for i in cfg["watchlist"]}
    for code, meta in SPEC["codes"].items():
        if code not in watch:
            out.append(f"{code} {meta['name']}: 관심종목에서 빠짐 — 신규 시세 수집이 멈춰 독립 표본이 더는 쌓이지 않음")
        elif config.market_of(cfg, code) != meta["market"]:
            out.append(f"{code} {meta['name']}: 시장 구분이 {meta['market']} → {config.market_of(cfg, code)} 로 바뀜(거래세 계산에 쓰임)")
    return out


FROZEN_ITEMS = [{"code": c, "name": m["name"], "market": m["market"]} for c, m in SPEC["codes"].items()]     # 이후에 추가된 종목은 연구에 넣지 않는다. 시장 구분도 고정값을 쓴다
raw = {i["code"]: complete_bars(prov.prices(i["code"])) for i in FROZEN_ITEMS}
raw = {c: d for c, d in raw.items() if len(d) >= base.start + 60}
n = min(len(d) for d in raw.values())
items = [i for i in FROZEN_ITEMS if i["code"] in raw]
t0 = time.time()
PREP = []
for it in items:
    df = raw[it["code"]].iloc[-n:]; d = ind.add_all(df)
    PREP.append(dict(code=it["code"], name=it["name"], df=df, d=d, R=precompute_signals(df, cfg.get("thresholds"), base.start), atr=d["atr"].to_numpy(), mk=it["market"]))
# 부트스트랩은 인덱스 i 를 '같은 날짜'로 쓴다. 종목마다 거래일이 어긋나면 조용히 틀리므로 미리 막는다.
for p_ in PREP[1:]:
    if not p_["df"].index.equals(PREP[0]["df"].index):
        raise SystemExit(f"종목 간 거래일이 다릅니다({p_['code']}): 날짜 기준 정렬이 필요합니다. 연구를 중단합니다.")
print(f"종목 {len(PREP)}개 · 기간 {PREP[0]['df'].index[base.start].date()}~{PREP[0]['df'].index[-1].date()} · 신호 준비 {time.time() - t0:.0f}s")


def up_distance(prof: dict) -> float:
    """위 저항 영역 하단까지 ATR 거리. 영역 안이면 0, 위쪽 두꺼운 영역이 없거나 계산 불가면 inf(=멈)."""
    if not prof["ok"] or prof["atr"] is None:
        return float("inf")
    if prof["inside"] is not None:
        return 0.0
    return prof["up"]["enter_atr"] if prof["up"] else float("inf")


rows = []
for k, p in enumerate(PREP):
    df, d, R = p["df"], p["d"], p["R"]
    rules = BaselineRules(df, d, R, tr)
    o, h, l, c = (df[x].to_numpy(float) for x in ("open", "high", "low", "close"))
    ma60 = d["ma60"].to_numpy(float)
    for i in range(base.start, n - 1 - HORIZON):
        a = p["atr"][i]
        loose = rules.slope[i] > 0 and (c[i] - rules.ma20[i]) / a <= 1.5 and rules.rsi[i] < 65
        if not (loose and np.isfinite(a) and a > 0):
            continue
        entry = cost.fill_buy(o[i + 1])
        fo = forward_outcomes(o, h, l, c, i, a, entry, base.stop_k, base.target_k, (5, 10))
        if fo is None:
            continue
        sell_f = cost.sell_factor(settlement_date(df.index, i + 10), p["mk"])
        row = dict(code=p["code"], name=p["name"], i=i, date=df.index[i].date(), base=bool(rules.entry(i)), atr_pct=a / c[i] * 100,
                   ext20=(c[i] - rules.ma20[i]) / a, gap60=(c[i] - ma60[i]) / a, ret10=c[i + 10] * sell_f / (entry * (1 + cost.commission)) - 1,
                   ret5=c[i + 5] * cost.sell_factor(settlement_date(df.index, i + 5), p["mk"]) / (entry * (1 + cost.commission)) - 1,
                   **{k_: v for k_, v in fo.items() if not k_.startswith("close")})
        hist = df.iloc[: i + 1]
        for name, kw in CONFIGS.items():
            prof = vp.compute(hist, **kw)
            row[f"dist_{name}"] = up_distance(prof)
            if name == "main":
                row["up_lo"], row["up_hi"] = (prof["up"]["lo"], prof["up"]["hi"]) if prof["ok"] and prof["up"] else (np.nan, np.nan)
                row["inside"] = bool(prof["ok"] and prof["inside"] is not None)
                row["profile_ok"] = prof["ok"]
                if prof["ok"] and prof["inside"] is not None:
                    row["up_lo"], row["up_hi"] = prof["inside"]["lo"], prof["inside"]["hi"]
                for tag, pick in (("tk", lambda b: b["mult"] >= vp.THICK), ("th", lambda b: b["mult"] < 1.0)):      # 같은 폭(한 구간)의 두꺼운/얇은 구간으로 비교
                    b = next((b for b in prof["bins"] if b["lo"] > prof["price"] and pick(b)), None) if prof["ok"] else None
                    z = zone_after(c, i, b["lo"], b["hi"]) if b else None
                    row[f"{tag}_dist"] = (b["lo"] - prof["price"]) / a if b else np.nan
                    row[f"{tag}_reached"], row[f"{tag}_out"] = (z["reached"], z["outcome"]) if z else (False, None)
        rows.append(row)
E_ALL = pd.DataFrame(rows)
E_ALL["win"] = (E_ALL["first_cons"] == "target").astype(float); E_ALL["lose"] = (E_ALL["first_cons"] == "stop").astype(float)
E_ALL["win_opt"] = (E_ALL["first_opt"] == "target").astype(float)
E_ALL["oos"] = E_ALL["date"] >= OOS_START
E = E_ALL[~E_ALL["oos"]].reset_index(drop=True)          # 1~5번 절은 탐색 구간(사건일 < OOS_START)만 본다
print(f"사건: 구조 조건 통과일 {len(E)}건 · 그중 현행 매수 조건 통과 {int(E['base'].sum())}건 · 매물대 계산 불가 {int((~E['profile_ok']).sum())}건")
print("종목별(현행 조건 통과):", E[E["base"]].groupby("name").size().to_dict())


# ---- 통계 ----------------------------------------------------------------------------------------------
def design(S: pd.DataFrame, near: np.ndarray) -> np.ndarray:
    cols = [np.ones(len(S)), near.astype(float), S["atr_pct"].to_numpy(), S["ext20"].to_numpy(), S["gap60"].to_numpy()]
    dummies = pd.get_dummies(S["code"], drop_first=True).to_numpy(float)
    return np.column_stack(cols + ([dummies] if dummies.size else []))


def effect(S, near, y, w, adjust):
    """near − far 평균 차이(가중). adjust=True 면 종목 고정효과·ATR%·20일선 이격·60일선 이격 보정 후 near 계수."""
    w = np.asarray(w, float)
    if w[near].sum() == 0 or w[~near].sum() == 0:
        return np.nan
    if not adjust:
        return float(np.average(y[near], weights=w[near]) - np.average(y[~near], weights=w[~near]))
    X, sw = design(S, near), np.sqrt(w)
    beta = np.linalg.lstsq(X * sw[:, None], y * sw, rcond=None)[0]
    return float(beta[1])


def boot(S, near, ycol, adjust, seed=0, block=10):
    """날짜 단위 원형 블록 부트스트랩 95% 구간 (같은 날의 여러 종목은 함께 뽑힌다)."""
    y, day = S[ycol].to_numpy(float), S["i"].to_numpy()
    point = effect(S, near, y, np.ones(len(S)), adjust)
    rng = np.random.default_rng(seed)
    nb, out = int(np.ceil(n / block)), []
    for _ in range(N_BOOT):
        starts = rng.integers(0, n, nb)
        days = ((starts[:, None] + np.arange(block)[None, :]) % n).ravel()[:n]
        w = np.bincount(days, minlength=n)[day].astype(float)
        v = effect(S, near, y, w, adjust)
        if not np.isnan(v):
            out.append(v)
    lo, hi = np.quantile(out, [0.025, 0.975]) if len(out) > 50 else (np.nan, np.nan)
    return point, float(lo), float(hi)


def nonoverlap(S, gap=10):
    keep = []
    for _, g in S.groupby("code"):
        last = -10 ** 9
        for ix, i in zip(g.index, g["i"]):
            if i - last >= gap:
                keep.append(ix); last = i
    return S.loc[keep]


OUT = (("mfe10", "10일 MFE(ATR)"), ("win", "목표 선도달"), ("mae10", "10일 MAE(ATR)"), ("ret10", "10일 순수익률"), ("lose", "손절 선도달"))


def report(S: pd.DataFrame, label: str):
    near = (S["dist_main"] <= NEAR_ATR).to_numpy()
    print(f"\n■ {label}: 사건 {len(S)}건 · 가까움(≤{NEAR_ATR}ATR, 영역 안 {int(S['inside'].sum())}건 포함) {int(near.sum())}건 · 멈 {int((~near).sum())}건 · 종목 {S['code'].nunique()}개 · 날짜 {S['i'].nunique()}일")
    if near.sum() < 3 or (~near).sum() < 3:
        print("   표본이 너무 적어 비교하지 않는다."); return
    print(f"   {'지표':<14}{'가까움':>10}{'멈':>10}{'차이(원)':>10}{'95% 구간':>20}{'차이(보정)':>12}{'95% 구간':>20}")
    for col, nm in OUT:
        a_, b_ = S.loc[near, col].mean(), S.loc[~near, col].mean()
        pt, lo, hi = boot(S, near, col, False, seed=1)
        pa, loa, hia = boot(S, near, col, True, seed=2)
        f = (lambda v: f"{v:+.1%}") if col in ("win", "lose", "ret10") else (lambda v: f"{v:+.2f}")
        print(f"   {nm:<14}{f(a_):>10}{f(b_):>10}{f(pt):>10}{'[' + f(lo) + ', ' + f(hi) + ']':>20}{f(pa):>12}{'[' + f(loa) + ', ' + f(hia) + ']':>20}")
    print(f"   (낙관 시나리오 — 같은 봉에서 둘 다 닿으면 목표 먼저) 목표 선도달: 가까움 {S.loc[near, 'win_opt'].mean():.1%} · 멈 {S.loc[~near, 'win_opt'].mean():.1%}")


print("\n" + "=" * 8 + " 1. 주 결과 (기본 설정: 120일·24구간·1.5배·범위 균등) " + "=" * 8)
Pm = E[E["base"]].reset_index(drop=True)
report(Pm, "현행 매수 조건 통과일")
report(nonoverlap(Pm).reset_index(drop=True), "  └ 같은 종목 10일 간격 비중첩")
report(E.reset_index(drop=True), "구조 조건 통과일(보조, 표본 큼)")
report(nonoverlap(E).reset_index(drop=True), "  └ 같은 종목 10일 간격 비중첩")
print("\n--- 혼란 점검: '멈'에 위쪽 두꺼운 영역이 아예 없는 날(고점 근처 모멘텀)이 섞이므로, 위쪽 영역이 있는 날끼리만 비교 ---")
report(E[np.isfinite(E["dist_main"])].reset_index(drop=True), "구조 조건 통과일 중 위쪽 영역이 있는 날")
report(Pm[np.isfinite(Pm["dist_main"])].reset_index(drop=True), "현행 매수 조건 통과일 중 위쪽 영역이 있는 날")

print("\n--- 3분류: 가까움(영역 안 포함 ≤2ATR) / 위 영역은 있지만 멂 / 위쪽 두꺼운 영역 없음 ---")
for lab, S_ in (("현행 매수 조건 통과일", Pm), ("구조 조건 통과일", E)):
    cls3 = np.where(S_["dist_main"] <= NEAR_ATR, "가까움", np.where(np.isfinite(S_["dist_main"]), "영역 있으나 멂", "위 영역 없음"))
    print(f"  {lab}")
    for nm_ in ("가까움", "영역 있으나 멂", "위 영역 없음"):
        g = S_[cls3 == nm_]
        if len(g) == 0:
            print(f"    {nm_:<14} 0건"); continue
        print(f"    {nm_:<14} {len(g):>4}건 · MFE10 {g['mfe10'].mean():+.2f} · MAE10 {g['mae10'].mean():+.2f} · 목표 선도달 {g['win'].mean():.1%} · 손절 선도달 {g['lose'].mean():.1%} · 10일 순수익률 {g['ret10'].mean():+.1%}")

print("\n" + "=" * 8 + " 2. 민감도: 설정을 바꿔도 부호·크기가 유지되는가 (구조 조건 통과일, 10일 MFE 차이=가까움−멈, 날짜 블록 95% 구간) " + "=" * 8)
print("※ 가장 좋은 조합을 고르려는 표가 아니다. 방향이 뒤집히는 설정이 있는지만 본다.")
print(f"   {'설정':<10}{'가까움/멈':>12}{'MFE차(원)':>12}{'95% 구간':>20}{'MFE차(보정)':>13}{'목표선도달 차이':>16}")
for name in CONFIGS:
    S = E.reset_index(drop=True); nr = (S[f"dist_{name}"] <= NEAR_ATR).to_numpy()
    if nr.sum() < 3 or (~nr).sum() < 3:
        print(f"   {name:<10}{int(nr.sum())}/{int((~nr).sum()):>10}   표본 부족"); continue
    pt, lo, hi = boot(S, nr, "mfe10", False, seed=3); pa, _, _ = boot(S, nr, "mfe10", True, seed=4)
    dw = S.loc[nr, "win"].mean() - S.loc[~nr, "win"].mean()
    print(f"   {name:<10}{f'{int(nr.sum())}/{int((~nr).sum())}':>12}{pt:>+12.2f}{f'[{lo:+.2f}, {hi:+.2f}]':>20}{pa:>+13.2f}{dw:>+16.1%}")
print("   기준 거리 민감도(기본 설정):")
for T in (1.0, 1.5, 2.0, 3.5):
    S = E.reset_index(drop=True); nr = (S["dist_main"] <= T).to_numpy()
    if nr.sum() < 3 or (~nr).sum() < 3:
        print(f"   ≤{T}ATR {int(nr.sum())}/{int((~nr).sum())} 표본 부족"); continue
    pt, lo, hi = boot(S, nr, "mfe10", False, seed=5)
    print(f"   ≤{T}ATR  가까움/멈 {int(nr.sum())}/{int((~nr).sum())} · MFE차 {pt:+.2f} [{lo:+.2f}, {hi:+.2f}] · 목표선도달 차이 {S.loc[nr, 'win'].mean() - S.loc[~nr, 'win'].mean():+.1%}")

print("\n" + "=" * 8 + " 3. 저항 영역에 닿은 뒤 (종가 기준, 도달 10일 이내 → 이후 5일 안에 상단 위 마감=돌파 / 하단 아래 마감=반락) " + "=" * 8)
print("※ 대조: 현재가 위에서 가장 가까운 '두꺼운 한 구간'(평균 1.5배 이상) vs '얇은 한 구간'(1배 미만)에 같은 규칙. 폭이 같은 한 구간끼리, 거리 4ATR 이내만.")


def zone_table(S, tag, label):
    ok = (S[f"{tag}_dist"] <= 4.0)
    reached = ok & S[f"{tag}_reached"]
    s_ = S.loc[reached, f"{tag}_out"].value_counts()
    dec = int(s_.get("break", 0) + s_.get("reject", 0))
    rj = s_.get("reject", 0) / dec if dec else float("nan")
    print(f"   {label:<10} 4ATR 이내 {int(ok.sum())}건 · 도달 {int(reached.sum())}건 · 돌파 {int(s_.get('break', 0))} 반락 {int(s_.get('reject', 0))} 정체 {int(s_.get('stall', 0))} 미결 {int(s_.get('censored', 0))} · 결판난 것 중 반락 {rj:.0%} (n={dec})")


zone_table(E, "tk", "두꺼운 구간")
zone_table(E, "th", "얇은 구간")
zone_table(nonoverlap(E), "tk", "└ 비중첩 두꺼움")
zone_table(nonoverlap(E), "th", "└ 비중첩 얇음")

print("\n" + "=" * 8 + " 4. 하드 게이트를 적용했다면 (현행 전략 sleeve 거래 중 진입일의 위 저항이 ≤2ATR 이었던 거래) " + "=" * 8)
print("※ 거래 단위 정적 분류다. 아래 '합'은 거래별 수익률의 단순 합이며 자본곡선 수익률이 아니다(하나를 빼면 이후 진입 시점·복리 경로가 달라지는데 반영하지 않음).")
dist_lookup = {(r.code, r.i): r.dist_main for r in E.itertuples()}
tot_tr, rem = [], []
for p in PREP:
    rules = BaselineRules(p["df"], p["d"], p["R"], tr)
    res = simulate(p["df"], p["atr"], rules, base, p["mk"], cost)
    for t in res.trades:
        dec = t.i0 - 1
        d_ = dist_lookup.get((p["code"], dec))
        tot_tr.append((p["name"], t.pnl_pct, d_ is not None and d_ <= NEAR_ATR, d_ is None))
T = pd.DataFrame(tot_tr, columns=["name", "pnl", "near", "nolookup"])
print(f"   전체 거래 {len(T)}건 · 진입일 매물대 정보 없음(표본 밖 구간) {int(T['nolookup'].sum())}건")
for lab, g in (("제거됨(가까움)", T[T["near"]]), ("유지됨", T[~T["near"]])):
    if len(g):
        print(f"   {lab:<14} {len(g):>3}건 · 승 {int((g['pnl'] > 0).sum())} 패 {int((g['pnl'] <= 0).sum())} · 평균 {g['pnl'].mean():+.2%} · 단순 합 {g['pnl'].sum():+.1%}")

print("\n" + "=" * 8 + " 5. 지금(마지막 완성 봉) 관심종목의 위 저항 거리 " + "=" * 8)
for p in PREP:
    pr = vp.compute(p["df"])
    st = "계산 불가" if not pr["ok"] else ("영역 안" if pr["inside"] else (f"{pr['up']['enter_atr']:.1f} ATR" if pr["up"] and pr["up"]["enter_atr"] is not None else "위쪽 두꺼운 영역 없음"))
    print(f"   {p['name']:<10} {st}")


print("\n" + "=" * 8 + f" 6. 독립 표본 (사건일 ≥ {OOS_START}) " + "=" * 8)
drift = spec_drift()
spec_sha = sha(SPEC_PATH.read_bytes())
if drift:
    print(f"※ 고정 사양과 달라서 이 절은 독립 검증이 아니다(탐색 결과로만 읽을 것). 사양 파일 {spec_sha}:")
    for d_ in drift:
        print("   -", d_)
else:
    print(f"고정 사양 일치(사양 파일 {spec_sha}): 종목·시장 9개, 계산법 v1, 임계값·비용, 분석 스크립트와 핵심 코드 해시가 시작 시점과 같다.")
print(f"사전 고정: 주 모집단={SPEC['primary_population']} · 주 비교={SPEC['primary_contrast']} · 보조 모집단={SPEC['secondary_population']} · '위 영역 없음'={SPEC['no_zone_role']} · 각 비교 그룹 최소 {MIN_GROUP}건 · 주 결과={SPEC['primary']}")
O = E_ALL[E_ALL["oos"]].reset_index(drop=True)
POP = {"baseline_entry": O[O["base"]].reset_index(drop=True), "structural_entry": O}
print(f"탐색 구간 사건 {len(E)}건(현행 매수 조건 {int(E['base'].sum())}건) · 독립 표본 사건 {len(O)}건(현행 매수 조건 {len(POP['baseline_entry'])}건) · 마지막 사건일 {O['date'].max() if len(O) else '아직 없음'}")


def klass(S):
    return np.where(S["dist_main"] <= NEAR_ATR, "가까움", np.where(np.isfinite(S["dist_main"]), "영역 있으나 멂", "위 영역 없음"))


for role, key in (("주(현행 매수 조건 통과일)", SPEC["primary_population"]), ("보조(구조 조건 통과일)", SPEC["secondary_population"])):
    S = POP[key]; k_ = klass(S) if len(S) else np.array([])
    print(f"\n■ {role}: 사건 {len(S)}건")
    for nm_ in SPEC["classes"]:                              # 세 분류는 기술 통계. '위 영역 없음'은 비교군이 아니다
        g = S[k_ == nm_] if len(S) else S
        tag = " (기술 통계만)" if nm_ == "위 영역 없음" else ""
        print(f"   {nm_:<14} {len(g):>4}건{tag}" + (f" · MFE10 {g['mfe10'].mean():+.2f} · 목표 선도달 {g['win'].mean():.1%} · 손절 선도달 {g['lose'].mean():.1%}" if len(g) else ""))
    sub = S[k_ != "위 영역 없음"].reset_index(drop=True) if len(S) else S
    n_near, n_far = int((sub["dist_main"] <= NEAR_ATR).sum()) if len(sub) else 0, int((sub["dist_main"] > NEAR_ATR).sum()) if len(sub) else 0
    if n_near >= MIN_GROUP and n_far >= MIN_GROUP:
        print(f"   주 비교(가까움 vs 영역 있으나 멂), 주 결과 = 10일 MFE·목표 선도달:")
        report(sub, f"{role} 독립 표본")
    else:
        print(f"   주 비교(가까움 vs 영역 있으나 멂): 표본 부족 — 가까움 {n_near}건 / 영역 있으나 멂 {n_far}건 (각 {MIN_GROUP}건 이상 필요). 결론을 내지 않는다.")
print("\n탐색 구간에서 '영역 있으나 멂'이 현행 매수 조건 통과일 96건 중 0건, 구조 조건 통과일 1185건 중 54건이었다 → 주 비교는 표본이 쌓이기까지 오래 걸리고, 보조 모집단이 먼저 채워질 가능성이 크다.")
print("해석 원칙: 독립 표본에서도 같은 방향·크기가 재현될 때만 '재현'이라고 부른다. 표본이 작으면 '표본 부족'으로 둔다. 3C(하드 게이트)·점수 가감은 별도 승인 전에는 하지 않는다.")
