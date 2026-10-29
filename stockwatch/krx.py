"""KRX 정보데이터시스템(data.krx.co.kr) 로그인 + 개별종목 투자자별 순매수 수집. 계정은 .env 의 ID/PW."""
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

BASE = "https://data.krx.co.kr"
LOGIN_PAGE = f"{BASE}/contents/MDC/COMS/client/MDCCOMS001.cmd"
LOGIN_URL = f"{BASE}/contents/MDC/COMS/client/MDCCOMS001D1.cmd"
JSON_URL = f"{BASE}/comm/bldAttendant/getJsonData.cmd"
REFERER = f"{BASE}/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201"
ROOT = Path(__file__).resolve().parent.parent


from .envfile import env as _env  # noqa: E402


class KrxClient:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": "stockwatch/0.1 (personal use)", "Referer": REFERER})

    def login(self) -> str:
        self.s.get(LOGIN_PAGE, timeout=15)
        payload = {"mbrNm": "", "telNo": "", "di": "", "certType": "", "mbrId": _env("ID"), "pw": _env("PW")}
        r = self.s.post(LOGIN_URL, data=payload, timeout=15)
        code = r.json().get("_error_code")
        if code == "CD011":  # 다른 곳 접속 중 → 기존 세션 종료 후 재로그인
            r = self.s.post(LOGIN_URL, data={**payload, "skipDup": "Y"}, timeout=15)
            code = r.json().get("_error_code")
        if code != "CD001":
            raise RuntimeError(f"KRX 로그인 실패 (error_code={code})")
        return code

    def post(self, bld: str, **params) -> dict:
        r = self.s.post(JSON_URL, data={"bld": bld, **params}, timeout=20)
        r.raise_for_status()
        return r.json()


# KRX 개별종목 투자자별 일별 순매수(거래량) 컬럼 순서: 기관합계, 기타법인, 개인, 외국인합계 (pykrx 컬럼 순서 기준)
_FLOW_COLS = {"TRDVAL1": "institution", "TRDVAL2": "other_corp", "TRDVAL3": "individual", "TRDVAL4": "foreign"}


def fetch_flows(c: KrxClient, code: str, days: int = 130) -> pd.DataFrame:
    """개별종목 투자자별 일별 순매수 수량 (음수=순매도)."""
    r = c.post("dbms/comm/finder/finder_stkisu", locale="ko_KR", mktsel="ALL", typeNo="0", searchText=code)
    isin = next(x["full_code"] for x in r["block1"] if x["short_code"] == code)
    end = date.today()
    r = c.post("dbms/MDC/STAT/standard/MDCSTAT02302", locale="ko_KR", inqTpCd="1", trdVolVal="1", askBid="3",
               strtDd=(end - timedelta(days=days)).strftime("%Y%m%d"), endDd=end.strftime("%Y%m%d"),
               isuCd=isin, share="1", money="1", csvxls_isNo="false")
    df = pd.DataFrame(r.get("output", []))
    if df.empty:
        raise RuntimeError(f"{code}: 수급 데이터 없음")
    out = pd.DataFrame({"date": pd.to_datetime(df["TRD_DD"], format="%Y/%m/%d")})
    for src, dst in _FLOW_COLS.items():
        out[dst] = pd.to_numeric(df[src].str.replace(",", ""), errors="coerce")
    return out.sort_values("date")


_INVESTORS = {"금융투자": "fin_invest", "보험": "insurance", "투신": "trust", "사모": "private_fund", "은행": "bank",
              "기타금융": "other_fin", "연기금 등": "pension", "기관합계": "institution", "기타법인": "other_corp",
              "개인": "individual", "외국인": "foreign_reg", "기타외국인": "foreign_etc"}


def _num(s) -> float:
    return float(str(s).replace(",", "") or 0)


def fetch_flow_detail(c: KrxClient, code: str, dates: list, isin: str | None = None) -> pd.DataFrame:
    """일자별 투자자 세부 구분 순매수 (수량 _vol, 거래대금 원 _val). 하루당 1회 호출."""
    if isin is None:
        r = c.post("dbms/comm/finder/finder_stkisu", locale="ko_KR", mktsel="ALL", typeNo="0", searchText=code)
        isin = next(x["full_code"] for x in r["block1"] if x["short_code"] == code)
    rows = []
    for d in dates:
        day = pd.Timestamp(d).strftime("%Y%m%d")
        r = c.post("dbms/MDC/STAT/standard/MDCSTAT02301", locale="ko_KR", inqTpCd="2", trdVolVal="1", askBid="3",
                   strtDd=day, endDd=day, isuCd=isin, share="1", money="1", csvxls_isNo="false")
        rec = {"date": pd.Timestamp(d)}
        for x in r.get("output", []):
            k = _INVESTORS.get(x["INVST_TP_NM"])
            if k:
                rec[f"{k}_vol"] = _num(x["NETBID_TRDVOL"]); rec[f"{k}_val"] = _num(x["NETBID_TRDVAL"])
        rows.append(rec)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------
# 추가 수집 (외국인 보유, 시총, 공매도, PER/PBR, 시장수급, 지수). 응답 컬럼은 소문자로 그대로 저장한다.
# ---------------------------------------------------------------------------------------------
_DATE_KEYS = ("TRD_DD", "RPT_DUTY_OCCR_DD")
_LOOKUP: dict = {}


def lookup(c: KrxClient, code: str) -> tuple[str, str]:
    """종목코드 -> (ISIN, 시장 STK/KSQ)."""
    if code not in _LOOKUP:
        r = c.post("dbms/comm/finder/finder_stkisu", locale="ko_KR", mktsel="ALL", typeNo="0", searchText=code)
        x = next(x for x in r["block1"] if x["short_code"] == code)
        _LOOKUP[code] = (x["full_code"], x["marketCode"])
    return _LOOKUP[code]


def _frame(rows: list) -> pd.DataFrame:
    """KRX 응답 행 -> 날짜 + 숫자 컬럼(소문자) DataFrame, 오름차순."""
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    dk = next((k for k in _DATE_KEYS if k in df), None)
    out = pd.DataFrame({"date": pd.to_datetime(df[dk], format="%Y/%m/%d")}) if dk else pd.DataFrame(index=df.index)
    for k in df.columns:
        if k == dk or k.endswith("_CD") or k in ("ISU_ABBRV_STR",):
            continue
        num = pd.to_numeric(df[k].astype(str).str.replace(",", ""), errors="coerce")
        if num.notna().any():
            out[k.lower()] = num
    return out.sort_values("date").reset_index(drop=True) if dk else out


def _stock_range(c, bld: str, code: str, days: int, **extra) -> pd.DataFrame:
    isin, _ = lookup(c, code)
    end = date.today()
    r = c.post(bld, locale="ko_KR", isuCd=isin, strtDd=(end - timedelta(days=days)).strftime("%Y%m%d"),
               endDd=end.strftime("%Y%m%d"), share="1", money="1", csvxls_isNo="false", **extra)
    df = _frame(next((v for v in r.values() if isinstance(v, list)), []))
    if df.empty:
        raise RuntimeError(f"{code}: {bld.rsplit('/', 1)[-1]} 데이터 없음")
    return df


def fetch_foreign_ownership(c, code, days=130):   # 외국인 보유수량/보유율/한도소진율
    return _stock_range(c, "dbms/MDC/STAT/standard/MDCSTAT03702", code, days, searchType="2")


def fetch_market_cap(c, code, days=130):          # 시가총액, 상장주식수, 거래대금
    return _stock_range(c, "dbms/MDC/STAT/standard/MDCSTAT01701", code, days, adjStkPrc_check="N", adjStkPrc="1")


def fetch_short_trading(c, code, days=130):       # 일별 공매도 거래량/대금/비중
    return _stock_range(c, "dbms/MDC/STAT/srt/MDCSTAT30102", code, days)


def fetch_short_balance(c, code, days=130):       # 공매도 잔고 수량/금액/비율 (잔고는 공시 기준이라 며칠 지연)
    return _stock_range(c, "dbms/MDC/STAT/srt/MDCSTAT30502", code, days)


def fetch_fundamentals(c, codes: list[str], day: date | None = None) -> pd.DataFrame:
    """전종목 PER/PBR/EPS/BPS/DPS/배당수익률 스냅샷(시장별 1회) 중 관심 종목만. 휴장일이면 직전 영업일로 되돌아간다."""
    day = day or date.today()
    for back in range(7):
        d = day - timedelta(days=back)
        rows = []
        for mkt in ("STK", "KSQ"):
            r = c.post("dbms/MDC/STAT/standard/MDCSTAT03501", locale="ko_KR", searchType="1", mktId=mkt,
                       trdDd=d.strftime("%Y%m%d"), csvxls_isNo="false")
            rows += next((v for v in r.values() if isinstance(v, list)), [])
        sel = [x for x in rows if x["ISU_SRT_CD"] in codes]
        if sel:
            df = _frame([{k: v for k, v in x.items() if k in ("ISU_SRT_CD", "EPS", "PER", "BPS", "PBR", "DPS", "DVD_YLD")}
                         for x in sel])
            df.insert(0, "code", [x["ISU_SRT_CD"] for x in sel]); df.insert(1, "date", pd.Timestamp(d))
            return df
    raise RuntimeError("PER/PBR 스냅샷 없음")


def fetch_market_flow(c, market: str, dates: list) -> pd.DataFrame:
    """시장 전체(STK/KSQ) 투자자별 일별 순매수 거래대금(원)."""
    rows = []
    for d in dates:
        day = pd.Timestamp(d).strftime("%Y%m%d")
        r = c.post("dbms/MDC/STAT/standard/MDCSTAT02201", locale="ko_KR", inqTpCd="1", trdVolVal="2", askBid="3",
                   mktId=market, strtDd=day, endDd=day, money="1", csvxls_isNo="false")
        rec = {"date": pd.Timestamp(d)}
        for x in r.get("output", []):
            k = _INVESTORS.get(x["INVST_TP_NM"])
            if k:
                rec[f"{k}_val"] = _num(x["NETBID_TRDVAL"])
        rows.append(rec)
    return pd.DataFrame(rows)


def fetch_index(c, idx: str, idx2: str, days=200) -> pd.DataFrame:
    """지수 일봉. KOSPI=(1,001), KOSDAQ=(2,001)."""
    end = date.today()
    r = c.post("dbms/MDC/STAT/standard/MDCSTAT00301", locale="ko_KR", indIdx=idx, indIdx2=idx2,
               strtDd=(end - timedelta(days=days)).strftime("%Y%m%d"), endDd=end.strftime("%Y%m%d"),
               money="3", csvxls_isNo="false")
    df = _frame(r.get("output", []))
    if df.empty:
        raise RuntimeError(f"지수 {idx}/{idx2} 데이터 없음")
    return df
