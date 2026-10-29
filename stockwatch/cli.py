"""사용법: python -m stockwatch.cli [종목코드 ...]   (생략 시 config.yaml 의 watchlist 전체)"""
import argparse
from pathlib import Path

import pandas as pd
import yaml

from . import config, sectors
from .providers import DbProvider
from .signals import analyze, Result

ROOT = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    return config.load()


DETAIL_LABELS = {"fin_invest": "금융투자", "insurance": "보험", "trust": "투신", "private_fund": "사모",
                 "bank": "은행", "other_fin": "기타금융", "pension": "연기금", "institution": "기관합계",
                 "other_corp": "기타법인", "individual": "개인"}


def summarize_detail(df) -> dict | None:
    """최근 7일 투자자별 순매수 거래대금 합계(억원)."""
    if df is None or df.empty:
        return None
    out = {lbl: round(df[f"{k}_val"].sum() / 1e8, 1) for k, lbl in DETAIL_LABELS.items() if f"{k}_val" in df}
    if "foreign_reg_val" in df:
        out["외국인"] = round((df["foreign_reg_val"] + df.get("foreign_etc_val", 0)).sum() / 1e8, 1)
    return out


def summarize_extra(prov, code: str, price_df) -> list[str]:
    """보조 지표 요약 문자열 (점수에는 반영하지 않음)."""
    out = []
    fo = prov.table("foreign_own", code)
    if fo is not None and len(fo) > 7:
        out.append(f"외국인 보유율 {fo['forn_shr_rt'].iloc[-1]:.2f}% (7일 {fo['forn_shr_rt'].iloc[-1] - fo['forn_shr_rt'].iloc[-8]:+.2f}%p)")
    mc = prov.table("market_cap", code)
    if mc is not None and len(mc):
        out.append(f"시가총액 {mc['mktcap'].iloc[-1] / 1e12:,.1f}조원 / 7일 평균 거래대금 {mc['acc_trdval'].tail(7).mean() / 1e8:,.0f}억원")
    fu = prov.table("fundamental", code)
    if fu is not None and len(fu):
        # 장중에는 당일 PER/PBR 이 비어 있을 수 있어, 값이 있는 가장 최근 날짜를 쓴다
        f = lambda v, spec: "-" if v is None or pd.isna(v) else format(v, spec)
        valid = fu.dropna(subset=["per", "pbr"], how="all")
        r = (valid if len(valid) else fu).iloc[-1]
        out.append(f"PER {f(r.get('per'), '.1f')} / PBR {f(r.get('pbr'), '.2f')} / EPS {f(r.get('eps'), ',.0f')} / 배당수익률 {f(r.get('dvd_yld'), '.2f')}%")
    st = prov.table("short_trading", code)
    if st is not None and len(st):
        st = st.copy()
        while len(st) > 1 and st["cvsrtsell_trdvol"].iloc[-1] == 0:  # 당일 공매도는 익일 공개라 말미 0 은 미공개로 본다
            st = st.iloc[:-1]
        out.append(f"공매도 비중(7일 평균) {st['trdval_wt'].tail(7).mean():.2f}% (기준일 {st.index[-1].date()})")
    sb = prov.table("short_balance", code)
    if sb is not None and len(sb):
        out.append(f"공매도 잔고 {sb['bal_rto'].iloc[-1]:.2f}% / {sb['bal_amt'].iloc[-1] / 1e8:,.0f}억원 (공시일 {sb.index[-1].date()})")
    idx = prov.table("index", "KOSPI")
    if idx is not None and len(idx) > 20 and len(price_df) > 20:
        stock20 = price_df["close"].iloc[-1] / price_df["close"].iloc[-21] - 1
        idx20 = idx["clsprc_idx"].iloc[-1] / idx["clsprc_idx"].iloc[-21] - 1
        out.append(f"20일 수익률 {stock20 * 100:+.1f}% (KOSPI {idx20 * 100:+.1f}%, 상대 {100 * (stock20 - idx20):+.1f}%p)")
    return out


def summarize_market(prov) -> dict:
    """시장 전체 최근 7일 투자자별 순매수대금(억원)."""
    res = {}
    for m, label in (("STK", "코스피"), ("KSQ", "코스닥")):
        df = prov.table("market_flow", m)
        if df is not None and len(df):
            res[label] = {lbl: round(df[f"{k}_val"].sum() / 1e8) for k, lbl in
                          (("individual", "개인"), ("institution", "기관"), ("foreign_reg", "외국인")) if f"{k}_val" in df}
    return res


def run(codes: list[str] | None = None) -> list[Result]:
    cfg = load_config()
    prov = DbProvider()
    vix = prov.vix()
    cls = sectors.classify([i["code"] for i in cfg["watchlist"]])
    results = []
    for item in cfg["watchlist"]:
        if codes and item["code"] not in codes:
            continue
        try:
            r = analyze(item["code"], item["name"], prov.prices(item["code"]),
                        prov.flows(item["code"]), vix, cfg.get("thresholds"))
            r.detail["flow_detail7d"] = summarize_detail(prov.flow_detail(item["code"]))
            px = prov.prices(item["code"])
            r.detail["extra"] = summarize_extra(prov, item["code"], px)
            c = cls.get(item["code"], {})
            r.detail["sector"] = {"label": c.get("label") or item.get("group") or "", "primary": c.get("primary") or item.get("group") or "", "tags": c.get("tags", [])}
            r.detail["spark"] = [round(float(x), 1) for x in px["close"].tail(30)]  # 홈 미니 차트
            results.append(r)
        except (FileNotFoundError, ValueError) as e:
            print(f"[건너뜀] {item['code']} {item['name']}: {e}")
    return results


def render(r: Result) -> str:
    out = [f"■ {r.name}({r.code})  {r.price:,.0f}원  → {r.verdict}   [매수 {r.buy_score} / 매도 {r.sell_score}]"]
    out += [f"   + {x}" for x in r.reasons]
    out += [f"   ! {x}" for x in r.warnings]
    f = r.detail["flow7d"]
    if f:
        out.append("   7일 수급: " + " / ".join(f"{k} {v:+,}" for k, v in f.items()))
    fd = r.detail.get("flow_detail7d")
    if fd:
        out.append("   7일 순매수대금(억원): " + " / ".join(f"{k} {v:+,.0f}" for k, v in fd.items()))
    out += [f"   · {x}" for x in r.detail.get("extra", [])]
    v = r.detail["volume7d"]
    if v["ratio"]:
        out.append(f"   7일 평균 거래량: 직전 20일 평균의 {v['ratio']:.2f}배")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codes", nargs="*")
    args = ap.parse_args()
    mk = summarize_market(DbProvider())
    for label, v in mk.items():
        print(f"[시장 7일 순매수 {label}, 억원] " + " / ".join(f"{k} {x:+,}" for k, x in v.items()))
    print()
    for r in run(args.codes):
        print(render(r), end="\n\n")
    print("※ 참고용 분석이며 투자 판단과 손실 책임은 본인에게 있습니다.")


if __name__ == "__main__":
    main()
