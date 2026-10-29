"""종목 상세 차트용 시계열: 일 단위 수급, 외국인 보유율, 공매도 비중/잔고."""
import pandas as pd


def _rows(df: pd.DataFrame | None, cols: dict, days: int) -> list[dict]:
    """df(날짜 인덱스)에서 cols{출력키: 원본컬럼} 만 뽑아 최근 days 개를 JSON 용 행으로. NaN → None."""
    if df is None or df.empty:
        return []
    out = []
    for dt, r in df.tail(days).iterrows():
        row = {"date": dt.strftime("%Y-%m-%d")}
        for k, src in cols.items():
            v = r.get(src)
            row[k] = None if v is None or pd.isna(v) else float(v)
        out.append(row)
    return out


def build(prov, code: str, days: int = 60) -> dict:
    short = prov.table("short_trading", code)
    if short is not None and "cvsrtsell_trdvol" in short:
        # 당일 공매도는 익일 공개라 말미의 0 은 '미공개'로 보고 제외 (실제 0 과 혼동 방지)
        short = short.copy()
        while len(short) > 1 and short["cvsrtsell_trdvol"].iloc[-1] == 0:
            short = short.iloc[:-1]
    return {
        "flows": _rows(prov.flows(code), {"individual": "individual", "institution": "institution", "foreign": "foreign", "other_corp": "other_corp"}, days),
        "foreign_own": _rows(prov.table("foreign_own", code), {"ratio": "forn_shr_rt", "limit_used": "forn_lmt_exhst_rt"}, days),
        "short": _rows(short, {"ratio": "trdval_wt", "value": "cvsrtsell_trdval"}, days),
        "short_balance": _rows(prov.table("short_balance", code), {"ratio": "bal_rto", "amount": "bal_amt"}, days),
    }
