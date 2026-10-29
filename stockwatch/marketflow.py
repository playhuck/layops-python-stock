"""시장 전체(코스피/코스닥) 일 단위 투자자별 순매수대금 + 지수 종가/등락률 (화면용 변환)."""
import pandas as pd

MARKETS = (("STK", "KOSPI", "코스피"), ("KSQ", "KOSDAQ", "코스닥"))
# 기관 세부 구분 (KRX 투자자 구분 순서)
INSTITUTION_PARTS = (("fin_invest", "금융투자"), ("insurance", "보험"), ("trust", "투신"),
                     ("private_fund", "사모"), ("bank", "은행"), ("other_fin", "기타금융"), ("pension", "연기금"))


def _eok(r: pd.Series, key: str):
    v = r.get(f"{key}_val")
    return None if v is None or pd.isna(v) else round(float(v) / 1e8, 1)


def daily(prov, days: int = 20) -> dict:
    """{"markets":[{id,label,rows:[{date,index_close,index_rate,individual,institution,foreign,other_corp,parts:{...}}]}]}  금액 단위: 억원, 날짜 오름차순."""
    out = []
    for mkt, idx_name, label in MARKETS:
        mf = prov.table("market_flow", mkt)
        if mf is None or mf.empty:
            continue
        ix = prov.table("index", idx_name)
        rows = []
        for dt, r in mf.tail(days).iterrows():
            foreign = [x for x in (_eok(r, "foreign_reg"), _eok(r, "foreign_etc")) if x is not None]
            row = {"date": dt.strftime("%Y-%m-%d"), "individual": _eok(r, "individual"), "institution": _eok(r, "institution"),
                   "foreign": round(sum(foreign), 1) if foreign else None, "other_corp": _eok(r, "other_corp"),
                   "parts": {lbl: _eok(r, k) for k, lbl in INSTITUTION_PARTS}, "index_close": None, "index_rate": None}
            if ix is not None and dt in ix.index:
                row["index_close"] = float(ix.at[dt, "clsprc_idx"])
                if "updn_rate" in ix:
                    row["index_rate"] = float(ix.at[dt, "updn_rate"])
            rows.append(row)
        out.append({"id": mkt, "label": label, "rows": rows})
    return {"markets": out}
